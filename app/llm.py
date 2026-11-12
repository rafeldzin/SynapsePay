"""The only module that talks to a language model (PRD 4.3, 7.2). Provider: Google Gemini.

Both entry points raise LLMError on any provider error, timeout, invalid JSON,
output that does not match the schema, or step cap. Callers turn that into an
abstention (INV-10), never into a pass or a write.

LLM_MODE=fake answers from tests/fakes.py:FakeLLM. The fake returns the same raw
text the provider would, so parsing, validation, error mapping and the step cap
below run identically in both modes.

The daily call cap (LLM_DAILY_CALL_CAP) is not built: PRD 14.1 cut 1 is active.
"""
import json
import os
import re
import time
from typing import Any, Callable

TEMPERATURE = 0  # PRD 4.1

# Gemini answers 429/5xx when a model is overloaded. A model call has no side
# effects, so it is retried a little, always inside the caller's time budget.
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
RETRY_DELAYS_S = (1.0, 2.0)  # before attempts 2 and 3
# Gemini rejects a request deadline under 10 s with 400 INVALID_ARGUMENT, so every
# call gets at least this much, and a retry starts only if a full one still fits.
MIN_DEADLINE_S = 10.0
MIN_ATTEMPT_S = MIN_DEADLINE_S


class LLMError(Exception):
    """Provider error, timeout, invalid output or step cap. Callers abstain."""


class LLMStepCapError(LLMError):
    """tool_loop reached max_steps tool calls without a final answer."""


# ---- backend selection ---------------------------------------------------

_fake: Any = None


def use_fake(fake: Any) -> None:
    """Installs the FakeLLM used when LLM_MODE=fake. None restores the default."""
    global _fake
    _fake = fake


def _settings() -> tuple[str, str, str]:
    mode = os.environ.get("LLM_MODE", "live").strip().lower()
    if mode not in ("live", "fake"):
        raise LLMError(f"LLM_MODE must be live or fake, got {mode!r}")
    return mode, os.environ.get("LLM_API_KEY", "").strip(), os.environ.get("LLM_MODEL", "").strip()


def _backend(deadline: float) -> tuple[Any, str]:
    """Returns (backend, api_key). The key is returned only so errors can redact it."""
    mode, key, model = _settings()
    if mode == "fake":
        global _fake
        if _fake is None:
            from tests.fakes import FakeLLM
            _fake = FakeLLM()
        return _fake, ""
    if not key or not model:
        raise LLMError("LLM_API_KEY and LLM_MODEL must be set when LLM_MODE=live")
    return _Gemini(key, model, deadline), key


def _as_llm_error(error: BaseException, key: str) -> LLMError:
    if isinstance(error, LLMError):
        return error
    detail = " ".join(str(part) for part in (
        type(error).__name__, getattr(error, "code", ""), getattr(error, "status", ""),
        str(error)[:300]) if part)
    if key:
        detail = detail.replace(key, "[redacted]")
    return LLMError(detail)


# ---- output parsing and validation --------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


def _validate(value: Any, schema: dict, path: str = "$") -> None:
    """The JSON Schema subset our schemas use: type, enum, properties, required, items."""
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_TYPES[t](value) for t in types):
            raise LLMError(f"{path}: expected {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise LLMError(f"{path}: value outside the allowed list")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise LLMError(f"{path}: missing {name}")
        for name, sub in schema.get("properties", {}).items():
            if name in value:
                _validate(value[name], sub, f"{path}.{name}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]")


def _parse(text: Any, schema: dict) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise LLMError("the model returned no text")
    text = text.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise LLMError(f"invalid JSON from the model: {error.msg}") from None
    if not isinstance(value, dict):
        raise LLMError("the model's JSON is not an object")
    _validate(value, schema)
    return value


# ---- public API (PRD 7.2) ------------------------------------------------

def complete_json(*, system: str, user: str, schema: dict, timeout_s: float = 30) -> dict:
    """One model call whose answer must be a JSON object matching `schema`."""
    key = ""
    try:
        backend, key = _backend(time.monotonic() + timeout_s)
        text = backend.complete_json(system=system, user=user, schema=schema)
    except Exception as error:  # every provider failure becomes an abstention
        raise _as_llm_error(error, key) from None
    return _parse(text, schema)


def tool_loop(*, system: str, user: str, tools: list[dict], call_tool: Callable[[str, dict], dict],
              final_schema: dict, max_steps: int, timeout_s: float = 45) -> dict:
    """Runs tool calls until the model returns final JSON or max_steps is hit.

    tools: [{"name", "description", "parameters": <JSON schema>}].
    call_tool(name, args) -> dict is supplied by the caller and runs only for a
    name in `tools`. A name outside the list is answered with an error result
    and never reaches call_tool. Every requested call counts as a step.
    Raises LLMError on provider error, invalid JSON, timeout, and
    LLMStepCapError (an LLMError) on the step cap.
    """
    deadline = time.monotonic() + timeout_s
    allowed = {tool["name"] for tool in tools}
    system = (f"{system}\n\nWhen you have enough evidence, stop calling tools and reply with "
              f"only a JSON object matching this schema:\n{json.dumps(final_schema)}")
    key = ""
    try:
        backend, key = _backend(deadline)
        session = backend.tool_session(system=system, user=user, tools=tools)
    except Exception as error:
        raise _as_llm_error(error, key) from None

    steps = 0
    while True:
        if time.monotonic() > deadline:
            raise LLMError(f"tool loop exceeded {timeout_s} s")
        try:
            kind, payload = session.next()
        except Exception as error:
            raise _as_llm_error(error, key) from None
        if kind == "final":
            return _parse(payload, final_schema)

        results = []
        for name, args in payload:
            if steps >= max_steps:
                raise LLMStepCapError(f"step cap of {max_steps} tool calls reached")
            steps += 1
            if name in allowed:
                response = call_tool(name, dict(args or {}))
            else:
                response = {"error": f"unknown tool {name!r}; use only the listed tools"}
            results.append({"name": name, "response": response})
        try:
            session.respond(results)
        except Exception as error:
            raise _as_llm_error(error, key) from None


# ---- Gemini backend ------------------------------------------------------

class _Gemini:
    def __init__(self, key: str, model: str, deadline: float):
        from google import genai
        from google.genai import types

        self.types = types
        self.model = model
        self.deadline = deadline
        # The SDK must not retry on its own: the caller's time budget is fixed.
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1)))

    def _http(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("time budget used up before the model call")
        return self.types.HttpOptions(timeout=int(max(MIN_DEADLINE_S, remaining) * 1000),
                                      retry_options=self.types.HttpRetryOptions(attempts=1))

    def generate(self, contents, **config):
        """One generate_content call, retried on transient statuses within the budget."""
        attempt = 1
        while True:
            try:
                return self.client.models.generate_content(
                    model=self.model, contents=contents,
                    config=self.types.GenerateContentConfig(**config, http_options=self._http()))
            except Exception as error:
                if getattr(error, "code", None) not in TRANSIENT_STATUS or attempt >= MAX_ATTEMPTS:
                    raise
                delay = RETRY_DELAYS_S[attempt - 1]
                if self.deadline - time.monotonic() < delay + MIN_ATTEMPT_S:
                    raise
                time.sleep(delay)
                attempt += 1

    def complete_json(self, *, system: str, user: str, schema: dict) -> str | None:
        response = self.generate(
            user, system_instruction=system, temperature=TEMPERATURE,
            response_mime_type="application/json", response_json_schema=schema)
        return response.text

    def tool_session(self, *, system: str, user: str, tools: list[dict]) -> "_GeminiToolSession":
        return _GeminiToolSession(self, system, user, tools)


class _GeminiToolSession:
    def __init__(self, gemini: _Gemini, system: str, user: str, tools: list[dict]):
        types = gemini.types
        self.gemini = gemini
        self.contents = [types.Content(role="user", parts=[types.Part(text=user)])]
        self.system = system
        self.tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=tool["name"], description=tool.get("description", ""),
                                      parameters_json_schema=tool["parameters"])
            for tool in tools])]

    def next(self):
        types = self.gemini.types
        response = self.gemini.generate(
            self.contents, system_instruction=self.system, temperature=TEMPERATURE,
            tools=self.tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        if not response.candidates or response.candidates[0].content is None:
            raise LLMError("the model returned no candidate")
        # Keep the model turn as returned, so Gemini's thought signatures go back with it.
        self.contents.append(response.candidates[0].content)
        calls = response.function_calls or []
        if calls:
            return ("calls", [(call.name, dict(call.args or {})) for call in calls])
        return ("final", response.text)

    def respond(self, results: list[dict]) -> None:
        types = self.gemini.types
        self.contents.append(types.Content(role="user", parts=[
            types.Part.from_function_response(name=result["name"], response=result["response"])
            for result in results]))
