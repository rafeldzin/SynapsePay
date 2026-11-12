"""Package B3, tier 2: the investigator against the live model, in both modes.

Run with `py -m pytest -m llm -q -s`. Fake PayPal reads, real model (UAT 1.2).
Mirrors UAT-LLM-05 and LLM-06 at the evaluate_case() level; the UAT-named rows
go through POST /api/cases/{id}/investigate once that route exists.
"""
import sqlite3
import time

import pytest

from app.investigate import investigator
from tests.test_b3_investigator import FakeReads, _schema_sql, scenario_a, scenario_b

pytestmark = pytest.mark.llm


@pytest.fixture(params=["tool_loop", "prefetch"])
def mode(request, monkeypatch):
    monkeypatch.setenv("INVESTIGATOR_MODE", request.param)
    return request.param


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.executescript(_schema_sql())
    yield connection
    connection.close()


def _investigate(conn, reads, mode):
    started = time.monotonic()
    report = investigator.evaluate_case(conn, "case_aaaaaaaa", reads=reads)
    elapsed = time.monotonic() - started
    rank1 = next((h for h in report.hypotheses if h.rank == 1), None)
    print(f"\n{mode}: action={report.recommended_action} downgraded_by={report.downgraded_by} "
          f"rank1={rank1.code if rank1 else None} steps={report.steps_used} {elapsed:.1f}s")
    print(f"  hypotheses={[(h.rank, h.code, h.supported, h.evidence_ids) for h in report.hypotheses]}")
    print(f"  reply_draft={report.reply_draft!r}")
    assert report.mode == mode
    assert report.downgraded_by is None, "no downgrade may be applied (llm_error means no answer)"
    assert report.steps_used <= 6
    assert elapsed < 45, "UAT-LLM-08: investigation within 45 s"
    return report, rank1


def test_b3_live_scenario_a(conn, mode):  # UAT-LLM-05
    reads = FakeReads()
    scenario_a(conn, reads)

    report, rank1 = _investigate(conn, reads, mode)

    assert report.recommended_action == "CANCEL_UNCLAIMED_AND_REISSUE"
    assert rank1.code == "MISTYPED_RECIPIENT"
    assert any(h.code == "PROCESSING_DELAY" and h.supported is False for h in report.hypotheses)


def test_b3_live_scenario_b(conn, mode):  # UAT-LLM-06
    reads = FakeReads()
    scenario_b(conn, reads)

    report, _ = _investigate(conn, reads, mode)

    assert report.recommended_action == "RECONCILE_MARK_PAID"
