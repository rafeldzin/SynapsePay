from enum import Enum
from typing import List, Optional
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
