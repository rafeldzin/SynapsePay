"""A1: schema, ledger invariants (UAT-LED-01 to 04), events, reset and the startup guard (UAT-ARCH-06)."""
import sqlite3

import pytest

from app import db, events, seed
from app.config import ConfigError, get_settings
from tests.conftest import CORRECT_EMAIL, TYPO_EMAIL


@pytest.fixture
def conn():
    connection = db.connect()
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded(conn):
    with db.tx(conn):
        seed.seed_scenario(conn, "A", payee_email_correct=CORRECT_EMAIL, payee_email_typo=TYPO_EMAIL)
    return conn


def _payout(conn, payout_id, attempt, state):
    conn.execute(
        "INSERT INTO payouts (id, milestone_id, attempt, sender_batch_id, recipient_email, amount_cents, "
        "currency, state, approved_by, created_at, updated_at) "
        "VALUES (?, 'm1', ?, ?, 'x@example.test', 30000, 'USD', ?, 'ops-lead', 't', 't')",
        (payout_id, attempt, f"SP-run-m1-A{attempt}", state),
    )


def _case(conn, case_id, payout_id, state):
    conn.execute(
        "INSERT INTO cases (id, payout_id, milestone_id, trigger_rule, state, opened_at, updated_at) "
        "VALUES (?, ?, 'm1', 'D1', ?, 't', 't')", (case_id, payout_id, state),
    )


def test_schema_creates_wal_database_with_foreign_keys(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "contracts", "milestones", "deliverables", "payouts", "messages", "cases",
            "events"} <= tables


def test_uat_led_01_events_cannot_be_updated(conn):
    events.append(conn, "RESET", "system")
    with pytest.raises(sqlite3.IntegrityError, match="events is append-only"):
        conn.execute("UPDATE events SET type = 'x'")


def test_uat_led_02_events_cannot_be_deleted(conn):
    events.append(conn, "RESET", "system")
    with pytest.raises(sqlite3.IntegrityError, match="events is append-only"):
        conn.execute("DELETE FROM events")


def test_uat_led_03_one_live_payout_per_milestone(seeded):
    _payout(seeded, "po_00000001", 1, "SENT")
    with pytest.raises(sqlite3.IntegrityError):
        _payout(seeded, "po_00000002", 2, "UNCLAIMED")


def test_a1_a_returned_payout_does_not_block_a_new_live_one(seeded):
    _payout(seeded, "po_00000001", 1, "RETURNED")
    _payout(seeded, "po_00000002", 2, "SENT")


def test_uat_led_04_one_open_case_per_payout(seeded):
    _payout(seeded, "po_00000001", 1, "UNCLAIMED")
    _case(seeded, "case_00000001", "po_00000001", "OPEN")
    with pytest.raises(sqlite3.IntegrityError):
        _case(seeded, "case_00000002", "po_00000001", "INVESTIGATED")
    seeded.execute("UPDATE cases SET state = 'RESOLVED' WHERE id = 'case_00000001'")
    _case(seeded, "case_00000002", "po_00000001", "OPEN")  # allowed once the first is resolved


def test_a1_events_append_and_list_roundtrip(conn):
    events.append(conn, "RESET", "system", payload={"run_id": "abcd1234", "scenario": "A"})
    events.append(conn, "SYNC_ERROR", "paypal", payout_id="po_1", paypal_batch_id="B1")
    listed = events.list_events(conn)
    assert [e["type"] for e in listed] == ["RESET", "SYNC_ERROR"]
    assert listed[0]["payload"] == {"run_id": "abcd1234", "scenario": "A"}
    assert [e["type"] for e in events.list_events(conn, payout_id="po_1")] == ["SYNC_ERROR"]


def test_a1_events_list_returns_newest_window_oldest_first(conn):
    for _ in range(5):
        events.append(conn, "SYNC_ERROR", "paypal")
    ids = [e["id"] for e in events.list_events(conn, limit=3)]
    assert ids == sorted(ids) and len(ids) == 3 and ids[-1] == 5


def test_a1_events_reject_a_type_outside_prd_5_1(conn):
    with pytest.raises(ValueError):
        events.append(conn, "MADE_UP", "system")


def test_a1_event_types_equal_prd_5_1():
    from pathlib import Path
    import re
    prd = (Path(__file__).resolve().parent.parent / "docs" / "PRD.md").read_text(encoding="utf-8")
    section = prd[prd.index("### 5.1 Event types"):prd.index("## 6. State machines")]
    assert events.EVENT_TYPES == set(re.findall(r"^\| `([A-Z_]+)` \|", section, flags=re.M))


def test_a1_tx_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with db.tx(conn):
            events.append(conn, "RESET", "system")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_a1_reset_drops_everything_including_events_and_restores_the_schema(seeded):
    events.append(seeded, "RESET", "system")
    db.reset(seeded)
    assert seeded.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert seeded.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 0
    assert seeded.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):  # triggers are back
        events.append(seeded, "RESET", "system")
        seeded.execute("DELETE FROM events")


def test_a1_config_blank_values_use_prd_defaults(monkeypatch):
    for name in ("LLM_DAILY_CALL_CAP", "INVESTIGATOR_MAX_STEPS", "SYNC_INTERVAL_SECONDS", "PAYPAL_BASE_URL"):
        monkeypatch.setenv(name, "")
    s = get_settings()
    assert (s.llm_daily_call_cap, s.investigator_max_steps, s.sync_interval_seconds) == (300, 6, 5)
    assert s.paypal_base_url == "https://api-m.sandbox.paypal.com"
    assert s.sync_loop_enabled is False  # set by the tier 1 fixture
    s.validate()


@pytest.mark.parametrize("url", ["https://api-m.paypal.com", "https://api.paypal.com",
                                 "https://api-m.sandbox.paypal.com.evil.test", "http://localhost:9"])
def test_a1_config_rejects_any_host_but_the_sandbox(monkeypatch, url):
    monkeypatch.setenv("PAYPAL_BASE_URL", url)
    with pytest.raises(ConfigError):
        get_settings().validate()


def test_uat_arch_06_startup_refuses_a_non_sandbox_host(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")
    with pytest.raises(ConfigError):
        with TestClient(app):
            pytest.fail("the app must not start")


def test_a1_health_reports_modes_on_a_fresh_database(client):
    body = client.get("/api/health").json()
    assert body == {"ok": True, "run_id": None, "scenario": None,
                    "paypal_mode": "fake", "llm_mode": "fake"}


def test_a1_startup_creates_the_schema_when_no_database_exists(client, tmp_path):
    assert (tmp_path / "test.db").exists()
    conn = db.connect()
    try:
        assert db.schema_exists(conn)
    finally:
        conn.close()
