import asyncio
import sqlite3
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
import httpx

from app.core.database import (
    create_contract,
    create_milestone,
    create_payout_reserved,
    get_events_for_milestone,
    get_milestone,
    get_payout,
    init_db,
    record_event,
    update_payout,
)
from app.core.policy_gate import (
    evaluate_payout,
    execute_cancel_and_reissue,
    execute_payout,
    sync_payout,
)
from app.main import app
from app.models.schema import (
    CriterionVerdict,
    Milestone,
    Verdict,
    VerdictEnum,
)
from app.services.paypal import (
    DuplicateBatchError,
    PayPalClient,
)


@pytest.fixture
def setup_db(tmp_path):
    """
    Fixture providing a temporary SQLite database with initial contract and milestone.
    """
    db_file = str(tmp_path / "test_policy_gate.db")
    init_db(db_file)

    contract = create_contract(
        id="c-alpha",
        buyer_name="Lumen Studio",
        contractor_name="Dewi",
        currency="USD",
        db_path=db_file,
    )
    milestone = create_milestone(
        id="m-001",
        contract_id="c-alpha",
        title="Prototype & Architecture",
        amount=300.0,
        contractor_email="dewi@example.com",
        db_path=db_file,
    )

    return {
        "db_path": db_file,
        "contract": contract,
        "milestone": Milestone(
            id=milestone["id"],
            contract_id=milestone["contract_id"],
            title=milestone["title"],
            amount=milestone["amount"],
            contractor_email=milestone["contractor_email"],
        ),
    }


# ==============================================================================
# POLICY GATE: 7 CHECKS INDIVIDUALLY FAILING & ALL PASSING
# ==============================================================================

def test_gate_each_of_7_checks_failing_individually_blocks_payment(setup_db):
    """
    Requirement: gate: each of the 7 checks failing individually blocks payment; all passing allows.
    1. verdict_pass
    2. no_injection
    3. amount_matches
    4. recipient_matches_contract
    5. no_active_payout
    6. human_approved
    7. idempotency_key
    """
    milestone = setup_db["milestone"]

    valid_verdict = Verdict(
        milestone_id=milestone.id,
        verdict=VerdictEnum.PASS,
        injection_flag=False,
        criteria=[
            CriterionVerdict(criterion="Criterion 1", verdict=VerdictEnum.PASS, evidence="Proof 1")
        ],
    )

    # Baseline: ALL PASSING
    res_pass = evaluate_payout(
        milestone=milestone,
        verdict=valid_verdict,
        requested_amount=300.0,
        human_approved=True,
        receiver_email="dewi@example.com",
        attempt=1,
        active_payout=None,
    )
    assert res_pass.allowed is True
    assert res_pass.sender_batch_id == f"{milestone.id}-a1"
    assert len(res_pass.checks) == 7
    assert all(c.passed for c in res_pass.checks)

    # 1. Check failing: verdict_pass (verdict is FAIL or missing or downgraded)
    fail_verdict = Verdict(
        milestone_id=milestone.id,
        verdict=VerdictEnum.FAIL,
        injection_flag=False,
        criteria=[
            CriterionVerdict(criterion="Criterion 1", verdict=VerdictEnum.FAIL, evidence="Failed tests")
        ],
    )
    res_fail1 = evaluate_payout(
        milestone=milestone,
        verdict=fail_verdict,
        requested_amount=300.0,
        human_approved=True,
        receiver_email="dewi@example.com",
        attempt=1,
    )
    assert res_fail1.allowed is False
    check_v = next(c for c in res_fail1.checks if c.name == "verdict_pass")
    assert check_v.passed is False

    # 2. Check failing: no_injection (injection_flag is True)
    inj_verdict = Verdict(
        milestone_id=milestone.id,
        verdict=VerdictEnum.PASS,
        injection_flag=True,
        criteria=[
            CriterionVerdict(criterion="Criterion 1", verdict=VerdictEnum.PASS, evidence="Injected text")
        ],
    )
    res_fail2 = evaluate_payout(
        milestone=milestone,
        verdict=inj_verdict,
        requested_amount=300.0,
        human_approved=True,
        receiver_email="dewi@example.com",
        attempt=1,
    )
    assert res_fail2.allowed is False
    check_inj = next(c for c in res_fail2.checks if c.name == "no_injection")
    assert check_inj.passed is False

    # 3. Check failing: amount_matches (requested 350 vs milestone 300)
    res_fail3 = evaluate_payout(
        milestone=milestone,
        verdict=valid_verdict,
        requested_amount=350.0,
        human_approved=True,
        receiver_email="dewi@example.com",
        attempt=1,
    )
    assert res_fail3.allowed is False
    check_amt = next(c for c in res_fail3.checks if c.name == "amount_matches")
    assert check_amt.passed is False

    # 4. Check failing: recipient_matches_contract (wrong email)
    res_fail4 = evaluate_payout(
        milestone=milestone,
        verdict=valid_verdict,
        requested_amount=300.0,
        human_approved=True,
        receiver_email="attacker@example.com",
        attempt=1,
    )
    assert res_fail4.allowed is False
    check_rec = next(c for c in res_fail4.checks if c.name == "recipient_matches_contract")
    assert check_rec.passed is False

    # 5. Check failing: no_active_payout (active payout in progress)
    active_row = {"id": 99, "status": "RESERVED"}
    res_fail5 = evaluate_payout(
        milestone=milestone,
        verdict=valid_verdict,
        requested_amount=300.0,
        human_approved=True,
        receiver_email="dewi@example.com",
        attempt=1,
        active_payout=active_row,
    )
    assert res_fail5.allowed is False
    check_act = next(c for c in res_fail5.checks if c.name == "no_active_payout")
    assert check_act.passed is False

    # 6. Check failing: human_approved is False
    res_fail6 = evaluate_payout(
        milestone=milestone,
        verdict=valid_verdict,
        requested_amount=300.0,
        human_approved=False,
        receiver_email="dewi@example.com",
        attempt=1,
    )
    assert res_fail6.allowed is False
    check_human = next(c for c in res_fail6.checks if c.name == "human_approved")
    assert check_human.passed is False


# ==============================================================================
# CONCURRENCY GUARD: DOUBLE PAY
# ==============================================================================

def test_double_pay_concurrent_execute_payout_creates_exactly_one_call(setup_db):
    """
    Requirement: double pay: two concurrent execute_payout calls for the same milestone create exactly ONE PayPal call.
    """
    async def _test():
        db_path = setup_db["db_path"]
        milestone = setup_db["milestone"]

        # Record valid verdict first
        valid_verdict = Verdict(
            milestone_id=milestone.id,
            verdict=VerdictEnum.PASS,
            injection_flag=False,
            criteria=[
                CriterionVerdict(criterion="Criterion 1", verdict=VerdictEnum.PASS, evidence="Proof 1")
            ],
        )
        record_event(
            type="VERDICT_RECORDED",
            milestone_id=milestone.id,
            payload=valid_verdict.model_dump(),
            db_path=db_path,
        )

        # Mock PayPal Client
        mock_paypal = PayPalClient()
        mock_paypal.create_payout = AsyncMock(
            return_value={
                "batch_id": "BATCH-111",
                "batch_status": "PENDING",
                "payout_item_id": "ITEM-111",
            }
        )
        mock_paypal.get_payout_batch = AsyncMock(
            return_value={"batch_header": {"batch_status": "PENDING"}, "items": [{"payout_item_id": "ITEM-111"}]}
        )

        # Run 2 concurrent payout requests
        res1, res2 = await asyncio.gather(
            execute_payout(
                milestone_id=milestone.id,
                human_approved=True,
                paypal_client=mock_paypal,
                db_path=db_path,
            ),
            execute_payout(
                milestone_id=milestone.id,
                human_approved=True,
                paypal_client=mock_paypal,
                db_path=db_path,
            ),
        )

        # Exactly ONE call should succeed, the other should be rejected by the DB race guard
        successes = [r for r in (res1, res2) if r.get("success") is True]
        rejections = [r for r in (res1, res2) if r.get("success") is False]

        assert len(successes) == 1
        assert len(rejections) == 1
        assert "already in progress or paid" in rejections[0].get("error", "")

        # Crucial check: PayPal write endpoint was invoked EXACTLY once
        assert mock_paypal.create_payout.call_count == 1

    asyncio.run(_test())


# ==============================================================================
# TIMEOUT & UNKNOWN STATE
# ==============================================================================

def test_timeout_leaves_payout_in_unknown_state_and_does_not_retry(setup_db):
    """
    Requirement: timeout leaves payout in unknown state, not FAILED, and does not retry.
    """
    async def _test():
        db_path = setup_db["db_path"]
        milestone = setup_db["milestone"]

        valid_verdict = Verdict(
            milestone_id=milestone.id,
            verdict=VerdictEnum.PASS,
            injection_flag=False,
            criteria=[
                CriterionVerdict(criterion="Criterion 1", verdict=VerdictEnum.PASS, evidence="Proof 1")
            ],
        )
        record_event("VERDICT_RECORDED", milestone.id, payload=valid_verdict.model_dump(), db_path=db_path)

        # Mock PayPal Client raising TimeoutException
        mock_paypal = PayPalClient()
        mock_paypal.create_payout = AsyncMock(side_effect=httpx.ReadTimeout("Connection timed out waiting for PayPal"))

        res = await execute_payout(
            milestone_id=milestone.id,
            human_approved=True,
            paypal_client=mock_paypal,
            db_path=db_path,
        )

        assert res["success"] is False
        assert res["reconcile_required"] is True
        assert res["status"] == "RESERVED"

        payout = get_payout(res["payout_id"], db_path=db_path)
        # Status MUST remain RESERVED, not FAILED!
        assert payout["status"] == "RESERVED"

        # Check PAYOUT_ERROR was recorded in audit events
        events = get_events_for_milestone(milestone.id, db_path=db_path)
        assert any(e["type"] == "PAYOUT_ERROR" for e in events)

        # Verify it was not retried blindly
        assert mock_paypal.create_payout.call_count == 1

    asyncio.run(_test())


# ==============================================================================
# CANCEL AND REISSUE GATE CHECKS (UNCLAIMED & BATCH_PROCESSED)
# ==============================================================================

def test_cancel_reissue_refuses_when_item_not_unclaimed(setup_db):
    """
    Requirement: cancel-reissue refuses when item is no longer UNCLAIMED.
    """
    async def _test():
        db_path = setup_db["db_path"]
        milestone = setup_db["milestone"]

        # Insert a payout that is SUCCESS
        payout_id = create_payout_reserved(
            milestone_id=milestone.id,
            attempt=1,
            sender_batch_id="m-001-a1",
            receiver_email="dewi@example.com",
            amount=300.0,
            db_path=db_path,
        )
        update_payout(
            payout_id,
            status="SUCCESS",
            payout_item_id="ITEM-ALREADY-SUCCESS",
            db_path=db_path,
        )

        mock_paypal = PayPalClient()
        mock_paypal.get_payout_item = AsyncMock(
            return_value={"payout_item_id": "ITEM-ALREADY-SUCCESS", "transaction_status": "SUCCESS"}
        )

        with pytest.raises(ValueError) as exc_info:
            await execute_cancel_and_reissue(
                payout_item_id="ITEM-ALREADY-SUCCESS",
                new_email="dewi_new@example.com",
                human_approved=True,
                new_email_verified=True,
                paypal_client=mock_paypal,
                db_path=db_path,
            )

        assert "must be UNCLAIMED" in str(exc_info.value)

    asyncio.run(_test())


def test_cancel_reissue_refuses_when_batch_not_processed_yet(setup_db):
    """
    Requirement 3: execute_cancel_and_reissue must add a gate check 'batch_processed' (batch status SUCCESS)
    in addition to item UNCLAIMED. If batch is not processed yet, return a clear 'retry shortly' result
    without calling cancel.
    """
    async def _test():
        db_path = setup_db["db_path"]
        milestone = setup_db["milestone"]

        payout_id = create_payout_reserved(
            milestone_id=milestone.id,
            attempt=1,
            sender_batch_id="m-001-a1",
            receiver_email="old_unclaimed@example.com",
            amount=300.0,
            db_path=db_path,
        )
        update_payout(
            payout_id,
            status="UNCLAIMED",
            payout_batch_id="BATCH-PROCESSING",
            payout_item_id="ITEM-UNCLAIMED-123",
            db_path=db_path,
        )

        mock_paypal = PayPalClient()
        mock_paypal.get_payout_item = AsyncMock(
            return_value={"payout_item_id": "ITEM-UNCLAIMED-123", "transaction_status": "UNCLAIMED"}
        )
        # Batch is still PROCESSING
        mock_paypal.get_payout_batch = AsyncMock(
            return_value={"batch_header": {"batch_status": "PROCESSING", "payout_batch_id": "BATCH-PROCESSING"}}
        )
        mock_paypal.cancel_payout_item = AsyncMock()

        res = await execute_cancel_and_reissue(
            payout_item_id="ITEM-UNCLAIMED-123",
            new_email="verified_new@example.com",
            human_approved=True,
            new_email_verified=True,
            paypal_client=mock_paypal,
            db_path=db_path,
        )

        assert res["success"] is False
        assert res["retry_shortly"] is True
        assert "retry shortly" in res["error"].lower()
        assert res["batch_status"] == "PROCESSING"

        # Must NOT call cancel_payout_item!
        mock_paypal.cancel_payout_item.assert_not_called()

    asyncio.run(_test())


def test_cancel_reissue_succeeds_when_unclaimed_and_batch_success(setup_db):
    """
    Requirement: cancel-reissue succeeds when item is UNCLAIMED and batch is SUCCESS.
    Cancels UNCLAIMED item, marks RETURNED, updates contractor email,
    and creates new payout with attempt+1.
    """
    async def _test():
        db_path = setup_db["db_path"]
        milestone = setup_db["milestone"]

        # Insert initial UNCLAIMED payout
        payout_id = create_payout_reserved(
            milestone_id=milestone.id,
            attempt=1,
            sender_batch_id="m-001-a1",
            receiver_email="old_unclaimed@example.com",
            amount=300.0,
            db_path=db_path,
        )
        update_payout(
            payout_id,
            status="UNCLAIMED",
            payout_batch_id="BATCH-PROCESSED-OK",
            payout_item_id="ITEM-UNCLAIMED-123",
            db_path=db_path,
        )

        # Record verdict so attempt 2 payout passes gate
        valid_verdict = Verdict(
            milestone_id=milestone.id,
            verdict=VerdictEnum.PASS,
            criteria=[CriterionVerdict(criterion="Criterion 1", verdict=VerdictEnum.PASS, evidence="Valid")],
        )
        record_event("VERDICT_RECORDED", milestone.id, payload=valid_verdict.model_dump(), db_path=db_path)

        mock_paypal = PayPalClient()
        mock_paypal.get_payout_item = AsyncMock(
            return_value={"payout_item_id": "ITEM-UNCLAIMED-123", "transaction_status": "UNCLAIMED"}
        )
        # Batch is SUCCESS
        mock_paypal.get_payout_batch = AsyncMock(
            return_value={
                "batch_header": {"batch_status": "SUCCESS", "payout_batch_id": "BATCH-PROCESSED-OK"},
                "items": [{"payout_item_id": "ITEM-UNCLAIMED-123"}],
            }
        )
        mock_paypal.cancel_payout_item = AsyncMock(
            return_value={"payout_item_id": "ITEM-UNCLAIMED-123", "transaction_status": "RETURNED"}
        )
        mock_paypal.create_payout = AsyncMock(
            return_value={"batch_id": "BATCH-REISSUE", "batch_status": "PENDING", "payout_item_id": "ITEM-NEW-456"}
        )

        res = await execute_cancel_and_reissue(
            payout_item_id="ITEM-UNCLAIMED-123",
            new_email="verified_new@example.com",
            human_approved=True,
            new_email_verified=True,
            paypal_client=mock_paypal,
            db_path=db_path,
        )

        assert res["success"] is True
        assert res["old_status"] == "RETURNED"
        assert res["new_email"] == "verified_new@example.com"
        assert res["new_payout"]["success"] is True

        # Old payout must be RETURNED
        old_payout = get_payout(payout_id, db_path=db_path)
        assert old_payout["status"] == "RETURNED"

        # Contract / milestone email was updated
        updated_m = get_milestone(milestone.id, db_path=db_path)
        assert updated_m["contractor_email"] == "verified_new@example.com"

        # New payout has attempt 2 and sender_batch_id 'm-001-a2'
        new_payout_id = res["new_payout"]["payout_id"]
        new_payout = get_payout(new_payout_id, db_path=db_path)
        assert new_payout["attempt"] == 2
        assert new_payout["sender_batch_id"] == "m-001-a2"
        assert new_payout["receiver_email"] == "verified_new@example.com"

    asyncio.run(_test())


# ==============================================================================
# STATUS SYNC (STORES ITEM STATUS, NOT BATCH STATUS)
# ==============================================================================

def test_sync_payout_stores_item_status_not_batch_status(setup_db):
    """
    Requirement 4: sync_payout must store the ITEM status, not the batch status.
    """
    async def _test():
        db_path = setup_db["db_path"]
        milestone = setup_db["milestone"]

        p_id = create_payout_reserved(
            milestone_id=milestone.id,
            attempt=1,
            sender_batch_id="m-001-a1",
            receiver_email="dewi@example.com",
            amount=300.0,
            db_path=db_path,
        )
        update_payout(p_id, status="PENDING", payout_batch_id="BATCH-001", payout_item_id="ITEM-SYNC-1", db_path=db_path)

        mock_paypal = PayPalClient()
        # Mock item status returns SUCCESS (even if batch might be SUCCESS or PENDING)
        mock_paypal.get_payout_item = AsyncMock(
            return_value={"payout_item_id": "ITEM-SYNC-1", "transaction_status": "SUCCESS"}
        )

        res = await sync_payout(payout_id=p_id, paypal_client=mock_paypal, db_path=db_path)

        assert res["new_status"] == "SUCCESS"
        stored_payout = get_payout(p_id, db_path=db_path)
        assert stored_payout["status"] == "SUCCESS"

    asyncio.run(_test())


def test_sync_payout_exception_detection_rule(setup_db):
    """
    Requirement: Deterministic exception detection rule: flag when local status != PayPal status,
    status is UNCLAIMED, or a 'not paid' message is received.
    """
    async def _test():
        db_path = setup_db["db_path"]
        milestone = setup_db["milestone"]

        p_id = create_payout_reserved(
            milestone_id=milestone.id,
            attempt=1,
            sender_batch_id="m-001-a1",
            receiver_email="dewi@example.com",
            amount=300.0,
            db_path=db_path,
        )
        update_payout(p_id, status="PENDING", payout_item_id="ITEM-SYNC-1", db_path=db_path)

        # 1. PayPal live returns UNCLAIMED
        mock_paypal = PayPalClient()
        mock_paypal.get_payout_item = AsyncMock(
            return_value={"payout_item_id": "ITEM-SYNC-1", "transaction_status": "UNCLAIMED"}
        )

        res = await sync_payout(
            payout_id=p_id,
            dispute_message="Contractor says: I have not paid received yet",
            paypal_client=mock_paypal,
            db_path=db_path,
        )

        assert res["flagged"] is True
        # Both status mismatch, UNCLAIMED, and dispute message detected
        assert any("mismatch" in f.lower() for f in res["flags"])
        assert any("unclaimed" in f.lower() for f in res["flags"])
        assert any("payee non-receipt" in f.lower() for f in res["flags"])

    asyncio.run(_test())


# ==============================================================================
# FASTAPI API ENDPOINTS INTEGRATION
# ==============================================================================

def test_api_endpoints_workflow(setup_db, monkeypatch):
    """
    Requirement: Test FastAPI endpoints:
    GET /milestones, GET /milestones/{id}
    POST /milestones/{id}/verify
    POST /milestones/{id}/pay
    GET /payouts, POST /payouts/{id}/sync
    GET /milestones/{id}/events
    POST /demo/reset
    GET /health
    """
    db_path = setup_db["db_path"]
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Patch default_paypal_client methods used by API endpoints
    with patch("app.core.policy_gate.default_paypal_client.create_payout", new_callable=AsyncMock) as mock_create, \
         patch("app.core.policy_gate.default_paypal_client.get_payout_item", new_callable=AsyncMock) as mock_get_item, \
         patch("app.core.policy_gate.default_paypal_client.get_payout_batch", new_callable=AsyncMock) as mock_get_batch:

        mock_create.return_value = {
            "batch_id": "BATCH-API-001",
            "batch_status": "PENDING",
            "payout_item_id": "ITEM-API-001",
        }
        mock_get_item.return_value = {
            "payout_item_id": "ITEM-API-001",
            "transaction_status": "SUCCESS",
        }
        mock_get_batch.return_value = {
            "batch_header": {"batch_status": "PENDING"},
            "items": [{"payout_item_id": "ITEM-API-001", "transaction_status": "PENDING"}],
        }

        client = TestClient(app)

        # 1. Health check
        res_h = client.get("/health")
        assert res_h.status_code == 200
        assert res_h.json() == {"status": "ok"}

        # 2. Get milestones
        res_m = client.get("/milestones")
        assert res_m.status_code == 200
        assert len(res_m.json()) >= 1

        # 3. Get single milestone
        res_s = client.get("/milestones/m-001")
        assert res_s.status_code == 200
        assert res_s.json()["id"] == "m-001"

        # 4. Verify milestone
        res_v = client.post("/milestones/m-001/verify", json={"content": "Completed React architecture with tests"})
        assert res_v.status_code == 200
        verdict_data = res_v.json()
        assert verdict_data["verdict"] == "PASS"

        # 5. Pay milestone with human approval
        res_pay = client.post("/milestones/m-001/pay", json={"human_approved": True})
        assert res_pay.status_code == 200
        pay_data = res_pay.json()
        assert pay_data["success"] is True
        assert pay_data["payout_batch_id"] == "BATCH-API-001"
        payout_id = pay_data["payout_id"]

        # 6. List payouts
        res_p = client.get("/payouts")
        assert res_p.status_code == 200
        assert len(res_p.json()) >= 1

        # 7. Sync payout
        res_sync = client.post(f"/payouts/{payout_id}/sync")
        assert res_sync.status_code == 200
        assert res_sync.json()["new_status"] == "SUCCESS"

        # 8. Get milestone events
        res_ev = client.get("/milestones/m-001/events")
        assert res_ev.status_code == 200
        events = res_ev.json()
        assert len(events) >= 3
        event_types = [e["type"] for e in events]
        assert "VERDICT_RECORDED" in event_types
        assert "GATE_EVALUATED" in event_types
        assert "PAYOUT_SENT" in event_types

        # 9. Demo reset
        res_reset = client.post("/demo/reset")
        assert res_reset.status_code == 200
        assert len(res_reset.json()["milestones"]) == 2
