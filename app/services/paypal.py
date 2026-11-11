import asyncio
import os
import re
import time
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()


class PayPalError(Exception):
    """
    Typed exception carrying HTTP status, PayPal error name, debug_id, and raw body.
    """
    def __init__(
        self,
        status_code: int,
        name: str,
        message: str,
        debug_id: Optional[str] = None,
        raw_body: Optional[str] = None,
    ):
        super().__init__(f"PayPal API Error [{status_code} - {name}]: {message} (debug_id: {debug_id})")
        self.status_code = status_code
        self.name = name
        self.message = message
        self.debug_id = debug_id
        self.raw_body = raw_body


class DuplicateBatchError(PayPalError):
    """
    Raised when PayPal rejects a reused sender_batch_id (e.g. DUPLICATE_BATCH_HEADER).
    """
    def __init__(
        self,
        status_code: int,
        name: str,
        message: str,
        debug_id: Optional[str] = None,
        raw_body: Optional[str] = None,
        original_batch_id: Optional[str] = None,
        original_batch_link: Optional[str] = None,
    ):
        super().__init__(
            status_code=status_code,
            name=name,
            message=message,
            debug_id=debug_id,
            raw_body=raw_body,
        )
        self.original_batch_id = original_batch_id
        self.original_batch_link = original_batch_link


# Backward compatibility alias
PayPalAPIError = PayPalError


class PayPalClient:
    """
    Async PayPal Sandbox REST API Client for Payouts (no SDK).
    Manages OAuth2 token caching, payout creation, status retrieval, and cancellation.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.client_id = client_id or os.getenv("PAYPAL_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("PAYPAL_CLIENT_SECRET", "")
        self.base_url = (base_url or os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")).rstrip("/")
        self.timeout = timeout

        self._cached_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    async def get_access_token(self) -> str:
        """
        Fetch or return cached OAuth2 Bearer token from PayPal.
        """
        # Return valid cached token if expiry is > 60 seconds away
        if self._cached_token and time.time() < (self._token_expires_at - 60):
            return self._cached_token

        token_url = f"{self.base_url}/v1/oauth2/token"
        headers = {
            "Accept": "application/json",
            "Accept-Language": "en_US",
        }
        data = {"grant_type": "client_credentials"}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                token_url,
                auth=(self.client_id, self.client_secret),
                headers=headers,
                data=data,
            )

        if response.status_code != 200:
            raise PayPalError(
                status_code=response.status_code,
                name="AUTHENTICATION_FAILURE",
                message=f"Failed to obtain OAuth2 token: {response.text}",
                debug_id=response.headers.get("PayPal-Debug-Id"),
                raw_body=response.text,
            )

        payload = response.json()
        self._cached_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self._token_expires_at = time.time() + expires_in
        return self._cached_token

    def _parse_error(self, response: httpx.Response) -> PayPalError:
        """
        Parse HTTP error response from PayPal and return typed PayPalError or DuplicateBatchError.
        """
        raw_text = response.text
        debug_id = response.headers.get("PayPal-Debug-Id") or response.headers.get("paypal-debug-id")
        name = "UNKNOWN_ERROR"
        message = raw_text

        links = []
        details = []
        err_json = {}
        try:
            err_json = response.json()
            if isinstance(err_json, dict):
                name = err_json.get("name", name)
                message = err_json.get("message", message)
                debug_id = err_json.get("debug_id") or debug_id
                links = err_json.get("links", [])
                details = err_json.get("details", [])
                if isinstance(details, list) and details:
                    detail_msgs = [f"{d.get('issue', '')}: {d.get('description', '')}" for d in details if isinstance(d, dict)]
                    if detail_msgs:
                        message = f"{message} Details: {'; '.join(detail_msgs)}"
        except Exception:
            pass

        # Check for duplicate batch
        is_duplicate = (
            name in ("DUPLICATE_BATCH_HEADER", "BATCH_ALREADY_EXISTS")
            or "duplicate sender_batch_id" in message.lower()
            or "duplicate" in name.lower()
            or "duplicate" in raw_text.lower()
        )

        if is_duplicate:
            original_batch_link: Optional[str] = None
            original_batch_id: Optional[str] = None

            # 1. Search in links[]
            for link in links:
                if isinstance(link, dict):
                    href = link.get("href", "")
                    rel = link.get("rel", "")
                    if rel in ("original_batch", "batch") or "/payouts/" in href:
                        original_batch_link = href
                        match = re.search(r"/payouts/([A-Za-z0-9_-]+)", href)
                        if match:
                            original_batch_id = match.group(1)
                            break

            # 2. Search in details[]
            if not original_batch_id and isinstance(details, list):
                for d in details:
                    if isinstance(d, dict):
                        d_text = f"{d.get('issue', '')} {d.get('description', '')}"
                        match = re.search(r"/payouts/([A-Za-z0-9_-]+)", d_text)
                        if match:
                            original_batch_id = match.group(1)
                            break

            # 3. Search in top-level JSON fields
            if not original_batch_id and isinstance(err_json, dict):
                for k in ("original_batch_id", "payout_batch_id", "batch_id"):
                    if err_json.get(k):
                        original_batch_id = str(err_json[k])
                        break

            # 4. Search in raw_text via regex
            if not original_batch_id and raw_text:
                match = re.search(r"/payouts/([A-Za-z0-9_-]+)", raw_text)
                if match:
                    original_batch_id = match.group(1)

            return DuplicateBatchError(
                status_code=response.status_code,
                name=name,
                message=message,
                debug_id=debug_id,
                raw_body=raw_text,
                original_batch_id=original_batch_id,
                original_batch_link=original_batch_link,
            )

        return PayPalError(
            status_code=response.status_code,
            name=name,
            message=message,
            debug_id=debug_id,
            raw_body=raw_text,
        )

    async def _send_request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Send authenticated HTTP request to PayPal REST API.
        """
        token = await self.get_access_token()
        req_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)

        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method=method,
                url=url,
                json=json_data,
                headers=req_headers,
            )

        if response.status_code == 204 or not response.content:
            return {"status": "SUCCESS", "status_code": response.status_code}

        if response.status_code not in (200, 201, 202):
            raise self._parse_error(response)

        return response.json()

    async def create_payout(
        self,
        sender_batch_id: str,
        receiver_email: str,
        amount: float,
        currency: str = "USD",
        note: str = "",
        sender_item_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a payout batch via POST /v1/payments/payouts.
        Sends PayPal-Request-Id = sender_batch_id header for idempotency.
        Returns dict with batch_id, batch_status, and payout_item_id (if present).
        """
        headers = {
            "PayPal-Request-Id": sender_batch_id,
        }
        payload = {
            "sender_batch_header": {
                "sender_batch_id": sender_batch_id,
                "email_subject": "You have a payout from SynapsePay!",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "receiver": receiver_email,
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": currency,
                    },
                    "note": note or f"SynapsePay Milestone Payout {sender_batch_id}",
                    "sender_item_id": sender_item_id or f"{sender_batch_id}-item1",
                }
            ],
        }

        res = await self._send_request(
            method="POST",
            path="/v1/payments/payouts",
            json_data=payload,
            headers=headers,
        )

        batch_header = res.get("batch_header", {})
        batch_id = batch_header.get("payout_batch_id")
        batch_status = batch_header.get("batch_status", "PENDING")

        # Extract first item's payout_item_id if returned in items array
        payout_item_id = None
        items = res.get("items", [])
        if items and isinstance(items, list) and isinstance(items[0], dict):
            payout_item_id = items[0].get("payout_item_id")

        return {
            "batch_id": batch_id,
            "batch_status": batch_status,
            "payout_item_id": payout_item_id,
            "raw": res,
        }

    async def get_payout_batch(self, payout_batch_id: str) -> Dict[str, Any]:
        """
        Get payout batch status via GET /v1/payments/payouts/{id}.
        """
        return await self._send_request("GET", f"/v1/payments/payouts/{payout_batch_id}")

    async def get_payout_item(self, payout_item_id: str) -> Dict[str, Any]:
        """
        Get payout item details via GET /v1/payments/payouts-item/{id}.
        """
        return await self._send_request("GET", f"/v1/payments/payouts-item/{payout_item_id}")

    async def cancel_payout_item(self, payout_item_id: str) -> Dict[str, Any]:
        """
        Cancel an unclaimed payout item via POST /v1/payments/payouts-item/{id}/cancel.
        Retries up to 3 attempts on HTTP 5xx with backoff (1s, 3s). No retry on 4xx.
        """
        delays = [1.0, 3.0]
        for attempt in range(3):
            try:
                return await self._send_request("POST", f"/v1/payments/payouts-item/{payout_item_id}/cancel")
            except PayPalError as exc:
                if 500 <= exc.status_code < 600 and attempt < 2:
                    await asyncio.sleep(delays[attempt])
                    continue
                raise


default_paypal_client = PayPalClient()
