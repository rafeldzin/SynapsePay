"""Shared pytest setup (UAT 1.3). Tier 1 runs offline: fake PayPal, fake LLM, temp DB, no sync loop.

PayPal and DB side (Dev A): `tier1_env`, `fake_paypal`, `approve`, `client`. Fixtures for later
packages (seed_a, seed_b) are added with those packages.

LLM side (Dev B): a test marked `llm` (tier 2) runs against the live model with the
LLM_* settings from .env. Every other test (tiers 1 and 3) gets LLM_MODE=fake
and a fresh FakeLLM, so no model is ever called by accident.
"""
import os
from pathlib import Path

import pytest

from app import llm
from tests.fakes import FakeLLM

ROOT = Path(__file__).resolve().parents[1]

CORRECT_EMAIL = "dewi.lestari@example.test"
TYPO_EMAIL = "dewi.lestary@example.test"


# ---- PayPal and database (Dev A) ---------------------------------------------

@pytest.fixture(autouse=True)
def tier1_env(tmp_path, monkeypatch):
    # LLM_MODE is owned by _llm_tier below, so tier 2 can run the live model.
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SYNC_LOOP_ENABLED", "false")
    monkeypatch.setenv("UNCLAIMED_GRACE_SECONDS", "600")
    monkeypatch.setenv("PAYPAL_MODE", "fake")
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    monkeypatch.setenv("DEMO_PAYEE_EMAIL_CORRECT", CORRECT_EMAIL)
    monkeypatch.setenv("DEMO_PAYEE_EMAIL_TYPO", TYPO_EMAIL)


@pytest.fixture
def fake_paypal():
    """A fresh FakePayPal, wired in as the PayPal seam for PAYPAL_MODE=fake."""
    from app.paypal.fake import reset_fake

    return reset_fake()


def _seed(scenario: str) -> dict:
    from app import db

    conn = db.connect()
    try:
        if not db.schema_exists(conn):
            db.init_schema(conn)
        return db.reset_demo(conn, scenario)
    finally:
        conn.close()


@pytest.fixture
def seed_a():
    """Resets the test database into scenario A (typo email). Returns {run_id, scenario}."""
    return _seed("A")


@pytest.fixture
def seed_b():
    """Resets the test database into scenario B (correct email, timeout_after_send armed)."""
    return _seed("B")


@pytest.fixture
def approve():
    return {"approved_by": "ops-lead", "confirm": True}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


# ---- LLM (Dev B) --------------------------------------------------------------

def _dotenv_llm_settings() -> dict[str, str]:
    """LLM_* keys from .env, without printing or logging any value."""
    path = ROOT / ".env"
    if not path.exists():
        return {}
    settings = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.startswith("LLM_"):
            settings[key] = value.strip().strip("\"'")
    return settings


@pytest.fixture(autouse=True)
def _llm_tier(request, monkeypatch):
    if request.node.get_closest_marker("llm"):
        for key, value in _dotenv_llm_settings().items():
            if key not in os.environ:
                monkeypatch.setenv(key, value)
        monkeypatch.setenv("LLM_MODE", "live")
        llm.use_fake(None)
        yield None
        return
    fake = FakeLLM()
    monkeypatch.setenv("LLM_MODE", "fake")
    llm.use_fake(fake)
    yield fake
    llm.use_fake(None)


@pytest.fixture
def fake_llm(_llm_tier) -> FakeLLM:
    if _llm_tier is None:
        pytest.fail("fake_llm is not available in a test marked llm")
    return _llm_tier
