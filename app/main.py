from collections import defaultdict
from contextlib import asynccontextmanager
import time
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.database import (
    add_inbound_message,
    get_all_cases,
    get_all_milestones,
    get_all_payouts,
    get_contract,
    get_events_for_milestone,
    get_milestone,
    get_payout,
    init_db,
    record_event,
    reseed_demo_data,
)
from app.core.policy_gate import (
    check_and_open_cases,
    execute_cancel_and_reissue,
    execute_case_action,
    execute_payout,
    get_case_evidence_bundle,
    retry_payout,
    sync_payout,
)
from app.models.schema import Deliverable, Milestone, Verdict
from app.verifier.interface import verify


# Simple per-IP in-memory rate limiter (30 req/min)
class IPRateLimiter:
    def __init__(self, limit: int = 30, window: float = 60.0):
        self.limit = limit
        self.window = window
        self.requests = defaultdict(list)

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        self.requests[ip] = [t for t in self.requests[ip] if now - t < self.window]
        if len(self.requests[ip]) >= self.limit:
            return False
        self.requests[ip].append(now)
        return True


rate_limiter = IPRateLimiter(limit=30, window=60.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="SynapsePay API",
    description="Deterministic milestone verifier & direct PayPal Payouts payment rail.",
    version="0.3.0",
    lifespan=lifespan,
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request schemas
class VerifyRequest(BaseModel):
    content: Optional[str] = None


class PayRequest(BaseModel):
    human_approved: bool
    simulate_lost_response: bool = False


class RetryRequest(BaseModel):
    human_approved: bool


class SyncRequest(BaseModel):
    dispute_message: Optional[str] = None


class CancelReissueRequest(BaseModel):
    new_email: str
    new_email_verified: bool
    human_approved: bool


class InboundMessageRequest(BaseModel):
    sender: Optional[str] = None
    body: str


class CaseActionRequest(BaseModel):
    action: str
    human_approved: bool
    new_email: Optional[str] = None
    new_email_verified: Optional[bool] = None


class DemoResetRequest(BaseModel):
    scenario: Optional[str] = "timeout"


@app.get("/")
def root():
    return {
        "project": "SynapsePay",
        "status": "operational",
        "rail": "PayPal Payouts Sandbox (Zero-Escrow)",
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ==============================================================================
# MILESTONES
# ==============================================================================

@app.get("/milestones")
def list_milestones():
    return get_all_milestones()


@app.get("/milestones/{id}")
def get_single_milestone(id: str):
    m = get_milestone(id)
    if not m:
        raise HTTPException(status_code=404, detail=f"Milestone '{id}' not found")
    contract = get_contract(m["contract_id"]) if m.get("contract_id") else None
    return {
        **m,
        "contract": contract,
    }


@app.post("/milestones/{id}/verify", response_model=Verdict)
def verify_milestone_endpoint(id: str, req: Optional[VerifyRequest] = None):
    m = get_milestone(id)
    if not m:
        raise HTTPException(status_code=404, detail=f"Milestone '{id}' not found")

    content = req.content if (req and req.content) else f"Submission for {m['title']}"
    milestone_obj = Milestone(
        id=m["id"],
        contract_id=m["contract_id"],
        title=m["title"],
        amount=m["amount"],
        contractor_email=m["contractor_email"],
        acceptance_json=m["acceptance_json"],
    )
    deliverable = Deliverable(milestone_id=id, content=content)

    verdict = verify(milestone_obj, deliverable)

    record_event(
        type="VERDICT_RECORDED",
        milestone_id=id,
        payload=verdict.model_dump(),
    )

    return verdict


@app.post("/milestones/{id}/pay")
async def pay_milestone(id: str, req: PayRequest, request: Request):
    client_ip = request.client.host if request.client else "127.0.0.1"
    if not rate_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded: maximum 30 requests per minute.")

    m = get_milestone(id)
    if not m:
        raise HTTPException(status_code=404, detail=f"Milestone '{id}' not found")

    result = await execute_payout(
        milestone_id=id,
        human_approved=req.human_approved,
        simulate_lost_response=req.simulate_lost_response,
    )

    if not result.get("success"):
        return {
            "status": "REJECTED_OR_IN_PROGRESS",
            **result,
        }

    return {
        "status": "PROCESSED",
        **result,
    }


@app.get("/milestones/{id}/events")
def get_milestone_events(id: str):
    m = get_milestone(id)
    if not m:
        raise HTTPException(status_code=404, detail=f"Milestone '{id}' not found")
    return get_events_for_milestone(id)


@app.post("/milestones/{id}/messages")
def post_inbound_message(id: str, req: InboundMessageRequest):
    """
    Store inbound payee message and record MESSAGE_RECEIVED event.
    Simulated input from contractor inbox.
    """
    m = get_milestone(id)
    if not m:
        raise HTTPException(status_code=404, detail=f"Milestone '{id}' not found")

    msg = add_inbound_message(
        milestone_id=id,
        sender=req.sender,
        body=req.body,
    )

    record_event(
        type="MESSAGE_RECEIVED",
        milestone_id=id,
        payload={"sender": req.sender, "body": req.body, "message_id": msg.get("id")},
    )

    # Trigger deterministic case opening
    check_and_open_cases(id)

    return msg


@app.get("/milestones/{id}/case-evidence")
async def get_milestone_case_evidence(id: str):
    """
    Returns the complete unified evidence bundle for the AI investigator:
    - Contract info & milestone nominals
    - Live PayPal item & batch status fetched at call time
    - Append-only events timeline & contractor messages
    - Deterministic diversion risk & Levenshtein edit distance
    - Deterministically computed allowed_actions list (never plain resend)
    """
    m = get_milestone(id)
    if not m:
        raise HTTPException(status_code=404, detail=f"Milestone '{id}' not found")

    evidence = await get_case_evidence_bundle(id)
    return evidence


# ==============================================================================
# PAYOUTS & RETRY
# ==============================================================================

@app.get("/payouts")
def list_payouts():
    return get_all_payouts()


@app.post("/payouts/{id}/retry")
async def retry_payout_endpoint(id: int, req: RetryRequest):
    """
    Re-sends create_payout with the SAME sender_batch_id as the RESERVED row.
    Never automatically increments attempt number.
    Recovers payout if PayPal returns DuplicateBatchError with original batch or replays.
    """
    try:
        res = await retry_payout(
            payout_id=id,
            human_approved=req.human_approved,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/payouts/{id}/sync")
async def sync_payout_endpoint(id: int, req: Optional[SyncRequest] = None):
    payout = get_payout(id)
    if not payout:
        raise HTTPException(status_code=404, detail=f"Payout ID {id} not found")

    dispute_message = req.dispute_message if req else None
    sync_result = await sync_payout(
        payout_id=id,
        dispute_message=dispute_message,
    )
    return sync_result


@app.post("/payouts/{id}/cancel-reissue")
async def cancel_and_reissue_endpoint(id: int, req: CancelReissueRequest):
    payout = get_payout(id)
    if not payout:
        raise HTTPException(status_code=404, detail=f"Payout ID {id} not found")

    payout_item_id = payout.get("payout_item_id")
    if not payout_item_id:
        raise HTTPException(
            status_code=400,
            detail=f"Payout ID {id} does not have a PayPal payout_item_id associated with it.",
        )

    try:
        res = await execute_cancel_and_reissue(
            payout_item_id=payout_item_id,
            new_email=req.new_email,
            human_approved=req.human_approved,
            new_email_verified=req.new_email_verified,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==============================================================================
# CASES & ACTIONS
# ==============================================================================

@app.get("/cases")
def list_cases():
    """
    List all opened investigator cases across contracts.
    """
    return get_all_cases()


@app.post("/cases/{id}/actions")
async def execute_case_action_endpoint(id: int, req: CaseActionRequest):
    """
    Execute an allowed action for an open investigator case.
    Rejects any action not in deterministically computed allowed_actions.
    """
    try:
        res = await execute_case_action(
            case_id=id,
            action=req.action,
            human_approved=req.human_approved,
            new_email=req.new_email,
            new_email_verified=req.new_email_verified,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==============================================================================
# DEMO RESET
# ==============================================================================

@app.post("/demo/reset")
def reset_demo(request: Request, req: Optional[DemoResetRequest] = None):
    """
    Reseed demo data:
    Lumen Studio / Dewi: 2 milestones ($300 and $200).
    Scenarios:
    - "timeout": Milestone 1 contractor_email set to DEMO_CONTRACTOR_EMAIL (valid account)
    - "unclaimed": Milestone 1 contractor_email set to DEMO_TYPO_EMAIL (unregistered account)
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    if not rate_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded: maximum 30 requests per minute.")

    scenario = req.scenario if req and req.scenario else "timeout"
    return reseed_demo_data(scenario=scenario)
