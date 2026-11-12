"""Shared test fakes (UAT 1.4). FakePayPal lives in app/paypal/fake.py (Dev A).

FakeLLM is selected by LLM_MODE=fake through `app.llm.use_fake`. It answers the
same calls the live backend answers, so llm.py's parsing, schema validation,
error mapping and step cap run for real in Tier 1:

- `complete_json(system=, user=, schema=)` returns the model's raw text.
- `tool_session(system=, user=, tools=)` returns a session whose `next()` yields
  ("calls", [(name, args), ...]) or ("final", raw_text), and `respond(results)`.

Raising TimeoutError or any other exception stands in for a provider failure.
"""
import json
import re

from app.verify import injection

VERIFIER_SCRIPTS = (
    "honest", "all_pass_cited", "all_pass_no_citations", "unknown_fact_ids",
    "missing_criterion", "invalid_json", "timeout",
)
# The investigator scripts of UAT 1.4 arrive with package B3. "turns" is a
# test-only script that plays back a fixed list of tool-loop turns.
TOOL_SCRIPTS = ("infinite_tools", "invalid_json", "timeout", "turns")


def _block(tag: str, text: str) -> str:
    match = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f"<{tag}> block not found in the user turn")
    return match.group(1)


def _number(text: str) -> int:
    return int(text.replace(",", ""))


# Report claims the honest fake knows how to read: (pattern, fact id).
# The first group is the stated number, or absent for "all N duplicates removed".
_CLAIMS = (
    (r"^.*\b(?:Rows delivered|Final row count): ([\d,]+).*$", "row_count"),
    (r"^.*\bRows with an empty email: ([\d,]+).*$", "null_email_count"),
    (r"^.*\bDuplicate emails(?: remaining)?: ([\d,]+).*$", "duplicate_email_count"),
    (r"^.*\ball [\d,]+ duplicates removed.*$", "duplicate_email_count"),
)


class _ToolSession:
    def __init__(self, fake: "FakeLLM", system: str, user: str, tools: list[dict]):
        self.fake, self.system, self.user, self.tools = fake, system, user, tools
        self.responses: list[list[dict]] = []
        self.turn = 0

    def next(self):
        script = self.fake.script
        self.turn += 1
        if script == "timeout":
            raise TimeoutError("fake model timed out")
        if script == "invalid_json":
            return ("final", "Here is my answer: {not json")
        if script == "infinite_tools":
            if self.turn > 100:  # a broken step cap fails the test instead of hanging it
                raise RuntimeError("fake: the tool loop never stopped")
            return ("calls", [(self.tools[0]["name"], {})])
        if script == "turns":
            turns = self.fake.options["turns"]
            return turns[min(self.turn, len(turns)) - 1]
        raise NotImplementedError(f"tool-loop script {script!r} arrives with package B3")

    def respond(self, results: list[dict]) -> None:
        self.responses.append(results)


class FakeLLM:
    def __init__(self):
        self.script = "honest"
        self.options: dict = {}
        self.calls: list[dict] = []

    def use(self, script: str, **options) -> "FakeLLM":
        if script not in VERIFIER_SCRIPTS + TOOL_SCRIPTS:
            raise ValueError(f"unknown FakeLLM script {script!r}")
        self.script, self.options = script, options
        return self

    # ---- complete_json (verifier) -------------------------------------------

    def complete_json(self, *, system: str, user: str, schema: dict) -> str:
        self.calls.append({"kind": "complete_json", "system": system, "user": user,
                           "schema": schema})
        if self.script == "timeout":
            raise TimeoutError("fake model timed out")
        if self.script == "invalid_json":
            return '{"criteria": [ {"criterion_id": "m1.c1", "verdict": '
        return json.dumps(self._verifier_answer(user))

    def _verifier_answer(self, user: str) -> dict:
        criteria = json.loads(_block("criteria", user))
        facts = {fact["id"]: fact["value"] for fact in json.loads(_block("facts", user))}
        report = _block("untrusted_report", user)
        script = self.script

        if script in ("all_pass_cited", "all_pass_no_citations", "unknown_fact_ids"):
            cited = {"all_pass_cited": ["row_count"], "all_pass_no_citations": [],
                     "unknown_fact_ids": ["rows_ok", "email_quality"]}[script]
            return {
                "criteria": [{"criterion_id": c["id"], "verdict": "PASS",
                              "rationale": "Everything looks complete.",
                              "cited_fact_ids": list(cited)} for c in criteria],
                "contradicted_claims": [],
                "injection_suspected": False,
                "note_to_contractor": "All criteria are met.",
            }

        answer = self._honest(criteria, facts, report)
        if script == "missing_criterion":
            answer["criteria"] = answer["criteria"][:-1]
        return answer

    @staticmethod
    def _honest(criteria: list[dict], facts: dict, report: str) -> dict:
        contradicted, compared = [], []
        for pattern, fact_id in _CLAIMS:
            for match in re.finditer(pattern, report, flags=re.MULTILINE | re.IGNORECASE):
                compared.append(fact_id)
                stated = _number(match.group(1)) if match.groups() else 0
                if stated != facts[fact_id]:
                    contradicted.append({"claim": match.group(0).strip(" -"), "fact_id": fact_id})

        verdicts = []
        for criterion in criteria:
            checks = criterion["hard_checks"]
            if checks:
                failed = [c for c in checks if not _holds(facts[c["fact_id"]], c["op"], c["value"])]
                verdicts.append({
                    "criterion_id": criterion["id"],
                    "verdict": "FAIL" if failed else "PASS",
                    "rationale": ("; ".join(f"{c['fact_id']} is {facts[c['fact_id']]}" for c in failed)
                                  or "Every check on the facts holds."),
                    "cited_fact_ids": sorted({c["fact_id"] for c in checks}),
                })
            elif compared:
                verdicts.append({
                    "criterion_id": criterion["id"],
                    "verdict": "FAIL" if contradicted else "PASS",
                    "rationale": ("The report's counts disagree with the facts." if contradicted
                                  else "The report's counts agree with the facts."),
                    "cited_fact_ids": sorted(set(compared)),
                })
            else:
                verdicts.append({"criterion_id": criterion["id"], "verdict": "NEEDS_REVIEW",
                                 "rationale": "The report states no counts.",
                                 "cited_fact_ids": ["row_count"]})

        failing = [v["criterion_id"] for v in verdicts if v["verdict"] != "PASS"]
        return {
            "criteria": verdicts,
            "contradicted_claims": contradicted,
            "injection_suspected": bool(injection.scan(report, b"")),
            "note_to_contractor": (f"Criteria {', '.join(failing)} are not met yet. "
                                   "Please check the counts in your report against the file."
                                   if failing else "Every criterion is met. Thank you."),
        }

    # ---- tool_loop (investigator) -------------------------------------------

    def tool_session(self, *, system: str, user: str, tools: list[dict]) -> _ToolSession:
        self.calls.append({"kind": "tool_loop", "system": system, "user": user, "tools": tools})
        return _ToolSession(self, system, user, tools)


def _holds(fact_value, op: str, value) -> bool:
    if op == "==":
        return fact_value == value
    if op == "<=":
        return fact_value <= value
    return fact_value >= value
