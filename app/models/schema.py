from enum import Enum
from typing import List
from pydantic import BaseModel, Field


class VerdictEnum(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ABSTAIN = "ABSTAIN"


class Milestone(BaseModel):
    id: str
    title: str
    amount: float
    acceptance: str


class Contract(BaseModel):
    id: str
    total: float
    currency: str = "USD"
    milestones: List[Milestone] = Field(default_factory=list)


class Deliverable(BaseModel):
    milestone_id: str
    content: str


class Verdict(BaseModel):
    milestone_id: str
    verdict: VerdictEnum
    score: float = Field(..., ge=0.0, le=1.0, description="Verification confidence or completion score between 0.0 and 1.0")
    reasons: List[str] = Field(default_factory=list)
    injection_flag: bool = False
