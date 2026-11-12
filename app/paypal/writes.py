"""PayPal write calls (PRD 7.1, 8.2, 8.3, 8.6). Imported only by app/gate/executor.py (INV-2).

Every function sends exactly one request. Nothing here retries.
"""
from app.config import get_settings
from app.contracts import CreateResult, ItemView
from app.paypal import http

EMAIL_SUBJECT = "Payment from Lumen Studio"
EMAIL_MESSAGE = "Your milestone was approved and paid."


def _fake():
    if get_settings().paypal_mode == "fake":
        from app.paypal.fake import active_fake
        return active_fake()
    return None


def create_payout(*, sender_batch_id: str, receiver_email: str, amount_cents: int,
                  currency: str, note: str) -> CreateResult:
    """One batch with one item (PRD 8.2). `PayPal-Request-Id` carries the sender_batch_id."""
    if (fake := _fake()) is not None:
        return fake.create_payout(sender_batch_id=sender_batch_id, receiver_email=receiver_email,
                                  amount_cents=amount_cents, currency=currency, note=note)
    body = {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": EMAIL_SUBJECT,
            "email_message": EMAIL_MESSAGE,
        },
        "items": [{
            "recipient_type": "EMAIL",
            "amount": {"value": http.cents_to_amount(amount_cents), "currency": currency},
            "receiver": receiver_email,
            "note": note,
            "sender_item_id": f"{sender_batch_id}-I1",
        }],
    }
    try:
        data = http.get_http().write("POST", "/v1/payments/payouts", json=body,
                                     headers={"PayPal-Request-Id": sender_batch_id})
    except http.PayPalRejected as exc:
        original = http.duplicate_original_batch_id(exc.body) if exc.status_code == 400 else None
        if original is None:
            raise
        return CreateResult(outcome="ORIGINAL_FOUND", payout_batch_id=original,
                            batch_status=None, raw=exc.body)
    header = data.get("batch_header") or {}
    if not header.get("payout_batch_id"):
        raise http.PayPalOutcomeUnknown("2xx create response without payout_batch_id")
    return CreateResult(outcome="CREATED", payout_batch_id=header["payout_batch_id"],
                        batch_status=header.get("batch_status"), raw=data)


def cancel_item(payout_item_id: str) -> ItemView:
    """Cancels an UNCLAIMED item. A success carries transaction_status RETURNED (PRD 8.6)."""
    if (fake := _fake()) is not None:
        return fake.cancel_item(payout_item_id)
    data = http.get_http().write("POST", f"/v1/payments/payouts-item/{payout_item_id}/cancel", json={})
    return http.parse_item(data)
