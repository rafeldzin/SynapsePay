"""A2: live PayPal client against httpx.MockTransport (UAT-PP-01 to PP-09). No network."""
import json
from pathlib import Path

import httpx
import pytest

from app.paypal import http, reads, writes
from app.paypal.http import PayPalAuthError, PayPalOutcomeUnknown, PayPalRejected

GATE0 = json.loads((Path(__file__).resolve().parent.parent / "docs" / "gate0_result.json")
                   .read_text(encoding="utf-8"))
BASE = "https://api-m.sandbox.paypal.com"


class Recorder:
    """Builds a PayPalHTTP on a MockTransport and records every request."""

    def __init__(self, handler):
        self.handler, self.requests = handler, []
        transport = httpx.MockTransport(self._handle)
        self.http = http.PayPalHTTP(BASE, "id", "secret", client=httpx.Client(transport=transport))

    def _handle(self, request):
        self.requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": f"tok{self.count('/v1/oauth2/token')}",
                                             "expires_in": 32000})
        return self.handler(request)

    def count(self, path=None, method=None):
        return sum(1 for r in self.requests
                   if (path is None or r.url.path == path) and (method is None or r.method == method))

    def calls(self, method):
        return [r for r in self.requests if r.url.path != "/v1/oauth2/token" and r.method == method]


@pytest.fixture
def live(monkeypatch):
    """live(handler) -> Recorder wired in as the PayPal client, with sleeps recorded not slept."""
    monkeypatch.setenv("PAYPAL_MODE", "sandbox")
    sleeps = []
    monkeypatch.setattr(http, "_sleep", sleeps.append)

    def make(handler):
        rec = Recorder(handler)
        rec.sleeps = sleeps
        http.set_http(rec.http)
        return rec

    yield make
    http.set_http(None)


BATCH_BODY = {
    "batch_header": {"payout_batch_id": "B1", "batch_status": "SUCCESS",
                     "sender_batch_header": {"sender_batch_id": "SP-run-m1-A1"}},
    "items": [{"payout_item_id": "I1", "transaction_status": "UNCLAIMED", "payout_batch_id": "B1",
               "payout_item": {"receiver": "x@example.test", "amount": {"value": "300.00", "currency": "USD"}},
               "errors": {"name": "RECEIVER_UNREGISTERED"}}],
}


def _create(**overrides):
    kwargs = dict(sender_batch_id="SP-7f3a9c1e-m1-A1", receiver_email="dewi@example.test",
                  amount_cents=30000, currency="USD", note="Milestone m1")
    kwargs.update(overrides)
    return writes.create_payout(**kwargs)


def test_uat_pp_01_token_is_requested_once_and_reused(live):
    rec = live(lambda r: httpx.Response(200, json=BATCH_BODY))
    reads.get_batch("B1")
    reads.get_batch("B1")
    assert rec.count("/v1/oauth2/token") == 1
    assert {r.headers["Authorization"] for r in rec.calls("GET")} == {"Bearer tok1"}


def test_uat_pp_02_a_401_refreshes_the_token_once_and_repeats_the_read(live):
    def handler(request):
        if request.headers["Authorization"] == "Bearer tok1":
            return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"})
        return httpx.Response(200, json=BATCH_BODY)

    rec = live(handler)
    assert reads.get_batch("B1").payout_batch_id == "B1"
    assert rec.count("/v1/oauth2/token") == 2
    assert len(rec.calls("GET")) == 2


def test_a2_a_401_after_a_fresh_token_raises_auth_error_without_looping(live):
    rec = live(lambda r: httpx.Response(401, json={}))
    with pytest.raises(PayPalAuthError):
        reads.get_batch("B1")
    assert len(rec.calls("GET")) == 2


def test_a2_bad_credentials_raise_auth_error(live):
    rec = live(lambda r: httpx.Response(200, json={}))
    rec.http._client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    with pytest.raises(PayPalAuthError):
        reads.get_batch("B1")


def test_uat_pp_03_create_payout_body_and_header_match_prd_8_2(live):
    rec = live(lambda r: httpx.Response(201, json={"batch_header": {"payout_batch_id": "B1",
                                                                    "batch_status": "PENDING"}}))
    result = _create()
    (req,) = rec.calls("POST")
    assert req.url.path == "/v1/payments/payouts"
    assert req.headers["PayPal-Request-Id"] == "SP-7f3a9c1e-m1-A1"
    assert json.loads(req.content) == {
        "sender_batch_header": {"sender_batch_id": "SP-7f3a9c1e-m1-A1",
                                "email_subject": "Payment from Lumen Studio",
                                "email_message": "Your milestone was approved and paid."},
        "items": [{"recipient_type": "EMAIL", "amount": {"value": "300.00", "currency": "USD"},
                   "receiver": "dewi@example.test", "note": "Milestone m1",
                   "sender_item_id": "SP-7f3a9c1e-m1-A1-I1"}],
    }
    assert (result.outcome, result.payout_batch_id, result.batch_status) == ("CREATED", "B1", "PENDING")


@pytest.mark.parametrize("cents, text", [(30000, "300.00"), (12345, "123.45"), (5, "0.05"), (0, "0.00")])
def test_uat_pp_04_cents_format_as_two_decimal_strings(cents, text):
    assert http.cents_to_amount(cents) == text
    assert http.amount_to_cents(text) == cents


def test_uat_pp_05_duplicate_rejection_with_link_is_original_found(live):
    live(lambda r: httpx.Response(GATE0["duplicate_status_code"], json=GATE0["duplicate_response_body"]))
    result = _create()
    assert result.outcome == "ORIGINAL_FOUND"
    assert result.payout_batch_id == GATE0["duplicate_original_batch_id"]
    assert result.batch_status is None


def test_a2_duplicate_link_is_found_wherever_it_sits_in_the_body():
    body = {"name": "X", "links": [{"rel": "self", "href": "https://h/v1/payments/payouts/TOPLEVEL1"}]}
    assert http.find_original_batch_id(body) == "TOPLEVEL1"
    assert http.find_original_batch_id({"links": [{"href": "https://h/v1/payments/payouts-item/IT1"}]}) is None


def test_a2_a_400_without_a_duplicate_link_is_a_plain_rejection(live):
    live(lambda r: httpx.Response(400, json={"name": "VALIDATION_ERROR", "debug_id": "d1", "details": []}))
    with pytest.raises(PayPalRejected) as err:
        _create()
    assert (err.value.name, err.value.debug_id, err.value.status_code) == ("VALIDATION_ERROR", "d1", 400)


@pytest.mark.parametrize("failure", ["timeout", "connect", "503"])
def test_uat_pp_06_lost_responses_raise_unknown_and_are_sent_once(live, failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "connect":
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(503, json={"name": "SERVICE_UNAVAILABLE"})

    rec = live(handler)
    with pytest.raises(PayPalOutcomeUnknown):
        _create()
    assert len(rec.calls("POST")) == 1
    assert rec.sleeps == []


def test_uat_pp_07_a_422_raises_rejected_with_name_and_debug_id(live):
    live(lambda r: httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY", "debug_id": "dbg422"}))
    with pytest.raises(PayPalRejected) as err:
        _create()
    assert (err.value.name, err.value.debug_id) == ("UNPROCESSABLE_ENTITY", "dbg422")


def test_uat_pp_08_cancel_item_posts_an_empty_body_and_returns_returned(live):
    rec = live(lambda r: httpx.Response(200, json=GATE0["cancel_response_body"]))
    item = writes.cancel_item("ITEM9")
    (req,) = rec.calls("POST")
    assert req.url.path == "/v1/payments/payouts-item/ITEM9/cancel"
    assert json.loads(req.content) == {}
    assert item.transaction_status == "RETURNED"
    assert (item.amount_cents, item.currency) == (100, "USD")
    assert item.receiver == GATE0["cancel_response_body"]["payout_item"]["receiver"]


def test_a2_cancel_rejection_keeps_paypals_error_name(live):
    live(lambda r: httpx.Response(400, json={"name": "ITEM_INCORRECT_STATUS", "debug_id": "d2"}))
    with pytest.raises(PayPalRejected) as err:
        writes.cancel_item("ITEM9")
    assert err.value.name == "ITEM_INCORRECT_STATUS"


def test_uat_pp_09_a_batch_with_no_items_yet_is_not_an_error(live):
    live(lambda r: httpx.Response(200, json={"batch_header": {"payout_batch_id": "B1", "batch_status": "PENDING"}}))
    view = reads.get_batch("B1")
    assert view.items == [] and view.batch_status == "PENDING"


def test_a2_get_batch_and_get_item_parse_the_real_response_shape(live):
    live(lambda r: httpx.Response(200, json=BATCH_BODY if "payouts-item" not in r.url.path
                                  else BATCH_BODY["items"][0]))
    batch = reads.get_batch("B1")
    assert batch.sender_batch_id == "SP-run-m1-A1"
    assert batch.items[0].transaction_status == "UNCLAIMED"
    assert batch.items[0].amount_cents == 30000
    assert reads.get_item("I1").errors == {"name": "RECEIVER_UNREGISTERED"}


def test_a2_a_read_is_retried_once_after_one_second_then_raises(live):
    rec = live(lambda r: httpx.Response(503, json={}))
    with pytest.raises(PayPalOutcomeUnknown):
        reads.get_item("I1")
    assert len(rec.calls("GET")) == 2 and rec.sleeps == [1.0]


def test_a2_a_read_that_recovers_on_the_retry_returns_the_result(live):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        return httpx.Response(503) if state["n"] == 1 else httpx.Response(200, json=BATCH_BODY)

    live(handler)
    assert reads.get_batch("B1").payout_batch_id == "B1"


def test_a2_a_read_4xx_is_not_retried(live):
    rec = live(lambda r: httpx.Response(404, json={"name": "INVALID_RESOURCE_ID", "debug_id": "d3"}))
    with pytest.raises(PayPalRejected):
        reads.get_item("nope")
    assert len(rec.calls("GET")) == 1 and rec.sleeps == []


def test_a2_a_2xx_create_without_a_batch_id_is_unknown_not_created(live):
    live(lambda r: httpx.Response(201, json={}))
    with pytest.raises(PayPalOutcomeUnknown):
        _create()


def test_a2_a_write_401_refreshes_the_token_and_repeats_once(live):
    def handler(request):
        if request.headers["Authorization"] == "Bearer tok1":
            return httpx.Response(401, json={})
        return httpx.Response(201, json={"batch_header": {"payout_batch_id": "B1", "batch_status": "PENDING"}})

    rec = live(handler)
    assert _create().outcome == "CREATED"
    assert len(rec.calls("POST")) == 2  # 401 means PayPal did nothing, so the repeat is safe
