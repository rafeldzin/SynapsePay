"""Shared pytest setup (UAT 1.3). Dev A adds DB_PATH, PAYPAL_MODE and the
PayPal fixtures here.

LLM tiers: a test marked `llm` (tier 2) runs against the live model with the
LLM_* settings from .env. Every other test (tiers 1 and 3) gets LLM_MODE=fake
and a fresh FakeLLM, so no model is ever called by accident.
"""
import os
from pathlib import Path

import pytest

from app import llm
from tests.fakes import FakeLLM

ROOT = Path(__file__).resolve().parents[1]


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
