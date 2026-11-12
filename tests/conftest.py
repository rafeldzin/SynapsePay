"""Shared fixtures (UAT 1.3). Tier 1 runs offline: fake PayPal, fake LLM, temp DB, no sync loop.

Fixtures for later packages (fake_paypal, fake_llm, seed_a, seed_b) are added with those packages.
"""
import pytest

CORRECT_EMAIL = "dewi.lestari@example.test"
TYPO_EMAIL = "dewi.lestary@example.test"


@pytest.fixture(autouse=True)
def tier1_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SYNC_LOOP_ENABLED", "false")
    monkeypatch.setenv("UNCLAIMED_GRACE_SECONDS", "600")
    monkeypatch.setenv("PAYPAL_MODE", "fake")
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("DEMO_PAYEE_EMAIL_CORRECT", CORRECT_EMAIL)
    monkeypatch.setenv("DEMO_PAYEE_EMAIL_TYPO", TYPO_EMAIL)


@pytest.fixture
def approve():
    return {"approved_by": "ops-lead", "confirm": True}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c
