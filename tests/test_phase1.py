import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.core.database import init_db
from app.core.state_machine import (
    ContractState,
    ContractStateMachine,
    InvalidStateTransitionError,
)
from app.services.paypal import PayPalClient, PayPalAPIError


# ==============================================================================
# R2: STATE MACHINE & AUDIT LOG TESTS (OFFLINE)
# ==============================================================================

@pytest.fixture
def state_machine(tmp_path):
    """Fixture providing a fresh ContractStateMachine with a temporary SQLite database."""
    db_file = str(tmp_path / "test_synapsepay.db")
    sm = ContractStateMachine(db_path=db_file)
    return sm


def test_valid_lifecycle_flow(state_machine):
    """
    Test standard valid state lifecycle:
    CREATED -> AUTHORIZED -> M_i_CAPTURED -> VOIDED
    """
    contract_id = "contract-alpha-001"

    # Step 1: Initial state is None
    assert state_machine.get_current_state(contract_id) is None

    # Step 2: Transition to CREATED
    log1 = state_machine.transition(
        contract_id=contract_id,
        to_state=ContractState.CREATED,
        event="CONTRACT_REGISTERED",
        payload={"total_amount": 300.0, "currency": "USD"},
    )
    assert log1["to_state"] == "CREATED"
    assert state_machine.get_current_state(contract_id) == "CREATED"

    # Step 3: Transition to AUTHORIZED
    log2 = state_machine.transition(
        contract_id=contract_id,
        to_state=ContractState.AUTHORIZED,
        event="PAYPAL_AUTHORIZED",
        payload={"order_id": "ORDER-123", "authorization_id": "AUTH-456"},
    )
    assert log2["to_state"] == "AUTHORIZED"
    assert state_machine.get_current_state(contract_id) == "AUTHORIZED"

    # Step 4: Transition to M_i_CAPTURED (Milestone 1 captured)
    log3 = state_machine.transition(
        contract_id=contract_id,
        to_state=ContractState.M_I_CAPTURED,
        event="MILESTONE_VERIFIED_AND_CAPTURED",
        milestone_id="m-001",
        payload={"amount": 100.0, "capture_id": "CAP-789"},
    )
    assert log3["to_state"] == "M_i_CAPTURED"
    assert log3["milestone_id"] == "m-001"
    assert state_machine.get_current_state(contract_id) == "M_i_CAPTURED"

    # Step 5: Transition to VOIDED (e.g. buyer voids remainder or contract terminates)
    log4 = state_machine.transition(
        contract_id=contract_id,
        to_state=ContractState.VOIDED,
        event="AUTHORIZATION_VOIDED",
        payload={"reason": "Contract completed or remainder released"},
    )
    assert log4["to_state"] == "VOIDED"
    assert state_machine.get_current_state(contract_id) == "VOIDED"

    # Verify append-only history audit trail
    history = state_machine.get_contract_history(contract_id)
    assert len(history) == 4
    assert [h["to_state"] for h in history] == ["CREATED", "AUTHORIZED", "M_i_CAPTURED", "VOIDED"]
    assert [h["from_state"] for h in history] == ["NONE", "CREATED", "AUTHORIZED", "M_i_CAPTURED"]
    assert history[2]["milestone_id"] == "m-001"
    assert history[2]["payload"]["capture_id"] == "CAP-789"


def test_invalid_transition_created_to_captured(state_machine):
    """
    Test illegal direct transition from CREATED directly to M_i_CAPTURED.
    Should raise InvalidStateTransitionError.
    """
    contract_id = "contract-bad-001"

    # Create contract
    state_machine.transition(
        contract_id=contract_id,
        to_state=ContractState.CREATED,
        event="CONTRACT_REGISTERED",
    )

    # Direct capture without authorization must be rejected
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        state_machine.transition(
            contract_id=contract_id,
            to_state=ContractState.M_I_CAPTURED,
            event="ATTEMPT_CAPTURE_WITHOUT_AUTH",
            milestone_id="m-001",
        )

    assert "cannot move from CREATED to M_i_CAPTURED" in str(exc_info.value)
    # Ensure state remained CREATED
    assert state_machine.get_current_state(contract_id) == "CREATED"


def test_terminal_state_rejection(state_machine):
    """
    Test that once a contract reaches VOIDED, no further transitions are permitted.
    """
    contract_id = "contract-terminal-001"

    state_machine.transition(contract_id, ContractState.CREATED, "INIT")
    state_machine.transition(contract_id, ContractState.VOIDED, "CANCEL")
    assert state_machine.get_current_state(contract_id) == "VOIDED"

    with pytest.raises(InvalidStateTransitionError):
        state_machine.transition(contract_id, ContractState.AUTHORIZED, "REOPEN")


def test_multiple_milestone_transitions(state_machine):
    """
    Test multiple milestone captures and failures:
    CREATED -> AUTHORIZED -> M_i_CAPTURED (m1) -> M_i_FAILED (m2) -> M_i_CAPTURED (m2 retry) -> VOIDED
    """
    contract_id = "contract-multi-m"

    state_machine.transition(contract_id, ContractState.CREATED, "INIT")
    state_machine.transition(contract_id, ContractState.AUTHORIZED, "AUTH")
    state_machine.transition(contract_id, ContractState.M_I_CAPTURED, "M1_PASS", milestone_id="m-001")
    state_machine.transition(contract_id, ContractState.M_I_FAILED, "M2_FAIL", milestone_id="m-002")
    state_machine.transition(contract_id, ContractState.M_I_CAPTURED, "M2_PASS", milestone_id="m-002")
    state_machine.transition(contract_id, ContractState.VOIDED, "FINISH")

    history = state_machine.get_contract_history(contract_id)
    assert len(history) == 6
    assert state_machine.get_current_state(contract_id) == "VOIDED"


# ==============================================================================
# R1: PAYPAL SANDBOX CLIENT TESTS (MOCKED HTTPX)
# ==============================================================================

def test_paypal_token_caching():
    """
    Test that PayPalClient caches OAuth tokens and does not call /v1/oauth2/token redundantly.
    """
    async def _test():
        client = PayPalClient(client_id="test_id", client_secret="test_secret")

        mock_token_response = httpx.Response(
            200,
            json={"access_token": "mocked_bearer_token", "expires_in": 3600},
            request=httpx.Request("POST", "https://api-m.sandbox.paypal.com/v1/oauth2/token"),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_token_response

            # First call fetches token
            token1 = await client.get_access_token()
            assert token1 == "mocked_bearer_token"
            assert mock_post.call_count == 1

            # Second call immediately uses in-memory cached token
            token2 = await client.get_access_token()
            assert token2 == "mocked_bearer_token"
            assert mock_post.call_count == 1

    asyncio.run(_test())


def test_paypal_create_order():
    """
    Test create_order payload with AUTHORIZE intent.
    """
    async def _test():
        client = PayPalClient(client_id="test_id", client_secret="test_secret")
        client._cached_token = "valid_token"
        client._token_expires_at = 9999999999.0

        mock_order_response = httpx.Response(
            201,
            json={"id": "ORDER-TEST-001", "status": "CREATED", "intent": "AUTHORIZE"},
            request=httpx.Request("POST", "https://api-m.sandbox.paypal.com/v2/checkout/orders"),
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_order_response

            res = await client.create_order(total_amount=250.0, currency="USD")
            assert res["id"] == "ORDER-TEST-001"
            assert res["intent"] == "AUTHORIZE"

            mock_req.assert_called_once()
            _, kwargs = mock_req.call_args
            assert kwargs["method"] == "POST"
            assert "/v2/checkout/orders" in kwargs["url"]
            assert kwargs["json"]["intent"] == "AUTHORIZE"
            assert kwargs["json"]["purchase_units"][0]["amount"]["value"] == "250.00"

    asyncio.run(_test())


def test_paypal_capture_with_idempotency():
    """
    Test capture_authorization passes PayPal-Request-Id header when idempotency_key is provided.
    """
    async def _test():
        client = PayPalClient(client_id="test_id", client_secret="test_secret")
        client._cached_token = "valid_token"
        client._token_expires_at = 9999999999.0

        mock_capture_response = httpx.Response(
            201,
            json={"id": "CAPTURE-001", "status": "COMPLETED"},
            request=httpx.Request("POST", "https://api-m.sandbox.paypal.com/v2/payments/authorizations/AUTH-123/capture"),
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_capture_response

            res = await client.capture_authorization(
                authorization_id="AUTH-123",
                amount=75.50,
                currency="USD",
                idempotency_key="idempotency-key-xyz",
            )
            assert res["id"] == "CAPTURE-001"

            _, kwargs = mock_req.call_args
            assert kwargs["headers"]["PayPal-Request-Id"] == "idempotency-key-xyz"
            assert kwargs["json"]["amount"]["value"] == "75.50"

    asyncio.run(_test())


def test_paypal_void_authorization():
    """
    Test void_authorization handles 204 No Content response properly.
    """
    async def _test():
        client = PayPalClient(client_id="test_id", client_secret="test_secret")
        client._cached_token = "valid_token"
        client._token_expires_at = 9999999999.0

        mock_void_response = httpx.Response(
            204,
            content=b"",
            request=httpx.Request("POST", "https://api-m.sandbox.paypal.com/v2/payments/authorizations/AUTH-123/void"),
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_void_response

            res = await client.void_authorization(authorization_id="AUTH-123")
            assert res["status"] == "SUCCESS"
            assert res["status_code"] == 204

    asyncio.run(_test())
