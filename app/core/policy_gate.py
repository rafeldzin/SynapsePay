import re
import sqlite3
from typing import Any, Dict, List, Optional
import httpx

from app.core.database import (
    ActivePayoutExistsError,
    create_case,
    create_payout_reserved,
    get_active_payout_for_milestone,
    get_all_cases,
    get_case,
    get_cases_for_milestone,
    get_events_for_milestone,
    get_inbound_messages_for_milestone,
    get_latest_payout_attempt,
    get_latest_verdict_for_milestone,
    get_milestone,
    get_payout,
    get_payout_by_item_id,
    get_payouts_for_milestone,
    record_event,
    update_case_status,
    update_milestone_contractor_email,
    update_payout,
)
from app.models.schema import (
    GateCheck,
    Milestone,
    PolicyGateResult,
    Verdict,
    VerdictEnum,
)
from app.services.paypal import (
    DuplicateBatchError,
    PayPalClient,
    PayPalError,
    default_paypal_client,
)
from app.verifier.interface import apply_coverage_rule


# ==============================================================================
# HELPER FUNCTIONS: EMAIL EXTRACTION & LEVENSHTEIN DISTANCE
# ==============================================================================

def extract_emails_from_text(text: str) -> List[str]:
    """
    Extract all email addresses from text using standard email regex.
    """
    if not text:
        return []
    pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    matches = re.findall(pattern, text)
    seen = set()
    deduped = []
    for m in matches:
        m_lower = m.lower()
        if m_lower not in seen:
            seen.add(m_lower)
            deduped.append(m)
    return deduped


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Compute standard Levenshtein edit distance between two strings (case-insensitive).
    """
    s1, s2 = (s1 or "").lower(), (s2 or "").lower()
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,       # deletion
                dp[i][j - 1] + 1,       # insertion
                dp[i - 1][j - 1] + cost  # substitution
            )
    return dp[m][n]


# ==============================================================================
# POLICY GATE EVALUATION
# ==============================================================================

def evaluate_payout(
    milestone: Milestone,
    verdict: Optional[Verdict],
    requested_amount: float,
    human_approved: bool,
    receiver_email: str,
    attempt: int = 1,
    active_payout: Optional[Dict[str, Any]] = None,
    is_reissue: bool = False,
) -> PolicyGateResult:
    """
    Evaluate whether a milestone payout can be safely executed.
    Checks (all 7 must pass):
    1. verdict_pass: overall verdict PASS after coverage rule
    2. no_injection: injection_flag is False
    3. amount_matches: requested_amount == milestone.amount
    4. recipient_matches_contract: receiver_email == milestone.contractor_email (or verified new_email if is_reissue)
    5. no_active_payout: no active payout row for this milestone
    6. human_approved: True
    7. idempotency_key: sender_batch_id = f"{milestone.id}-a{attempt}"
    """
    checks: List[GateCheck] = []
    sender_batch_id = f"{milestone.id}-a{attempt}"

    # 1. verdict_pass
    if verdict is None:
        checks.append(
            GateCheck(
                name="verdict_pass",
                passed=False,
                detail="No verification verdict recorded for this milestone.",
            )
        )
    else:
        evaluated_verdict = apply_coverage_rule(verdict)
        passed = evaluated_verdict.verdict == VerdictEnum.PASS
        detail = (
            "Overall verdict is PASS after coverage rule evaluation."
            if passed
            else f"Verdict is '{evaluated_verdict.verdict.value}', but PASS is required."
        )
        checks.append(GateCheck(name="verdict_pass", passed=passed, detail=detail))

    # 2. no_injection
    if verdict is None:
        checks.append(
            GateCheck(
                name="no_injection",
                passed=False,
                detail="Cannot confirm injection status: verdict is missing.",
            )
        )
    else:
        passed = not verdict.injection_flag
        detail = (
            "No prompt injection or adversarial override detected."
            if passed
            else "Prompt injection or adversarial override flag is active."
        )
        checks.append(GateCheck(name="no_injection", passed=passed, detail=detail))

    # 3. amount_matches
    amount_matches = abs(float(requested_amount) - float(milestone.amount)) < 0.001
    checks.append(
        GateCheck(
            name="amount_matches",
            passed=amount_matches,
            detail=(
                f"Requested amount ${requested_amount:.2f} matches milestone nominal ${milestone.amount:.2f}."
                if amount_matches
                else f"Requested amount ${requested_amount:.2f} does not match milestone nominal ${milestone.amount:.2f}."
            ),
        )
    )

    # 4. recipient_matches_contract
    if is_reissue:
        email_matches = bool(receiver_email and receiver_email.strip())
        detail = f"Reissue to verified new contractor email: '{receiver_email}'."
    else:
        email_clean = (receiver_email or "").strip().lower()
        milestone_email_clean = (milestone.contractor_email or "").strip().lower()
        email_matches = bool(email_clean and email_clean == milestone_email_clean)
        detail = (
            f"Receiver email '{receiver_email}' matches contract contractor email."
            if email_matches
            else f"Receiver email '{receiver_email}' does not match contract email '{milestone.contractor_email}'."
        )
    checks.append(
        GateCheck(
            name="recipient_matches_contract",
            passed=email_matches,
            detail=detail,
        )
    )

    # 5. no_active_payout
    no_active = active_payout is None
    checks.append(
        GateCheck(
            name="no_active_payout",
            passed=no_active,
            detail=(
                "No active payout currently in progress for this milestone."
                if no_active
                else f"Active payout row (ID {active_payout.get('id')}, status '{active_payout.get('status')}') exists."
            ),
        )
    )

    # 6. human_approved
    checks.append(
        GateCheck(
            name="human_approved",
            passed=human_approved is True,
            detail=(
                "Human approval explicitly granted."
                if human_approved is True
                else "Human approval is required before calling payment rail."
            ),
        )
    )

    # 7. idempotency_key
    checks.append(
        GateCheck(
            name="idempotency_key",
            passed=True,
            detail=f"Deterministic idempotency key generated: {sender_batch_id}",
        )
    )

    allowed = all(c.passed for c in checks)
    reasons = [c.detail for c in checks if not c.passed]

    return PolicyGateResult(
        allowed=allowed,
        checks=checks,
        sender_batch_id=sender_batch_id,
        reasons=reasons,
    )


# ==============================================================================
# PAYOUT EXECUTION (WITH TIMEOUT SIMULATION)
# ==============================================================================

async def execute_payout(
    milestone_id: str,
    human_approved: bool,
    requested_amount: Optional[float] = None,
    receiver_email: Optional[str] = None,
    attempt: Optional[int] = None,
    verdict: Optional[Verdict] = None,
    simulate_lost_response: bool = False,
    is_reissue: bool = False,
    paypal_client: Optional[PayPalClient] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deterministic policy gate execution.
    The ONLY code path allowed to call a PayPal write endpoint.

    If simulate_lost_response is True:
    Performs the REAL create_payout call, but then behaves as if the response
    was lost in transit: leaves payout row as RESERVED, logs PAYPAL_RESPONSE_DISCARDED,
    and returns simulated timeout result.
    """
    # 1. Fetch milestone
    m_row = get_milestone(milestone_id, db_path=db_path)
    if not m_row:
        return {"success": False, "allowed": False, "error": f"Milestone '{milestone_id}' not found."}

    milestone = Milestone(
        id=m_row["id"],
        contract_id=m_row["contract_id"],
        title=m_row["title"],
        amount=m_row["amount"],
        contractor_email=m_row["contractor_email"],
        acceptance_json=m_row["acceptance_json"],
    )

    # 2. Fetch latest verdict if not provided
    if verdict is None:
        v_event = get_latest_verdict_for_milestone(milestone_id, db_path=db_path)
        if v_event and v_event.get("payload"):
            payload = v_event["payload"]
            verdict = Verdict.model_validate(payload)

    # 3. Defaults
    req_amount = requested_amount if requested_amount is not None else milestone.amount
    rec_email = receiver_email if receiver_email is not None else milestone.contractor_email
    att = attempt if attempt is not None else (get_latest_payout_attempt(milestone_id, db_path=db_path) + 1)
    active = get_active_payout_for_milestone(milestone_id, db_path=db_path)

    # 4. Gate evaluation
    gate_result = evaluate_payout(
        milestone=milestone,
        verdict=verdict,
        requested_amount=req_amount,
        human_approved=human_approved,
        receiver_email=rec_email,
        attempt=att,
        active_payout=active,
        is_reissue=is_reissue,
    )

    # a) If not allowed, record GATE_EVALUATED and return without touching PayPal
    if not gate_result.allowed:
        record_event(
            type="GATE_EVALUATED",
            milestone_id=milestone.id,
            payload={
                "allowed": False,
                "checks": [c.model_dump() for c in gate_result.checks],
                "sender_batch_id": gate_result.sender_batch_id,
            },
            db_path=db_path,
        )
        has_active_failure = any(c.name == "no_active_payout" and not c.passed for c in gate_result.checks)
        err_msg = "already in progress or paid" if has_active_failure else "Policy gate rejected payout execution."
        return {
            "success": False,
            "allowed": False,
            "gate_result": gate_result.model_dump(),
            "error": err_msg,
        }

    # Record passing gate evaluation
    record_event(
        type="GATE_EVALUATED",
        milestone_id=milestone.id,
        payload={
            "allowed": True,
            "checks": [c.model_dump() for c in gate_result.checks],
            "sender_batch_id": gate_result.sender_batch_id,
        },
        db_path=db_path,
    )

    # b) ATOMICALLY insert a payouts row with status RESERVED
    try:
        payout_id = create_payout_reserved(
            milestone_id=milestone.id,
            attempt=att,
            sender_batch_id=gate_result.sender_batch_id,
            receiver_email=rec_email,
            amount=req_amount,
            db_path=db_path,
        )
    except (ActivePayoutExistsError, sqlite3.IntegrityError) as e:
        return {
            "success": False,
            "allowed": False,
            "error": "already in progress or paid",
            "gate_result": gate_result.model_dump(),
            "detail": str(e),
        }

    record_event(
        type="PAYOUT_RESERVED",
        milestone_id=milestone.id,
        payout_id=payout_id,
        payload={
            "sender_batch_id": gate_result.sender_batch_id,
            "receiver_email": rec_email,
            "amount": req_amount,
            "status": "RESERVED",
        },
        db_path=db_path,
    )

    # c) Only then call PayPal create_payout
    client = paypal_client or default_paypal_client
    try:
        paypal_res = await client.create_payout(
            sender_batch_id=gate_result.sender_batch_id,
            receiver_email=rec_email,
            amount=req_amount,
            note=f"Payment for milestone: {milestone.title}",
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        record_event(
            type="PAYOUT_ERROR",
            milestone_id=milestone.id,
            payout_id=payout_id,
            payload={
                "error_type": "TIMEOUT_OR_NETWORK",
                "detail": str(exc),
                "sender_batch_id": gate_result.sender_batch_id,
            },
            db_path=db_path,
        )
        return {
            "success": False,
            "payout_id": payout_id,
            "status": "RESERVED",
            "error": f"Timeout or network error: {exc}. Row status kept as RESERVED. Reconciliation required.",
            "reconcile_required": True,
            "sender_batch_id": gate_result.sender_batch_id,
        }
    except DuplicateBatchError as exc:
        record_event(
            type="PAYOUT_ERROR",
            milestone_id=milestone.id,
            payout_id=payout_id,
            payload={
                "error_type": "DUPLICATE_BATCH",
                "name": exc.name,
                "detail": exc.message,
                "original_batch_id": exc.original_batch_id,
                "original_batch_link": exc.original_batch_link,
            },
            db_path=db_path,
        )
        raise exc
    except PayPalError as exc:
        update_payout(payout_id, status="FAILED", db_path=db_path)
        record_event(
            type="PAYOUT_ERROR",
            milestone_id=milestone.id,
            payout_id=payout_id,
            payload={
                "error_type": "PAYPAL_ERROR",
                "name": exc.name,
                "detail": exc.message,
                "debug_id": exc.debug_id,
            },
            db_path=db_path,
        )
        raise exc

    real_batch_id = paypal_res.get("batch_id")

    # TIMEOUT SIMULATION (Primary Demo Path)
    if simulate_lost_response:
        record_event(
            type="PAYOUT_ERROR",
            milestone_id=milestone.id,
            payout_id=payout_id,
            payload={
                "reason": "simulated_lost_response",
                "detail": "Simulated timeout: response discarded after real PayPal payout submission",
            },
            db_path=db_path,
        )
        record_event(
            type="PAYPAL_RESPONSE_DISCARDED",
            milestone_id=milestone.id,
            payout_id=payout_id,
            payload={
                "payout_batch_id": real_batch_id,
                "sender_batch_id": gate_result.sender_batch_id,
                "simulated": True,
            },
            db_path=db_path,
        )
        check_and_open_cases(milestone.id, db_path=db_path)

        return {
            "success": False,
            "payout_id": payout_id,
            "status": "RESERVED",
            "simulated_lost_response": True,
            "error": "Simulated lost response after real PayPal call",
            "reconcile_required": True,
            "sender_batch_id": gate_result.sender_batch_id,
        }

    # Standard success path: call get_payout_batch and persist items[0].payout_item_id
    batch_status = paypal_res.get("batch_status") or "PENDING"
    payout_item_id = paypal_res.get("payout_item_id")
    item_status = None

    if real_batch_id:
        try:
            batch_details = await client.get_payout_batch(real_batch_id)
            items = batch_details.get("items", [])
            if items and isinstance(items, list) and isinstance(items[0], dict):
                payout_item_id = items[0].get("payout_item_id") or payout_item_id
                item_status = items[0].get("transaction_status")
        except Exception:
            pass

    status_to_store = item_status or batch_status

    update_payout(
        payout_id=payout_id,
        status=status_to_store,
        payout_batch_id=real_batch_id,
        payout_item_id=payout_item_id,
        db_path=db_path,
    )

    record_event(
        type="PAYOUT_SENT",
        milestone_id=milestone.id,
        payout_id=payout_id,
        payload={
            "sender_batch_id": gate_result.sender_batch_id,
            "payout_batch_id": real_batch_id,
            "payout_item_id": payout_item_id,
            "status": status_to_store,
        },
        db_path=db_path,
    )

    return {
        "success": True,
        "allowed": True,
        "payout_id": payout_id,
        "sender_batch_id": gate_result.sender_batch_id,
        "payout_batch_id": real_batch_id,
        "payout_item_id": payout_item_id,
        "status": status_to_store,
        "gate_result": gate_result.model_dump(),
    }


# ==============================================================================
# RETRY ENDPOINT LOGIC (NO AUTOMATIC ATTEMPT INCREMENT)
# ==============================================================================

async def retry_payout(
    payout_id: int,
    human_approved: bool,
    paypal_client: Optional[PayPalClient] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retry a RESERVED payout using the EXACT SAME sender_batch_id.
    Never generates a new attempt number or new sender_batch_id automatically.
    """
    if not human_approved:
        raise ValueError("Human approval is required to retry payout execution.")

    payout = get_payout(payout_id, db_path=db_path)
    if not payout:
        raise ValueError(f"Payout ID {payout_id} not found.")

    if payout["status"] != "RESERVED":
        raise ValueError(
            f"Cannot retry payout {payout_id}: current status is '{payout['status']}', but must be RESERVED."
        )

    milestone_row = get_milestone(payout["milestone_id"], db_path=db_path)
    if not milestone_row:
        raise ValueError(f"Milestone '{payout['milestone_id']}' not found.")

    client = paypal_client or default_paypal_client
    sender_batch_id = payout["sender_batch_id"]

    try:
        res = await client.create_payout(
            sender_batch_id=sender_batch_id,
            receiver_email=payout["receiver_email"],
            amount=payout["amount"],
            note=f"Retry payment for milestone: {milestone_row['title']}",
        )
        batch_id = res.get("batch_id")
        item_id = res.get("payout_item_id")
        item_status = None

        if batch_id:
            try:
                batch_data = await client.get_payout_batch(batch_id)
                items = batch_data.get("items", [])
                if items and isinstance(items, list) and isinstance(items[0], dict):
                    item_id = items[0].get("payout_item_id") or item_id
                    item_status = items[0].get("transaction_status")
            except Exception:
                pass

        final_status = item_status or res.get("batch_status") or "SUCCESS"
        update_payout(
            payout_id=payout_id,
            status=final_status,
            payout_batch_id=batch_id,
            payout_item_id=item_id,
            db_path=db_path,
        )
        record_event(
            type="PAYOUT_RECONCILED",
            milestone_id=milestone_row["id"],
            payout_id=payout_id,
            payload={
                "payout_batch_id": batch_id,
                "payout_item_id": item_id,
                "status": final_status,
                "recovery_method": "idempotent_replay",
            },
            db_path=db_path,
        )
        return {
            "recovered": True,
            "item_status": final_status,
            "payout_batch_id": batch_id,
            "payout_id": payout_id,
            "payout_item_id": item_id,
            "recovery_method": "idempotent_replay",
        }

    except DuplicateBatchError as exc:
        if exc.original_batch_id:
            batch_data = await client.get_payout_batch(exc.original_batch_id)
            batch_header = batch_data.get("batch_header", {})
            batch_id = batch_header.get("payout_batch_id", exc.original_batch_id)
            items = batch_data.get("items", [])
            item_id = None
            item_status = None
            if items and isinstance(items, list) and isinstance(items[0], dict):
                item_id = items[0].get("payout_item_id")
                item_status = items[0].get("transaction_status")

            final_status = item_status or batch_header.get("batch_status") or "SUCCESS"
            update_payout(
                payout_id=payout_id,
                status=final_status,
                payout_batch_id=batch_id,
                payout_item_id=item_id,
                db_path=db_path,
            )
            record_event(
                type="PAYOUT_RECONCILED",
                milestone_id=milestone_row["id"],
                payout_id=payout_id,
                payload={
                    "payout_batch_id": batch_id,
                    "payout_item_id": item_id,
                    "status": final_status,
                    "recovery_method": "duplicate_batch_error",
                },
                db_path=db_path,
            )
            return {
                "recovered": True,
                "item_status": final_status,
                "payout_batch_id": batch_id,
                "payout_id": payout_id,
                "payout_item_id": item_id,
                "recovery_method": "duplicate_batch_error",
            }
        else:
            record_event(
                type="PAYOUT_ERROR",
                milestone_id=milestone_row["id"],
                payout_id=payout_id,
                payload={"error_type": "DUPLICATE_BATCH_NO_ID", "detail": exc.message},
                db_path=db_path,
            )
            return {
                "recovered": False,
                "status": "RESERVED",
                "error": exc.message,
            }

    except PayPalError as exc:
        record_event(
            type="PAYOUT_ERROR",
            milestone_id=milestone_row["id"],
            payout_id=payout_id,
            payload={
                "error_type": "PAYPAL_RETRY_ERROR",
                "status_code": exc.status_code,
                "name": exc.name,
                "detail": exc.message,
                "debug_id": exc.debug_id,
            },
            db_path=db_path,
        )
        return {
            "recovered": False,
            "status": "RESERVED",
            "error": exc.message,
            "status_code": exc.status_code,
            "name": exc.name,
            "debug_id": exc.debug_id,
        }


# ==============================================================================
# RECONCILE & SYNC
# ==============================================================================

async def reconcile_payout(
    payout_id: int,
    paypal_client: Optional[PayPalClient] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Reconcile a payout whose network call timed out or was interrupted.
    Calls get_payout_batch using sender_batch_id (or payout_batch_id) before any retry.
    """
    payout = get_payout(payout_id, db_path=db_path)
    if not payout:
        raise ValueError(f"Payout ID {payout_id} not found.")

    client = paypal_client or default_paypal_client
    lookup_id = payout.get("payout_batch_id") or payout.get("sender_batch_id")

    batch_data = await client.get_payout_batch(lookup_id)
    batch_header = batch_data.get("batch_header", {})
    new_status = batch_header.get("batch_status", payout["status"])
    batch_id = batch_header.get("payout_batch_id", payout.get("payout_batch_id"))

    payout_item_id = payout.get("payout_item_id")
    items = batch_data.get("items", [])
    if items and isinstance(items, list) and isinstance(items[0], dict):
        payout_item_id = items[0].get("payout_item_id") or payout_item_id
        if items[0].get("transaction_status"):
            new_status = items[0].get("transaction_status")

    update_payout(
        payout_id=payout_id,
        status=new_status,
        payout_batch_id=batch_id,
        payout_item_id=payout_item_id,
        db_path=db_path,
    )

    record_event(
        type="PAYOUT_STATUS_SYNCED",
        milestone_id=payout["milestone_id"],
        payout_id=payout_id,
        payload={
            "action": "RECONCILE",
            "payout_batch_id": batch_id,
            "payout_item_id": payout_item_id,
            "status": new_status,
        },
        db_path=db_path,
    )

    return get_payout(payout_id, db_path=db_path)


async def sync_payout(
    payout_id: int,
    dispute_message: Optional[str] = None,
    paypal_client: Optional[PayPalClient] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Status sync:
    GET batch/item from PayPal, update payouts.status, record PAYOUT_STATUS_SYNCED with PayPal IDs.
    Requirement: sync_payout must store the ITEM status, not the batch status.
    """
    payout = get_payout(payout_id, db_path=db_path)
    if not payout:
        raise ValueError(f"Payout ID {payout_id} not found.")

    client = paypal_client or default_paypal_client
    old_status = payout["status"]
    paypal_status = old_status

    if payout.get("payout_item_id"):
        item_data = await client.get_payout_item(payout["payout_item_id"])
        paypal_status = item_data.get("transaction_status") or item_data.get("status") or old_status
    elif payout.get("payout_batch_id"):
        batch_data = await client.get_payout_batch(payout["payout_batch_id"])
        items = batch_data.get("items", [])
        if items and isinstance(items, list) and isinstance(items[0], dict):
            if items[0].get("payout_item_id"):
                update_payout(payout_id, payout_item_id=items[0]["payout_item_id"], db_path=db_path)
            if items[0].get("transaction_status"):
                paypal_status = items[0]["transaction_status"]
            elif items[0].get("status"):
                paypal_status = items[0]["status"]
        else:
            paypal_status = old_status

    update_payout(payout_id, status=paypal_status, db_path=db_path)

    record_event(
        type="PAYOUT_STATUS_SYNCED",
        milestone_id=payout["milestone_id"],
        payout_id=payout_id,
        payload={
            "old_status": old_status,
            "new_status": paypal_status,
            "payout_batch_id": payout.get("payout_batch_id"),
            "payout_item_id": payout.get("payout_item_id"),
        },
        db_path=db_path,
    )

    # Check and open cases if needed
    check_and_open_cases(payout["milestone_id"], db_path=db_path, live_item_status=paypal_status)

    flags: List[str] = []
    if old_status != paypal_status:
        flags.append(f"Status mismatch: local status '{old_status}' diverges from live PayPal status '{paypal_status}'.")
    if paypal_status in ("UNCLAIMED", "ONHOLD", "BLOCKED"):
        flags.append(f"Payout is {paypal_status}: funds not accepted or under hold.")
    if dispute_message and any(k in dispute_message.lower() for k in ["not paid", "unpaid", "missing payment", "did not receive"]):
        flags.append(f"Received payee non-receipt notice: '{dispute_message}'")

    flagged = len(flags) > 0

    return {
        "payout_id": payout_id,
        "old_status": old_status,
        "new_status": paypal_status,
        "flagged": flagged,
        "flags": flags,
        "payout": get_payout(payout_id, db_path=db_path),
    }


# ==============================================================================
# CANCEL AND REISSUE (HARDENED)
# ==============================================================================

async def execute_cancel_and_reissue(
    payout_item_id: str,
    new_email: str,
    human_approved: bool,
    new_email_verified: bool,
    paypal_client: Optional[PayPalClient] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cancel an unclaimed payout item and reissue to a new verified email.
    Hardened against sandbox 500 errors:
    - Retries cancel with backoff
    - If cancel still fails, returns {success: False, cancel_unavailable: True, ...}
      and logs ITEM_CANCEL_FAILED. Does NOT change contract email or reissue.
    - Contract email changes ONLY after the new payout succeeds.
    - If reissue fails after successful cancel, logs PAYOUT_REISSUE_FAILED.
    - Attempt number computed from DB (max attempt + 1).
    """
    client = paypal_client or default_paypal_client

    # 1. Look up payout
    payout = get_payout_by_item_id(payout_item_id, db_path=db_path)
    if not payout and str(payout_item_id).isdigit():
        payout = get_payout(int(payout_item_id), db_path=db_path)
        if payout and payout.get("payout_item_id"):
            payout_item_id = payout["payout_item_id"]

    if not payout:
        raise ValueError(f"Payout record for item '{payout_item_id}' not found.")

    milestone_row = get_milestone(payout["milestone_id"], db_path=db_path)
    if not milestone_row:
        raise ValueError(f"Milestone '{payout['milestone_id']}' not found.")

    # 2. Gate Checks
    if not human_approved:
        raise ValueError("Human approval is required to cancel and reissue payout.")

    if not new_email_verified:
        raise ValueError("New contractor email must be verified before reissue.")

    # Live check on PayPal item
    item_details = await client.get_payout_item(payout_item_id)
    live_status = item_details.get("transaction_status") or item_details.get("status")
    if live_status != "UNCLAIMED":
        raise ValueError(
            f"Cannot cancel payout item '{payout_item_id}': status is '{live_status}', but must be UNCLAIMED."
        )

    # Gate check: batch_processed (batch status == SUCCESS)
    batch_id = payout.get("payout_batch_id") or item_details.get("payout_batch_id") or payout.get("sender_batch_id")
    batch_status = None
    if batch_id:
        batch_details = await client.get_payout_batch(batch_id)
        batch_header = batch_details.get("batch_header", {})
        batch_status = batch_header.get("batch_status")

    if batch_status != "SUCCESS":
        return {
            "success": False,
            "retry_shortly": True,
            "error": f"Payout batch '{batch_id}' is still processing (batch_status='{batch_status}'). Please retry shortly once batch status reaches SUCCESS.",
            "gate_check": "batch_processed",
            "batch_status": batch_status,
            "item_status": live_status,
        }

    if abs(float(payout["amount"]) - float(milestone_row["amount"])) >= 0.001:
        raise ValueError("Payout amount does not match milestone nominal value.")

    # 3. Call cancel_payout_item (handles retries on 5xx)
    try:
        cancel_res = await client.cancel_payout_item(payout_item_id)
    except PayPalError as exc:
        # Cancel failed (e.g. 500 error in sandbox)
        record_event(
            type="ITEM_CANCEL_FAILED",
            milestone_id=milestone_row["id"],
            payout_id=payout["id"],
            payload={
                "payout_item_id": payout_item_id,
                "status_code": exc.status_code,
                "paypal_name": exc.name,
                "debug_id": exc.debug_id,
                "error": exc.message,
            },
            db_path=db_path,
        )
        return {
            "success": False,
            "cancel_unavailable": True,
            "status_code": exc.status_code,
            "paypal_name": exc.name,
            "debug_id": exc.debug_id,
            "error": f"PayPal cancel service unavailable: {exc.message}",
        }

    # 4. Sync old payout to RETURNED
    update_payout(payout["id"], status="RETURNED", db_path=db_path)
    record_event(
        type="ITEM_CANCELLED",
        milestone_id=milestone_row["id"],
        payout_id=payout["id"],
        payload={
            "payout_item_id": payout_item_id,
            "status": "RETURNED",
            "cancel_response": cancel_res,
        },
        db_path=db_path,
    )

    # 5. Compute attempt number from DB: max attempt for that milestone + 1
    next_attempt = get_latest_payout_attempt(milestone_row["id"], db_path=db_path) + 1

    # 6. Execute NEW payout to new_email with is_reissue=True
    try:
        new_payout_res = await execute_payout(
            milestone_id=milestone_row["id"],
            human_approved=True,
            requested_amount=milestone_row["amount"],
            receiver_email=new_email,
            attempt=next_attempt,
            is_reissue=True,
            paypal_client=client,
            db_path=db_path,
        )
    except Exception as exc:
        record_event(
            type="PAYOUT_REISSUE_FAILED",
            milestone_id=milestone_row["id"],
            payout_id=payout["id"],
            payload={"error": str(exc), "new_email": new_email, "attempt": next_attempt},
            db_path=db_path,
        )
        return {
            "success": False,
            "reissue_failed": True,
            "old_payout_id": payout["id"],
            "old_status": "RETURNED",
            "error": f"Reissue failed: {exc}",
        }

    if not new_payout_res.get("success"):
        record_event(
            type="PAYOUT_REISSUE_FAILED",
            milestone_id=milestone_row["id"],
            payout_id=payout["id"],
            payload={"detail": new_payout_res, "new_email": new_email, "attempt": next_attempt},
            db_path=db_path,
        )
        return {
            "success": False,
            "reissue_failed": True,
            "old_payout_id": payout["id"],
            "old_status": "RETURNED",
            "detail": new_payout_res,
        }

    # 7. Update contract email ONLY after the NEW payout is created successfully
    update_milestone_contractor_email(milestone_row["id"], new_email, db_path=db_path)

    record_event(
        type="PAYOUT_REISSUED",
        milestone_id=milestone_row["id"],
        payout_id=new_payout_res.get("payout_id"),
        payload={
            "cancelled_payout_id": payout["id"],
            "cancelled_item_id": payout_item_id,
            "new_email": new_email,
            "new_payout_id": new_payout_res.get("payout_id"),
            "new_sender_batch_id": new_payout_res.get("sender_batch_id"),
            "attempt": next_attempt,
        },
        db_path=db_path,
    )

    return {
        "success": True,
        "cancelled_payout_id": payout["id"],
        "cancelled_item_id": payout_item_id,
        "old_status": "RETURNED",
        "new_email": new_email,
        "new_payout": new_payout_res,
    }


# ==============================================================================
# DETERMINISTIC CASE OPENING & INVESTIGATOR EVIDENCE BUNDLE
# ==============================================================================

def check_and_open_cases(
    milestone_id: str,
    db_path: Optional[str] = None,
    live_item_status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Deterministic rule-based case opening (no AI):
    Open a case when ANY of:
    - local payout status != live PayPal ITEM status
    - item status is UNCLAIMED / ONHOLD / BLOCKED / RETURNED
    - payout stuck RESERVED
    - an inbound message exists after a payout
    """
    payouts = get_payouts_for_milestone(milestone_id, db_path=db_path)
    messages = get_inbound_messages_for_milestone(milestone_id, db_path=db_path)
    existing_cases = get_cases_for_milestone(milestone_id, db_path=db_path)
    existing_reasons = {c["reason"] for c in existing_cases if c["status"] == "OPEN"}

    newly_opened = []

    for p in payouts:
        p_status = p["status"]

        # Rule 1: Stuck RESERVED
        if p_status == "RESERVED":
            reason = "Payout stuck in RESERVED status (potential lost response or interrupted delivery)"
            if reason not in existing_reasons:
                c = create_case(milestone_id, reason=reason, db_path=db_path)
                newly_opened.append(c)
                existing_reasons.add(reason)

        # Rule 2: Exception status
        curr_status = live_item_status or p_status
        if curr_status in ("UNCLAIMED", "ONHOLD", "BLOCKED", "RETURNED"):
            reason = f"Payout item in exception status: {curr_status}"
            if reason not in existing_reasons:
                c = create_case(milestone_id, reason=reason, db_path=db_path)
                newly_opened.append(c)
                existing_reasons.add(reason)

        # Rule 3: Status mismatch
        if live_item_status and p_status != live_item_status:
            reason = f"Local status '{p_status}' diverges from live PayPal item status '{live_item_status}'"
            if reason not in existing_reasons:
                c = create_case(milestone_id, reason=reason, db_path=db_path)
                newly_opened.append(c)
                existing_reasons.add(reason)

    # Rule 4: Inbound message exists after payout
    if payouts and messages:
        reason = "Inbound payee inquiry received regarding milestone payout"
        if reason not in existing_reasons:
            c = create_case(milestone_id, reason=reason, db_path=db_path)
            newly_opened.append(c)
            existing_reasons.add(reason)

    return newly_opened


async def get_case_evidence_bundle(
    milestone_id: str,
    paypal_client: Optional[PayPalClient] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Constructs the ONE unified JSON evidence bundle for the AI investigator:
    - contract email, milestone amount
    - all payouts rows
    - LIVE item and batch status fetched from PayPal at call time (with PayPal IDs)
    - events timeline
    - inbound messages
    - deterministic diversion risk & Levenshtein edit distance
    - allowed_actions list computed deterministically (NEVER includes plain resend)
    """
    m_row = get_milestone(milestone_id, db_path=db_path)
    if not m_row:
        raise ValueError(f"Milestone '{milestone_id}' not found.")

    contract_email = m_row["contractor_email"]
    milestone_amount = m_row["amount"]

    payouts = get_payouts_for_milestone(milestone_id, db_path=db_path)
    events = get_events_for_milestone(milestone_id, db_path=db_path)
    inbound_messages = get_inbound_messages_for_milestone(milestone_id, db_path=db_path)

    client = paypal_client or default_paypal_client

    # Fetch live statuses from PayPal for each payout
    live_statuses = []
    latest_live_item_status = None
    latest_live_batch_status = None
    latest_live_email = None

    for p in payouts:
        live_item_status = p["status"]
        live_batch_status = None
        live_item_id = p.get("payout_item_id")
        live_batch_id = p.get("payout_batch_id")

        if live_item_id:
            try:
                item_data = await client.get_payout_item(live_item_id)
                live_item_status = item_data.get("transaction_status") or item_data.get("status") or live_item_status
                payout_item_obj = item_data.get("payout_item", {})
                if isinstance(payout_item_obj, dict) and payout_item_obj.get("receiver"):
                    latest_live_email = payout_item_obj.get("receiver")
            except Exception:
                pass

        if live_batch_id:
            try:
                batch_data = await client.get_payout_batch(live_batch_id)
                batch_header = batch_data.get("batch_header", {})
                live_batch_status = batch_header.get("batch_status")
            except Exception:
                pass

        live_statuses.append({
            "payout_id": p["id"],
            "attempt": p["attempt"],
            "sender_batch_id": p["sender_batch_id"],
            "payout_batch_id": live_batch_id,
            "payout_item_id": live_item_id,
            "local_status": p["status"],
            "live_item_status": live_item_status,
            "live_batch_status": live_batch_status,
        })
        latest_live_item_status = live_item_status
        latest_live_batch_status = live_batch_status

    # Trigger case evaluation
    check_and_open_cases(milestone_id, db_path=db_path, live_item_status=latest_live_item_status)
    cases = get_cases_for_milestone(milestone_id, db_path=db_path)

    # Deterministic diversion-risk analysis
    all_message_emails: List[str] = []
    for msg in inbound_messages:
        all_message_emails.extend(extract_emails_from_text(msg.get("body", "")))

    diversion_risk = False
    requires_out_of_band_verification = False
    for extracted_email in all_message_emails:
        if extracted_email.strip().lower() != contract_email.strip().lower():
            diversion_risk = True
            requires_out_of_band_verification = True
            break

    # Levenshtein distances
    edit_distances: Dict[str, Any] = {}
    if all_message_emails:
        primary_msg_email = all_message_emails[0]
        edit_distances["contract_vs_message"] = {
            "contract_email": contract_email,
            "message_email": primary_msg_email,
            "distance": levenshtein_distance(contract_email, primary_msg_email),
        }
        if latest_live_email:
            edit_distances["live_payout_vs_message"] = {
                "live_payout_email": latest_live_email,
                "message_email": primary_msg_email,
                "distance": levenshtein_distance(latest_live_email, primary_msg_email),
            }

    # Determine allowed_actions (subset of allowed action types, NEVER plain resend)
    allowed_actions = []

    # Check if cancel has failed previously with cancel_unavailable
    cancel_failed_earlier = any(e["type"] == "ITEM_CANCEL_FAILED" for e in events)

    # 1. mark_paid_no_resend: only if live item status is SUCCESS
    if latest_live_item_status == "SUCCESS":
        allowed_actions.append({
            "action": "mark_paid_no_resend",
            "reason": "Live PayPal payout item is confirmed SUCCESS; record paid in platform ledger without resending funds.",
        })

    # 2. cancel_and_reissue: only if item is UNCLAIMED, batch is SUCCESS, and cancel has not failed with cancel_unavailable earlier
    if (
        latest_live_item_status == "UNCLAIMED"
        and latest_live_batch_status == "SUCCESS"
        and not cancel_failed_earlier
    ):
        allowed_actions.append({
            "action": "cancel_and_reissue",
            "reason": "Payout item is UNCLAIMED and batch processing is SUCCESS; item can be safely cancelled and reissued to verified address.",
        })

    # 3. request_out_of_band_verification: whenever diversion_risk is True
    if diversion_risk:
        allowed_actions.append({
            "action": "request_out_of_band_verification",
            "reason": "Payee message specifies an email address different from contract on file (diversion risk detected).",
        })

    # 4. wait_and_resync: if status is RESERVED, PENDING, or PROCESSING
    if latest_live_item_status in ("RESERVED", "PENDING", "PROCESSING") or any(p["status"] == "RESERVED" for p in payouts):
        allowed_actions.append({
            "action": "wait_and_resync",
            "reason": "Payout delivery is still in transit or pending PayPal batch settlement; re-synchronize status shortly.",
        })

    # 5. escalate_manual: if diversion risk is true, cancel failed, or item blocked/disputed
    if diversion_risk or cancel_failed_earlier or latest_live_item_status in ("BLOCKED", "ONHOLD"):
        allowed_actions.append({
            "action": "escalate_manual",
            "reason": "Automated rail cannot safely proceed due to exception conditions; route to human compliance officer.",
        })

    return {
        "milestone_id": milestone_id,
        "contract_email": contract_email,
        "milestone_amount": milestone_amount,
        "payouts": payouts,
        "live_paypal_statuses": live_statuses,
        "events": events,
        "inbound_messages": inbound_messages,
        "diversion_risk": diversion_risk,
        "requires_out_of_band_verification": requires_out_of_band_verification,
        "extracted_message_emails": all_message_emails,
        "email_edit_distances": edit_distances,
        "cases": cases,
        "allowed_actions": allowed_actions,
    }


async def execute_case_action(
    case_id: int,
    action: str,
    human_approved: bool,
    new_email: Optional[str] = None,
    new_email_verified: Optional[bool] = None,
    paypal_client: Optional[PayPalClient] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes ONLY an action that is currently in allowed_actions for this milestone case.
    Rejects unauthorized or prohibited actions (e.g. plain resends).
    """
    case = get_case(case_id, db_path=db_path)
    if not case:
        raise ValueError(f"Case ID {case_id} not found.")

    milestone_id = case["milestone_id"]
    evidence = await get_case_evidence_bundle(milestone_id, paypal_client=paypal_client, db_path=db_path)
    permitted_actions = [a["action"] for a in evidence.get("allowed_actions", [])]

    if action not in permitted_actions:
        raise ValueError(
            f"Action '{action}' is not permitted by deterministic policy. "
            f"Allowed actions: {permitted_actions}"
        )

    if action == "mark_paid_no_resend":
        if not human_approved:
            raise ValueError("Human approval required to mark payout as paid.")
        payouts = get_payouts_for_milestone(milestone_id, db_path=db_path)
        if payouts:
            update_payout(payouts[-1]["id"], status="SUCCESS", db_path=db_path)
        record_event(
            type="PAYOUT_MARKED_PAID",
            milestone_id=milestone_id,
            payload={"case_id": case_id, "action": "mark_paid_no_resend", "reason": "Confirmed via PayPal live status"},
            db_path=db_path,
        )
        update_case_status(case_id, "RESOLVED", db_path=db_path)
        return {"success": True, "action": action, "case_id": case_id, "status": "RESOLVED"}

    elif action == "cancel_and_reissue":
        payouts = get_payouts_for_milestone(milestone_id, db_path=db_path)
        payout_item_id = payouts[-1].get("payout_item_id") if payouts else None
        if not payout_item_id:
            raise ValueError("No payout item found to cancel.")
        res = await execute_cancel_and_reissue(
            payout_item_id=payout_item_id,
            new_email=new_email or "",
            human_approved=human_approved,
            new_email_verified=bool(new_email_verified),
            paypal_client=paypal_client,
            db_path=db_path,
        )
        if res.get("success"):
            update_case_status(case_id, "RESOLVED", db_path=db_path)
        return res

    elif action == "request_out_of_band_verification":
        record_event(
            type="OUT_OF_BAND_VERIFICATION_REQUESTED",
            milestone_id=milestone_id,
            payload={"case_id": case_id, "reason": "Payee email diversion flag"},
            db_path=db_path,
        )
        return {"success": True, "action": action, "case_id": case_id}

    elif action == "wait_and_resync":
        payouts = get_payouts_for_milestone(milestone_id, db_path=db_path)
        if payouts:
            res = await sync_payout(payouts[-1]["id"], paypal_client=paypal_client, db_path=db_path)
            return {"success": True, "action": action, "sync_result": res}
        return {"success": True, "action": action}

    elif action == "escalate_manual":
        record_event(
            type="CASE_ESCALATED",
            milestone_id=milestone_id,
            payload={"case_id": case_id, "reason": "Manual escalation initiated"},
            db_path=db_path,
        )
        update_case_status(case_id, "ESCALATED_MANUAL", db_path=db_path)
        return {"success": True, "action": action, "case_id": case_id, "status": "ESCALATED_MANUAL"}

    raise ValueError(f"Unhandled action '{action}'.")
