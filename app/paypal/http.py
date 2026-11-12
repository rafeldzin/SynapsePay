"""PayPal HTTP layer (PRD 8, 8.1): OAuth token cache, request helper, error mapping, parsers.

Writes are never retried here. A timeout, transport error or 5xx on a write raises
PayPalOutcomeUnknown and the caller decides (INV-4: only a gated RETRY may resend).
"""
import base64
import re
import threading
import time
from decimal import Decimal
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.contracts import BatchView, ItemView

TIMEOUT = httpx.Timeout(20.0, connect=5.0)
TOKEN_REFRESH_MARGIN_S = 60
READ_RETRY_DELAY_S = 1.0
_sleep = time.sleep  # tests replace it


class PayPalOutcomeUnknown(Exception):
    """Timeout, transport error or 5xx: PayPal may or may not have acted."""


class PayPalRejected(Exception):
    """A definite 4xx rejection."""

    def __init__(self, name: str, debug_id: str | None, body: dict, status_code: int | None = None):
        super().__init__(f"{status_code} {name} (debug_id={debug_id})")
        self.name = name
        self.debug_id = debug_id
        self.body = body
        self.status_code = status_code


class PayPalAuthError(Exception):
    """The OAuth token could not be obtained or was refused after a refresh."""


# ---- money and parsing ----------------------------------------------------

def cents_to_amount(cents: int) -> str:
    """30000 -> "300.00", 5 -> "0.05" (PRD 4.4)."""
    if cents < 0:
        raise ValueError("amount must not be negative")
    return f"{cents // 100}.{cents % 100:02d}"


def amount_to_cents(value: str) -> int:
    return int((Decimal(value) * 100).to_integral_value())


def _json(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        return {"raw_text": resp.text[:2000]}
    return body if isinstance(body, dict) else {"raw": body}


def find_original_batch_id(body) -> str | None:
    """Scans every `href` in a body for a /v1/payments/payouts/{id} link and returns the id.

    Reference: the duplicate body saved in docs/gate0_result.json, where the link sits in
    details[0].link[0].href and not in the top-level links.
    """
    stack = [body]
    while stack:
        node = stack.pop(0)
        if isinstance(node, dict):
            href = node.get("href")
            if isinstance(href, str):
                path = urlparse(href).path
                if "/v1/payments/payouts/" in path:
                    return path.rstrip("/").split("/")[-1]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def duplicate_original_batch_id(body: dict) -> str | None:
    """The original batch id when `body` rejects a duplicate sender_batch_id and links it, else None."""
    if "sender_batch_id" not in str(body).lower():
        return None
    return find_original_batch_id(body)


def parse_item(data: dict, batch_id: str | None = None) -> ItemView:
    inner = data.get("payout_item") or {}
    amount = inner.get("amount") or {}
    return ItemView(
        payout_item_id=data["payout_item_id"],
        payout_batch_id=data.get("payout_batch_id") or batch_id or "",
        transaction_status=data["transaction_status"],
        receiver=inner.get("receiver", ""),
        amount_cents=amount_to_cents(amount.get("value", "0")),
        currency=amount.get("currency", ""),
        errors=data.get("errors"),
        raw=data,
    )


def parse_batch(data: dict) -> BatchView:
    header = data.get("batch_header") or {}
    batch_id = header["payout_batch_id"]
    return BatchView(
        payout_batch_id=batch_id,
        sender_batch_id=(header.get("sender_batch_header") or {}).get("sender_batch_id", ""),
        batch_status=header["batch_status"],
        items=[parse_item(i, batch_id) for i in data.get("items") or []],
        raw=data,
    )


# ---- client -----------------------------------------------------------------

class PayPalHTTP:
    def __init__(self, base_url: str, client_id: str, client_secret: str,
                 client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self._basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._client = client or httpx.Client(timeout=TIMEOUT)
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _get_token(self, force: bool = False) -> str:
        with self._lock:
            if force or not self._token or time.monotonic() >= self._expires_at:
                resp = self._client.post(
                    self.base_url + "/v1/oauth2/token",
                    headers={"Authorization": f"Basic {self._basic}", "Accept": "application/json"},
                    data={"grant_type": "client_credentials"},
                )
                body = _json(resp)
                if resp.status_code != 200 or "access_token" not in body:
                    raise PayPalAuthError(f"token request failed: HTTP {resp.status_code}")
                self._token = body["access_token"]
                self._expires_at = (time.monotonic() + int(body.get("expires_in", 0))
                                    - TOKEN_REFRESH_MARGIN_S)
            return self._token

    def _send(self, method: str, path: str, json=None, headers: dict | None = None) -> httpx.Response:
        """One call with auth. On a 401 the token is refreshed once and the call repeated once."""
        for attempt in (1, 2):
            token = self._get_token(force=attempt == 2)
            resp = self._client.request(
                method, self.base_url + path, json=json,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                         **(headers or {})},
            )
            if resp.status_code != 401:
                return resp
        raise PayPalAuthError("PayPal refused a freshly issued token (HTTP 401)")

    @staticmethod
    def _rejected(resp: httpx.Response) -> PayPalRejected:
        body = _json(resp)
        return PayPalRejected(body.get("name") or f"HTTP_{resp.status_code}",
                              body.get("debug_id"), body, resp.status_code)

    def read(self, path: str) -> dict:
        """GET. A timeout, transport error or 5xx is retried once after 1 s, then PayPalOutcomeUnknown."""
        last: Exception | None = None
        for attempt in (1, 2):
            try:
                resp = self._send("GET", path)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            else:
                if resp.status_code < 300:
                    return _json(resp)
                if resp.status_code < 500:
                    raise self._rejected(resp)
                last = RuntimeError(f"HTTP {resp.status_code}")
            if attempt == 1:
                _sleep(READ_RETRY_DELAY_S)
        raise PayPalOutcomeUnknown(f"read {path} failed twice: {last}")

    def write(self, method: str, path: str, *, json: dict, headers: dict | None = None) -> dict:
        """POST. Sent exactly once: no automatic retry on any outcome."""
        try:
            resp = self._send(method, path, json=json, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise PayPalOutcomeUnknown(f"{method} {path}: {type(exc).__name__}") from exc
        if resp.status_code >= 500:
            raise PayPalOutcomeUnknown(f"{method} {path}: HTTP {resp.status_code} "
                                       f"debug_id={_json(resp).get('debug_id')}")
        if resp.status_code >= 300:
            raise self._rejected(resp)
        return _json(resp)


_http: PayPalHTTP | None = None


def get_http() -> PayPalHTTP:
    global _http
    if _http is None:
        s = get_settings()
        s.validate()
        _http = PayPalHTTP(s.paypal_base_url, s.paypal_client_id, s.paypal_client_secret)
    return _http


def set_http(http: PayPalHTTP | None) -> None:
    """Tests inject a client built on httpx.MockTransport, or None to rebuild from settings."""
    global _http
    _http = http
