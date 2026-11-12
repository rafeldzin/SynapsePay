"""FastAPI app: startup guard and routes (PRD 4.4, 12). Route handlers are plain def (PRD 4.4)."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from app import db
from app.config import get_settings, load_env_file
from app.contracts import GateResult
from app.gate import executor, policy

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env_file()
    get_settings().validate()  # raises ConfigError: the app never serves with a non-sandbox host (INV-12)
    conn = db.connect()
    try:
        if not db.schema_exists(conn):
            db.init_schema(conn)
        executor.recover_creating(conn)  # a CREATING row older than 60 s becomes UNKNOWN (PRD 6.2)
    finally:
        conn.close()
    yield


app = FastAPI(title="SynapsePay", lifespan=lifespan)


# ---- request bodies: unknown fields are rejected with 422 (INV-6) ---------------

class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalBody(_Body):
    approved_by: str
    confirm: StrictBool


class PayBody(ApprovalBody):
    expected_amount_cents: StrictInt
    override_reason: str | None = None


def _refused(gate: GateResult) -> JSONResponse:
    """A gate refusal is HTTP 409 with the GateResult as the body (PRD 12)."""
    return JSONResponse(status_code=409, content=gate.model_dump())


# ---- routes ----------------------------------------------------------------------

@app.get("/api/health")
def health():
    s = get_settings()
    conn = db.connect()
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()
    return {"ok": True, "run_id": meta.get("run_id"), "scenario": meta.get("scenario"),
            "paypal_mode": s.paypal_mode, "llm_mode": s.llm_mode}


@app.get("/api/milestones/{milestone_id}/gate", response_model=GateResult)
def milestone_gate(milestone_id: str):
    """PAY preview: the human check stays pending and no event is written (UAT-PAY-11)."""
    conn = db.connect()
    try:
        m = conn.execute("SELECT amount_cents FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
        if m is None:
            raise HTTPException(status_code=404, detail=f"unknown milestone {milestone_id!r}")
        return policy.evaluate_pay(conn, milestone_id, expected_amount_cents=m["amount_cents"], preview=True)
    finally:
        conn.close()


@app.post("/api/milestones/{milestone_id}/pay")
def pay_milestone(milestone_id: str, body: PayBody):
    try:
        result = executor.pay(milestone_id, approved_by=body.approved_by, confirm=body.confirm,
                              expected_amount_cents=body.expected_amount_cents,
                              override_reason=body.override_reason)
    except LookupError:
        raise HTTPException(status_code=404, detail=f"unknown milestone {milestone_id!r}")
    if not result.allowed:
        return _refused(result.gate)
    return {"gate": result.gate, "payout": result.payout}


@app.post("/api/payouts/{payout_id}/retry")
def retry_payout(payout_id: str, body: ApprovalBody):
    try:
        result = executor.retry(payout_id, approved_by=body.approved_by, confirm=body.confirm)
    except LookupError:
        raise HTTPException(status_code=404, detail=f"unknown payout {payout_id!r}")
    if not result.allowed:
        return _refused(result.gate)
    return {"gate": result.gate, "payout": result.payout, "case_id": result.case_id}


if STATIC_DIR.is_dir():  # B4 adds app/static; GET / serves the operator page
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
