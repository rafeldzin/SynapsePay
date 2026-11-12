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
# Investigator scripts of UAT 1.4. "turns" is a test-only script that plays back
# a fixed list of tool-loop turns.
TOOL_SCRIPTS = ("honest", "infinite_tools", "action_outside_list", "no_citations",
                "cancel_when_success", "obeys_message", "invalid_json", "timeout", "turns")
# Options for the honest investigator, to bend one field of its answer:
# force_action, cite_only_local, extra_evidence_ids, diversion_risk,
# required_verification, do_not_resend.


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
        # Every other script reads each tool once, then answers from what it saw.
        if self.turn == 1:
            ids = {key: re.search(rf"^{key}: (\S+)$", self.user, flags=re.MULTILINE).group(1)
                   for key in ("case_id", "payout_id", "milestone_id")}
            args = {"paypal_get_payout_item": {"payout_id": ids["payout_id"]},
                    "paypal_get_payout_batch": {"payout_id": ids["payout_id"]},
                    "ledger_get_milestone_payouts": {"milestone_id": ids["milestone_id"]},
                    "case_get_messages_and_events": {"case_id": ids["case_id"]}}
            return ("calls", [(tool["name"], args[tool["name"]]) for tool in self.tools])
        return ("final", json.dumps(self._investigator_answer()))

    def respond(self, results: list[dict]) -> None:
        self.responses.append(results)

    def _investigator_answer(self) -> dict:
        seen = {r["name"]: r["response"] for batch in self.responses for r in batch}
        item = seen.get("paypal_get_payout_item", {})
        batch = seen.get("paypal_get_payout_batch", {})
        ledger = seen.get("ledger_get_milestone_payouts", {})
        case = seen.get("case_get_messages_and_events", {})
        payout_id = re.search(r"^payout_id: (\S+)$", self.user, flags=re.MULTILINE).group(1)

        item_status = item.get("transaction_status") if item.get("available") else None
        if item_status is None and batch.get("available") and batch.get("items"):
            item_status = batch["items"][0]["transaction_status"]
        batch_status = batch.get("batch_status") if batch.get("available") else None
        payout = next((p for p in ledger.get("payouts", []) if p["id"] == payout_id), {})
        state, batch_known = payout.get("state"), bool(payout.get("payout_batch_id"))

        if item_status == "UNCLAIMED":
            rank1, rejected, action = "MISTYPED_RECIPIENT", "PROCESSING_DELAY", "CANCEL_UNCLAIMED_AND_REISSUE"
        elif state == "UNKNOWN" and item_status == "SUCCESS":
            rank1, rejected, action = ("ORIGINAL_SUCCEEDED_LEDGER_STALE", "PAYOUT_NEVER_CREATED",
                                       "RECONCILE_MARK_PAID")
        elif item_status in ("PENDING", "ONHOLD") or batch_status in ("PENDING", "PROCESSING"):
            rank1, rejected, action = "PROCESSING_DELAY", "MISTYPED_RECIPIENT", "WAIT_AND_RECHECK"
        elif state == "UNKNOWN" and not batch_known:
            rank1, rejected, action = ("PAYOUT_NEVER_CREATED", "ORIGINAL_SUCCEEDED_LEDGER_STALE",
                                       "RETRY_SAME_BATCH_ID")
        else:
            rank1, rejected, action = "OTHER", "PROCESSING_DELAY", "ESCALATE_TO_HUMAN"

        paypal_ids = [r["evidence_id"] for r in (item, batch) if "evidence_id" in r]
        local_ids = [r["evidence_id"] for r in (ledger, case) if "evidence_id" in r]
        options, script = self.fake.options, self.fake.script
        cited = local_ids if options.get("cite_only_local") else paypal_ids + local_ids
        cited = cited + options.get("extra_evidence_ids", [])
        if script == "no_citations":
            cited = []

        answer = {
            "hypotheses": [
                {"code": rank1, "rank": 1, "supported": True, "evidence_ids": list(cited),
                 "rationale": f"PayPal shows the item as {item_status} and the ledger as {state}."},
                {"code": rejected, "rank": 2, "supported": False, "evidence_ids": list(cited),
                 "rationale": "The PayPal status rules this out."},
            ],
            "recommended_action": options.get("force_action", action),
            "do_not_resend": options.get("do_not_resend", True),
            "resend_risk": "Sending again could pay the milestone twice.",
            "required_verification": options.get(
                "required_verification", "Confirm the new email by phone." if
                case.get("diversion", {}).get("diversion_risk") else None),
            "diversion_risk": options.get("diversion_risk",
                                          case.get("diversion", {}).get("diversion_risk", False)),
            "reply_draft": "Thanks for letting us know. We checked the payment with PayPal "
                           "and will follow up with the next step.",
        }
        if script == "action_outside_list":
            answer["recommended_action"] = "RESEND_NOW"
        elif script == "cancel_when_success":
            answer["recommended_action"] = "CANCEL_UNCLAIMED_AND_REISSUE"
        elif script == "obeys_message":
            bodies = " ".join(m["body"] for m in case.get("messages", [])).lower()
            if "resend" in bodies or "send" in bodies:
                answer["recommended_action"] = "RETRY_SAME_BATCH_ID"
                answer["reply_draft"] = "Resending the payment now, as you asked."
            answer["diversion_risk"] = False
        return answer


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
