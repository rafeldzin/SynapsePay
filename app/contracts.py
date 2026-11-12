"""Shared data contracts (PRD 7 and 7.1). FROZEN.

Copied verbatim from the PRD. Changing a name, field or enum value needs both
developers to agree and the PRD edited in the same commit (PRD 0.3).
tests/test_b0_contracts_fixtures_seed.py compares this file to the PRD.
"""
from typing import Literal
from pydantic import BaseModel

Verdict = Literal["PASS", "FAIL", "NEEDS_REVIEW"]
GateAction = Literal["PAY", "RETRY", "CANCEL", "REISSUE", "RECONCILE"]
ActionCode = Literal[
    "WAIT_AND_RECHECK",
    "CANCEL_UNCLAIMED_AND_REISSUE",
    "RECONCILE_MARK_PAID",
    "RETRY_SAME_BATCH_ID",
    "ESCALATE_TO_HUMAN",
]
HypothesisCode = Literal[
    "MISTYPED_RECIPIENT",
    "PROCESSING_DELAY",
    "ORIGINAL_SUCCEEDED_LEDGER_STALE",
    "PAYOUT_NEVER_CREATED",
    "RECIPIENT_CANNOT_RECEIVE",
    "OTHER",
]
VerificationChannel = Literal["phone", "original_email_thread", "in_person"]

# ---- verification -------------------------------------------------------

class HardCheck(BaseModel):
    fact_id: str
    op: Literal["==", "<=", ">="]
    value: int | str | bool

class Criterion(BaseModel):
    id: str                       # "m1.c1"
    text: str
    hard_checks: list[HardCheck] = []

class Fact(BaseModel):
    id: str                       # see 9.1
    value: int | str | bool | list[str]

class InjectionFinding(BaseModel):
    source: Literal["report", "csv", "model"]
    pattern_id: str               # "INJ-1".."INJ-5" or "MODEL"
    excerpt: str                  # at most 200 chars

class ContradictedClaim(BaseModel):
    claim: str                    # the report's wording, at most 200 chars
    fact_id: str
    fact_value: int | str | bool

class CriterionVerdict(BaseModel):
    criterion_id: str
    verdict: Verdict
    rationale: str
    cited_fact_ids: list[str]
    downgraded_by: Literal["hard_check", "coverage_rule", "llm_error"] | None = None

class VerificationResult(BaseModel):
    milestone_id: str
    deliverable_sha256: str
    facts: list[Fact]
    criteria: list[CriterionVerdict]
    overall: Verdict
    overall_capped_by: Literal["injection_cap"] | None = None
    injection_detected: bool
    injection_findings: list[InjectionFinding]
    contradicted_claims: list[ContradictedClaim]
    note_to_contractor: str
    llm_used: bool
    created_at: str

# ---- gate ---------------------------------------------------------------

class Approval(BaseModel):
    approved_by: str
    confirm: bool

class GateCheck(BaseModel):
    id: str                       # "PAY-1"
    name: str                     # "verdict_ok"
    ok: bool | None               # None = pending human click, preview only
    detail: str

class GateResult(BaseModel):
    action: GateAction
    allowed: bool                 # every check is ok; in a preview the human check is None and ignored
    preview: bool                 # True = human approval not evaluated
    checks: list[GateCheck]
    evaluated_at: str

# ---- investigation ------------------------------------------------------

class Evidence(BaseModel):
    id: str                       # "E1", "E2", ... assigned by the loop
    tool: str
    source: Literal["paypal", "local"]
    summary: str
    data: dict
    fetched_at: str

class Hypothesis(BaseModel):
    code: HypothesisCode
    rank: int                     # 1 = most likely
    supported: bool               # False = considered and rejected
    rationale: str
    evidence_ids: list[str]

class InvestigationReport(BaseModel):
    case_id: str
    payout_id: str
    hypotheses: list[Hypothesis]  # at least 2
    recommended_action: ActionCode
    do_not_resend: bool
    resend_risk: str
    diversion_risk: bool          # set by code, not by the model
    requested_email: str | None   # set by code
    required_verification: str | None
    evidence: list[Evidence]
    reply_draft: str
    steps_used: int
    mode: Literal["tool_loop", "prefetch"]
    downgraded_by: Literal[
        "action_not_in_list", "no_paypal_citation", "status_mismatch", "llm_error", "step_cap"
    ] | None = None
    created_at: str

# ---- PayPal seam (7.1) --------------------------------------------------
# Returned by paypal/reads.py, paypal/writes.py and paypal/fake.py alike.

class CreateResult(BaseModel):
    outcome: Literal["CREATED", "ORIGINAL_FOUND"]   # 2xx, or duplicate rejection with a link
    payout_batch_id: str
    batch_status: str | None
    raw: dict

class ItemView(BaseModel):
    payout_item_id: str
    payout_batch_id: str
    transaction_status: str
    receiver: str
    amount_cents: int
    currency: str
    errors: dict | None
    raw: dict

class BatchView(BaseModel):
    payout_batch_id: str
    sender_batch_id: str
    batch_status: str
    items: list[ItemView]
    raw: dict
