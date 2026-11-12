"""FastAPI app: startup guard and routes (PRD 4.4, 12). Route handlers are plain def (PRD 4.4)."""
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, ValidationError
from starlette.datastructures import UploadFile

from app import db, events
from app.config import get_settings, load_env_file
from app.contracts import GateResult
from app.gate import executor, policy
from app.investigate import investigator
from app.verify import verifier

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env_file()
    get_settings().validate()  # raises ConfigError: the app never serves with a non-sandbox host (INV-12)
    conn = db.connect()
    try:
        if not db.schema_exists(conn):
            db.init_schema(conn)
        if conn.execute("SELECT 1 FROM meta WHERE key = 'run_id'").fetchone() is None:
            db.reset_demo(conn, "A")  # no database yet: seed scenario A (PRD 13.4)
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


class ResetBody(_Body):
    scenario: Literal["A", "B"]


class ArmFaultBody(_Body):
    fault: Literal["timeout_after_send", "timeout_before_send"]


class FixtureBody(_Body):
    fixture: str
    submitted_by: str


MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # per file (PRD 12)


@dataclass
class DeliverableInput:
    submitted_by: str
    fixture: str | None = None
    csv_name: str = ""
    csv_bytes: bytes = b""
    report_text: str = ""


async def read_deliverable(request: Request) -> DeliverableInput:
    """Reads the JSON form {fixture, submitted_by} or the multipart form. A dependency, so the
    route handler itself stays a plain def (PRD 4.4)."""
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        csv_file, report_file, submitted_by = form.get("csv_file"), form.get("report_file"), form.get("submitted_by")
        if not isinstance(csv_file, UploadFile) or not isinstance(report_file, UploadFile) or not submitted_by:
            raise HTTPException(status_code=422, detail="csv_file, report_file and submitted_by are required")
        csv_bytes = await csv_file.read(MAX_UPLOAD_BYTES + 1)
        report_bytes = await report_file.read(MAX_UPLOAD_BYTES + 1)
        if len(csv_bytes) > MAX_UPLOAD_BYTES or len(report_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="each file is limited to 5 MB")
        return DeliverableInput(submitted_by=str(submitted_by), csv_name=csv_file.filename or "upload.csv",
                                csv_bytes=csv_bytes, report_text=report_bytes.decode("utf-8", errors="replace"))
    try:
        body = FixtureBody.model_validate(await request.json())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json()))
    except ValueError:
        raise HTTPException(status_code=422, detail="body must be JSON or multipart")
    return DeliverableInput(submitted_by=body.submitted_by, fixture=body.fixture)


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


def _load(value: str | None):
    return json.loads(value) if value else None


@app.get("/api/state")
def state():
    """Full snapshot for the operator page (PRD 12.1). No secret or env value except the two modes."""
    s = get_settings()
    conn = db.connect()
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        contract = conn.execute("SELECT * FROM contracts ORDER BY id LIMIT 1").fetchone()
        milestones = []
        for m in conn.execute("SELECT * FROM milestones ORDER BY seq").fetchall():
            milestones.append({
                "id": m["id"], "seq": m["seq"], "title": m["title"], "amount_cents": m["amount_cents"],
                "state": m["state"], "verdict": m["verdict"], "override_reason": m["override_reason"],
                "override_by": m["override_by"], "criteria": _load(m["criteria_json"]),
                "verification": _load(m["verification_json"]),
                "gate_preview": policy.evaluate_pay(conn, m["id"], expected_amount_cents=m["amount_cents"],
                                                    preview=True),
            })
        cases = []
        for row in conn.execute("SELECT * FROM cases ORDER BY opened_at, id").fetchall():
            case = dict(row)
            case["report"] = _load(case.pop("report_json"))
            cases.append(case)
        return {
            "run_id": meta.get("run_id"), "scenario": meta.get("scenario"),
            "paypal_mode": s.paypal_mode, "llm_mode": s.llm_mode,
            "contract": None if contract is None else {k: contract[k] for k in (
                "id", "buyer_name", "contractor_name", "payee_email", "payee_email_verified_at",
                "payee_email_verified_by", "currency")},
            "milestones": milestones,
            "payouts": [dict(r) for r in conn.execute("SELECT * FROM payouts ORDER BY created_at, attempt")],
            "cases": cases,
            "messages": [dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY created_at, id")],
            "events": events.list_events(conn, limit=300),
        }
    finally:
        conn.close()


@app.post("/api/demo/reset")
def demo_reset(body: ResetBody):
    """Resets the database into a scenario. The reset-time cancel of UNCLAIMED items is cut 1."""
    conn = db.connect()
    try:
        result = db.reset_demo(conn, body.scenario)
    finally:
        conn.close()
    if get_settings().paypal_mode == "fake":
        from app.paypal.fake import reset_fake
        reset_fake()
    return result


@app.post("/api/demo/arm-fault")
def demo_arm_fault(body: ArmFaultBody):
    """Arms a fault for the next create_payout. It replaces any armed fault (PRD 12.2)."""
    conn = db.connect()
    try:
        with db.tx(conn):
            conn.execute("INSERT INTO meta (key, value) VALUES ('armed_fault', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (body.fault,))
            events.append(conn, "FAULT_ARMED", "system", payload={"fault": body.fault})
    finally:
        conn.close()
    return {"armed": body.fault}


@app.post("/api/milestones/{milestone_id}/deliverable")
def submit_deliverable(milestone_id: str, data: DeliverableInput = Depends(read_deliverable)):
    try:
        if data.fixture:
            return verifier.submit_fixture(milestone_id, data.fixture, submitted_by=data.submitted_by)
        return verifier.submit(milestone_id, csv_name=data.csv_name, csv_bytes=data.csv_bytes,
                               report_text=data.report_text, submitted_by=data.submitted_by)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except verifier.MilestoneLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown case {case_id!r}")
        case = dict(row)
        case["report"] = _load(case.pop("report_json"))
        case["events"] = events.list_events(conn, case_id=case_id)
        return case
    finally:
        conn.close()


def _case_view(case: dict) -> dict:
    """A cases row as the API shows it: the report parsed, as in /api/state."""
    view = dict(case)
    view["report"] = _load(view.pop("report_json", None))
    return view


@app.post("/api/cases/{case_id}/actions/reconcile")
def reconcile_case(case_id: str, body: ApprovalBody):
    """Gated RECONCILE: a fresh PayPal read plus a human approval moves an UNKNOWN payout to PAID."""
    try:
        result = executor.reconcile(case_id, approved_by=body.approved_by, confirm=body.confirm)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not result.allowed:
        return _refused(result.gate)
    return {"gate": result.gate, "payout": result.payout, "case": _case_view(result.case)}


@app.post("/api/cases/{case_id}/investigate")
def investigate_case(case_id: str):
    try:
        return investigator.investigate(case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except investigator.CaseNotInvestigable as exc:
        raise HTTPException(status_code=409, detail=str(exc))


if STATIC_DIR.is_dir():  # B4 adds app/static; GET / serves the operator page
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
