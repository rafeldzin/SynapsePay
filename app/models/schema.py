from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VerdictEnum(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class CriterionVerdict(BaseModel):
    criterion: str
    verdict: VerdictEnum
    evidence: Optional[str] = None


class Milestone(BaseModel):
    id: str
    title: str
    amount: float
    contractor_email: str
    contract_id: Optional[str] = None
    acceptance: Optional[str] = None
    acceptance_json: Optional[str] = None


class Contract(BaseModel):
    id: str
    buyer_name: Optional[str] = None
    contractor_name: Optional[str] = None
    currency: str = "USD"
    milestones: List[Milestone] = Field(default_factory=list)


class Deliverable(BaseModel):
    milestone_id: Optional[str] = None
    content: str


class Verdict(BaseModel):
    milestone_id: str
    verdict: VerdictEnum
    reasons: List[str] = Field(default_factory=list)
    injection_flag: bool = False
    criteria: List[CriterionVerdict] = Field(default_factory=list)


class GateCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class PolicyGateResult(BaseModel):
    allowed: bool
    checks: List[GateCheck] = Field(default_factory=list)
    sender_batch_id: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)


# ==============================================================================
# INBOUND MESSAGES & INVESTIGATOR EVIDENCE MODELS (ADDITIVE)
# ==============================================================================

class InboundMessage(BaseModel):
    id: Optional[int] = None
    milestone_id: str
    sender: Optional[str] = None
    body: str
    received_at: Optional[str] = None


class CaseRecord(BaseModel):
    id: Optional[int] = None
    milestone_id: str
    reason: str
    opened_at: Optional[str] = None
    status: str = "OPEN"


class ActionExecution(BaseModel):
    action: str
    human_approved: bool
    new_email: Optional[str] = None
    new_email_verified: Optional[bool] = None


class CaseEvidenceBundle(BaseModel):
    milestone_id: str
    contract_email: str
    milestone_amount: float
    payouts: List[Dict[str, Any]] = Field(default_factory=list)
    live_paypal_statuses: List[Dict[str, Any]] = Field(default_factory=list)
    events: List[Dict[str, Any]] = Field(default_factory=list)
    inbound_messages: List[Dict[str, Any]] = Field(default_factory=list)
    diversion_risk: bool = False
    requires_out_of_band_verification: bool = False
    extracted_message_emails: List[str] = Field(default_factory=list)
    email_edit_distances: Dict[str, Any] = Field(default_factory=dict)
    cases: List[Dict[str, Any]] = Field(default_factory=list)
    allowed_actions: List[Dict[str, str]] = Field(default_factory=list)
