"""FakePayPal (UAT 1.4): the PayPal seam of PRD 7.1 for tests and PAYPAL_MODE=fake.

It behaves as Gate 0 observed the real sandbox (docs/gate0_result.json): a new batch is PENDING,
`advance()` settles it (SUCCESS for a registered receiver, UNCLAIMED otherwise), a duplicate
sender_batch_id never creates a second batch, and cancel works only on an UNCLAIMED item.
Its raw payloads use the real response shape, so paypal/http.py parses them unchanged.
"""
import threading

from app.contracts import BatchView, CreateResult, ItemView
from app.paypal.http import (PayPalOutcomeUnknown, PayPalRejected, cents_to_amount,
                             parse_batch, parse_item)

DEFAULT_REGISTERED = ("dewi.lestari@example.test",)
FAULT_KINDS = ("unknown_before_send", "unknown_after_send", "reject_400", "read_error")


class FakePayPal:
    def __init__(self, registered_emails=DEFAULT_REGISTERED, duplicate_mode: str = "reject"):
        self.registered_emails = {e.lower() for e in registered_emails}
        self.duplicate_mode = duplicate_mode  # "reject" or "replay"
        self.batches: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self._by_sender: dict[str, str] = {}
        self._faults: list[tuple[str, str]] = []
        self._n = 0
        self._lock = threading.RLock()

    # ---- test helpers --------------------------------------------------

    @property
    def write_calls(self) -> list[tuple[str, dict]]:
        return [c for c in self.calls if c[0] in ("create_payout", "cancel_item")]

    def fail_next(self, kind: str, on: str | None = None) -> None:
        """Arms one failure. Create kinds hit create_payout, read_error hits the next read.
        Pass on="cancel_item" to aim an unknown_* or reject_400 failure at the cancel call."""
        if kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind {kind!r}")
        self._faults.append((kind, on or ("read" if kind == "read_error" else "create_payout")))

    def advance(self) -> None:
        """Settles every PENDING batch."""
        with self._lock:
            for batch in self.batches.values():
                if batch["batch_status"] != "PENDING":
                    continue
                batch["batch_status"] = "SUCCESS"
                for item in batch["items"]:
                    if item["transaction_status"] == "PENDING":
                        item["transaction_status"] = (
                            "SUCCESS" if item["receiver"].lower() in self.registered_emails else "UNCLAIMED")

    def set_item_status(self, item_id: str, status: str) -> None:
        with self._lock:
            self._find_item(item_id)["transaction_status"] = status

    def claim(self, item_id: str) -> None:
        self.set_item_status(item_id, "SUCCESS")

    # ---- internals -----------------------------------------------------

    def _take_fault(self, name: str) -> str | None:
        for i, (kind, on) in enumerate(self._faults):
            if on == name:
                del self._faults[i]
                return kind
        return None

    def _find_item(self, item_id: str) -> dict:
        for batch in self.batches.values():
            for item in batch["items"]:
                if item["payout_item_id"] == item_id:
                    return item
        raise PayPalRejected("INVALID_RESOURCE_ID", "fake", {"name": "INVALID_RESOURCE_ID"}, 404)

    def _item_raw(self, item: dict, with_sender: bool = False) -> dict:
        status = item["transaction_status"]
        raw = {
            "payout_item_id": item["payout_item_id"],
            "transaction_status": status,
            "payout_batch_id": item["payout_batch_id"],
            "payout_item": {"recipient_type": "EMAIL",
                            "amount": {"currency": item["currency"], "value": cents_to_amount(item["amount_cents"])},
                            "note": item["note"], "receiver": item["receiver"],
                            "sender_item_id": item["sender_item_id"]},
        }
        if status == "UNCLAIMED":
            raw["errors"] = {"name": "RECEIVER_UNREGISTERED"}
        if with_sender:
            raw["sender_batch_id"] = self.batches[item["payout_batch_id"]]["sender_batch_id"]
        return raw

    def _batch_raw(self, batch: dict) -> dict:
        return {"batch_header": {"payout_batch_id": batch["payout_batch_id"],
                                 "batch_status": batch["batch_status"],
                                 "sender_batch_header": {"sender_batch_id": batch["sender_batch_id"]}},
                "items": [self._item_raw(i) for i in batch["items"]]}

    # ---- the seam ------------------------------------------------------

    def create_payout(self, *, sender_batch_id: str, receiver_email: str, amount_cents: int,
                      currency: str, note: str) -> CreateResult:
        with self._lock:
            self.calls.append(("create_payout", dict(
                sender_batch_id=sender_batch_id, receiver_email=receiver_email,
                amount_cents=amount_cents, currency=currency, note=note)))
            fault = self._take_fault("create_payout")
            if fault == "unknown_before_send":
                raise PayPalOutcomeUnknown("fake: lost before send")
            if fault == "reject_400":
                raise PayPalRejected("VALIDATION_ERROR", "fake-debug-id", {"name": "VALIDATION_ERROR"}, 400)

            existing = self._by_sender.get(sender_batch_id)
            if existing:  # never a second batch for the same sender_batch_id
                batch = self.batches[existing]
                if self.duplicate_mode == "replay":
                    result = CreateResult(outcome="CREATED", payout_batch_id=existing,
                                          batch_status=batch["batch_status"], raw=self._batch_raw(batch))
                else:
                    body = {"name": "USER_BUSINESS_ERROR", "details": [{
                        "field": "SENDER_BATCH_ID", "issue": "Batch with given sender_batch_id already exists",
                        "link": [{"href": f"https://api.sandbox.paypal.com/v1/payments/payouts/{existing}",
                                  "rel": "self", "method": "GET"}]}], "links": []}
                    result = CreateResult(outcome="ORIGINAL_FOUND", payout_batch_id=existing,
                                          batch_status=None, raw=body)
            else:
                self._n += 1
                batch_id, item_id = f"FAKEB{self._n:04d}", f"FAKEI{self._n:04d}"
                self.batches[batch_id] = {
                    "payout_batch_id": batch_id, "sender_batch_id": sender_batch_id,
                    "batch_status": "PENDING",
                    "items": [{"payout_item_id": item_id, "payout_batch_id": batch_id,
                               "transaction_status": "PENDING", "receiver": receiver_email,
                               "amount_cents": amount_cents, "currency": currency, "note": note,
                               "sender_item_id": f"{sender_batch_id}-I1"}]}
                self._by_sender[sender_batch_id] = batch_id
                result = CreateResult(outcome="CREATED", payout_batch_id=batch_id, batch_status="PENDING",
                                      raw=self._batch_raw(self.batches[batch_id]))
            if fault == "unknown_after_send":
                raise PayPalOutcomeUnknown("fake: response lost after send")
            return result

    def cancel_item(self, payout_item_id: str) -> ItemView:
        with self._lock:
            self.calls.append(("cancel_item", {"payout_item_id": payout_item_id}))
            fault = self._take_fault("cancel_item")
            if fault == "unknown_before_send":
                raise PayPalOutcomeUnknown("fake: lost before send")
            if fault == "reject_400":
                raise PayPalRejected("VALIDATION_ERROR", "fake-debug-id", {"name": "VALIDATION_ERROR"}, 400)
            item = self._find_item(payout_item_id)
            if self.batches[item["payout_batch_id"]]["batch_status"] != "SUCCESS":
                raise PayPalRejected("BATCH_NOT_COMPLETED", "fake-debug-id", {"name": "BATCH_NOT_COMPLETED"}, 400)
            if item["transaction_status"] != "UNCLAIMED":
                raise PayPalRejected("ITEM_INCORRECT_STATUS", "fake-debug-id",
                                     {"name": "ITEM_INCORRECT_STATUS"}, 400)
            item["transaction_status"] = "RETURNED"
            view = parse_item(self._item_raw(item))
            if fault == "unknown_after_send":
                raise PayPalOutcomeUnknown("fake: response lost after cancel")
            return view

    def get_batch(self, payout_batch_id: str) -> BatchView:
        with self._lock:
            self.calls.append(("get_batch", {"payout_batch_id": payout_batch_id}))
            if self._take_fault("read"):
                raise PayPalOutcomeUnknown("fake: read error")
            batch = self.batches.get(payout_batch_id)
            if batch is None:
                raise PayPalRejected("INVALID_RESOURCE_ID", "fake", {"name": "INVALID_RESOURCE_ID"}, 404)
            return parse_batch(self._batch_raw(batch))

    def get_item(self, payout_item_id: str) -> ItemView:
        with self._lock:
            self.calls.append(("get_item", {"payout_item_id": payout_item_id}))
            if self._take_fault("read"):
                raise PayPalOutcomeUnknown("fake: read error")
            return parse_item(self._item_raw(self._find_item(payout_item_id), with_sender=True))


_fake: FakePayPal | None = None


def active_fake() -> FakePayPal:
    """The process-wide fake used by paypal.reads and paypal.writes when PAYPAL_MODE=fake."""
    global _fake
    if _fake is None:
        _fake = FakePayPal()
    return _fake


def reset_fake(**kwargs) -> FakePayPal:
    global _fake
    _fake = FakePayPal(**kwargs)
    return _fake
