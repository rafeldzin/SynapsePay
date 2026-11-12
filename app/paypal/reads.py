"""PayPal reads (PRD 7.1, 8). PAYPAL_MODE=fake answers from paypal/fake.py."""
from app.config import get_settings
from app.contracts import BatchView, ItemView
from app.paypal import http


def _fake():
    if get_settings().paypal_mode == "fake":
        from app.paypal.fake import active_fake
        return active_fake()
    return None


def get_batch(payout_batch_id: str) -> BatchView:
    if (fake := _fake()) is not None:
        return fake.get_batch(payout_batch_id)
    return http.parse_batch(http.get_http().read(f"/v1/payments/payouts/{payout_batch_id}"))


def get_item(payout_item_id: str) -> ItemView:
    if (fake := _fake()) is not None:
        return fake.get_item(payout_item_id)
    return http.parse_item(http.get_http().read(f"/v1/payments/payouts-item/{payout_item_id}"))
