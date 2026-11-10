import os
import time
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()


class PayPalAPIError(Exception):
    """Raised when a PayPal API call returns an error response."""
    def __init__(self, status_code: int, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(f"PayPal API Error [{status_code}]: {message}")
        self.status_code = status_code
        self.details = details or {}


class PayPalClient:
    """
    Async PayPal Sandbox REST API Client.
    Manages OAuth2 token caching, authorization holds, partial milestone captures, and voids.
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
            raise PayPalAPIError(
                status_code=response.status_code,
                message=f"Failed to obtain OAuth2 token: {response.text}",
                details={"response": response.text},
            )

        payload = response.json()
        self._cached_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self._token_expires_at = time.time() + expires_in
        return self._cached_token

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

        # PayPal void returns 204 No Content on success
        if response.status_code == 204 or not response.content:
            return {"status": "SUCCESS", "status_code": response.status_code}

        if response.status_code not in (200, 201, 202):
            try:
                err_detail = response.json()
            except Exception:
                err_detail = {"raw_text": response.text}
            raise PayPalAPIError(
                status_code=response.status_code,
                message=f"PayPal API request failed: {response.text}",
                details=err_detail,
            )

        return response.json()

    async def create_order(self, total_amount: float, currency: str = "USD") -> Dict[str, Any]:
        """
        Create order with intent AUTHORIZE.
        """
        payload = {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "amount": {
                        "currency_code": currency,
                        "value": f"{total_amount:.2f}",
                    }
                }
            ],
        }
        return await self._send_request("POST", "/v2/checkout/orders", json_data=payload)

    async def authorize_order(self, order_id: str) -> Dict[str, Any]:
        """
        Authorize order, locking funds on buyer side.
        Call /v2/checkout/orders/{order_id}/authorize.
        """
        return await self._send_request("POST", f"/v2/checkout/orders/{order_id}/authorize")

    async def capture_authorization(
        self,
        authorization_id: str,
        amount: float,
        currency: str = "USD",
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Capture payment for a milestone from an authorized hold.
        Call /v2/payments/authorizations/{authorization_id}/capture.
        Passes 'PayPal-Request-Id' header if idempotency_key is specified.
        """
        headers = {}
        if idempotency_key:
            headers["PayPal-Request-Id"] = idempotency_key

        payload = {
            "amount": {
                "currency_code": currency,
                "value": f"{amount:.2f}",
            }
        }
        return await self._send_request(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/capture",
            json_data=payload,
            headers=headers,
        )

    async def void_authorization(self, authorization_id: str) -> Dict[str, Any]:
        """
        Void an existing authorization to release remaining funds back to the buyer.
        Call /v2/payments/authorizations/{authorization_id}/void.
        """
        return await self._send_request("POST", f"/v2/payments/authorizations/{authorization_id}/void")


# Default client instance
default_paypal_client = PayPalClient()


async def create_order(total_amount: float, currency: str = "USD") -> Dict[str, Any]:
    return await default_paypal_client.create_order(total_amount, currency)


async def authorize_order(order_id: str) -> Dict[str, Any]:
    return await default_paypal_client.authorize_order(order_id)


async def capture_authorization(
    authorization_id: str,
    amount: float,
    currency: str = "USD",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    return await default_paypal_client.capture_authorization(
        authorization_id,
        amount,
        currency,
        idempotency_key,
    )


async def void_authorization(authorization_id: str) -> Dict[str, Any]:
    return await default_paypal_client.void_authorization(authorization_id)
