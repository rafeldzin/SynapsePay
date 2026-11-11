import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

DEFAULT_DB_URL = "sqlite:///./db/synapsepay.db"


def parse_sqlite_path(db_url: Optional[str] = None) -> str:
    """
    Parse a SQLite database URL or file path.
    Checks DATABASE_PATH environment variable first.
    Supports 'sqlite:///relative/path.db', 'sqlite:////abs/path.db', and ':memory:'.
    """
    custom_path = os.getenv("DATABASE_PATH")
    if custom_path:
        if custom_path == ":memory:":
            return ":memory:"
        p = Path(custom_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    url = db_url or os.getenv("DATABASE_URL", DEFAULT_DB_URL)
    if url == ":memory:" or url.startswith("sqlite:///:memory:"):
        return ":memory:"

    if url.startswith("sqlite:///"):
        path_str = url[len("sqlite:///"):]
    elif url.startswith("sqlite://"):
        path_str = url[len("sqlite://"):]
    else:
        path_str = url

    db_path = Path(path_str)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


@contextmanager
def get_connection(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager providing a SQLite database connection with row factory.
    Enforces foreign keys.
    """
    path = parse_sqlite_path(db_path)
    conn = sqlite3.connect(path, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """
    Initialize SQLite schema for contracts, milestones, payouts, events,
    inbound_messages, and cases.
    Enforces append-only triggers on events and partial unique active payout index.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # 1. Contracts Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                id TEXT PRIMARY KEY,
                buyer_name TEXT,
                contractor_name TEXT,
                currency TEXT DEFAULT 'USD'
            );
            """
        )

        # 2. Milestones Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS milestones (
                id TEXT PRIMARY KEY,
                contract_id TEXT,
                title TEXT NOT NULL,
                amount REAL NOT NULL,
                acceptance_json TEXT DEFAULT '{}',
                contractor_email TEXT NOT NULL,
                FOREIGN KEY (contract_id) REFERENCES contracts (id) ON DELETE CASCADE
            );
            """
        )

        # 3. Payouts Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                milestone_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                sender_batch_id TEXT UNIQUE NOT NULL,
                payout_batch_id TEXT,
                payout_item_id TEXT,
                receiver_email TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (milestone_id) REFERENCES milestones (id) ON DELETE RESTRICT
            );
            """
        )

        # Partial unique index: only ONE active payout per milestone
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_payouts_active_milestone 
            ON payouts (milestone_id) 
            WHERE status IN ('RESERVED', 'PENDING', 'PROCESSING', 'UNCLAIMED', 'SUCCESS', 'ONHOLD', 'BLOCKED');
            """
        )

        # 4. Events Table (Append-Only)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                milestone_id TEXT,
                payout_id INTEGER,
                type TEXT NOT NULL,
                payload_json TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # Triggers enforcing append-only guarantee on events
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_events_update
            BEFORE UPDATE ON events
            BEGIN
                SELECT RAISE(ABORT, 'events table is append-only: updates not allowed');
            END;
            """
        )

        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_events_delete
            BEFORE DELETE ON events
            BEGIN
                SELECT RAISE(ABORT, 'events table is append-only: deletions not allowed');
            END;
            """
        )

        # 5. Inbound Messages Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                milestone_id TEXT NOT NULL,
                sender TEXT,
                body TEXT NOT NULL,
                received_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (milestone_id) REFERENCES milestones (id) ON DELETE CASCADE
            );
            """
        )

        # 6. Cases Table (Investigator Cases)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                milestone_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'OPEN',
                FOREIGN KEY (milestone_id) REFERENCES milestones (id) ON DELETE CASCADE
            );
            """
        )

        conn.commit()


# ==============================================================================
# CONTRACT OPERATIONS
# ==============================================================================

def create_contract(
    id: str,
    buyer_name: str,
    contractor_name: str,
    currency: str = "USD",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO contracts (id, buyer_name, contractor_name, currency)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                buyer_name=excluded.buyer_name,
                contractor_name=excluded.contractor_name,
                currency=excluded.currency;
            """,
            (id, buyer_name, contractor_name, currency),
        )
        conn.commit()
    return {"id": id, "buyer_name": buyer_name, "contractor_name": contractor_name, "currency": currency}


def get_contract(contract_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
        return dict(row) if row else None


# ==============================================================================
# MILESTONE OPERATIONS
# ==============================================================================

def create_milestone(
    id: str,
    contract_id: str,
    title: str,
    amount: float,
    contractor_email: str,
    acceptance_json: str = "{}",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO milestones (id, contract_id, title, amount, acceptance_json, contractor_email)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                contract_id=excluded.contract_id,
                title=excluded.title,
                amount=excluded.amount,
                acceptance_json=excluded.acceptance_json,
                contractor_email=excluded.contractor_email;
            """,
            (id, contract_id, title, amount, acceptance_json, contractor_email),
        )
        conn.commit()
    return {
        "id": id,
        "contract_id": contract_id,
        "title": title,
        "amount": amount,
        "acceptance_json": acceptance_json,
        "contractor_email": contractor_email,
    }


def get_milestone(milestone_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
        return dict(row) if row else None


def get_all_milestones(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM milestones ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def update_milestone_contractor_email(
    milestone_id: str,
    contractor_email: str,
    db_path: Optional[str] = None,
) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "UPDATE milestones SET contractor_email = ? WHERE id = ?",
            (contractor_email, milestone_id),
        )
        conn.commit()
        return cursor.rowcount > 0


# ==============================================================================
# PAYOUT OPERATIONS
# ==============================================================================

class ActivePayoutExistsError(Exception):
    """Raised when an active payout already exists for a milestone."""
    pass


def create_payout_reserved(
    milestone_id: str,
    attempt: int,
    sender_batch_id: str,
    receiver_email: str,
    amount: float,
    db_path: Optional[str] = None,
) -> int:
    """
    Atomically insert a payout with status RESERVED.
    Raises sqlite3.IntegrityError / ActivePayoutExistsError if an active payout exists for this milestone.
    """
    with get_connection(db_path) as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO payouts (
                    milestone_id, attempt, sender_batch_id, receiver_email, amount, status
                ) VALUES (?, ?, ?, ?, ?, 'RESERVED')
                """,
                (milestone_id, attempt, sender_batch_id, receiver_email, amount),
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                raise ActivePayoutExistsError(
                    f"Active payout already in progress or completed for milestone '{milestone_id}'"
                ) from e
            raise


def update_payout(
    payout_id: int,
    status: Optional[str] = None,
    payout_batch_id: Optional[str] = None,
    payout_item_id: Optional[str] = None,
    receiver_email: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params: List[Any] = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if payout_batch_id is not None:
        updates.append("payout_batch_id = ?")
        params.append(payout_batch_id)
    if payout_item_id is not None:
        updates.append("payout_item_id = ?")
        params.append(payout_item_id)
    if receiver_email is not None:
        updates.append("receiver_email = ?")
        params.append(receiver_email)

    params.append(payout_id)
    sql = f"UPDATE payouts SET {', '.join(updates)} WHERE id = ?"

    with get_connection(db_path) as conn:
        conn.execute(sql, params)
        conn.commit()

    return get_payout(payout_id, db_path=db_path)


def get_payout(payout_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
        return dict(row) if row else None


def get_payout_by_sender_batch_id(sender_batch_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM payouts WHERE sender_batch_id = ?", (sender_batch_id,)).fetchone()
        return dict(row) if row else None


def get_payout_by_item_id(payout_item_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM payouts WHERE payout_item_id = ?", (payout_item_id,)).fetchone()
        return dict(row) if row else None


def get_payouts_for_milestone(milestone_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM payouts WHERE milestone_id = ? ORDER BY id ASC",
            (milestone_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_active_payout_for_milestone(milestone_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM payouts 
            WHERE milestone_id = ? 
              AND status IN ('RESERVED', 'PENDING', 'PROCESSING', 'UNCLAIMED', 'SUCCESS', 'ONHOLD', 'BLOCKED')
            ORDER BY id DESC LIMIT 1
            """,
            (milestone_id,),
        ).fetchone()
        return dict(row) if row else None


def get_latest_payout_attempt(milestone_id: str, db_path: Optional[str] = None) -> int:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(attempt), 0) as max_attempt FROM payouts WHERE milestone_id = ?",
            (milestone_id,),
        ).fetchone()
        return row["max_attempt"] if row else 0


def get_all_payouts(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM payouts ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


# ==============================================================================
# EVENT OPERATIONS (APPEND-ONLY)
# ==============================================================================

def record_event(
    type: str,
    milestone_id: Optional[str] = None,
    payout_id: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> int:
    payload_json = json.dumps(payload) if payload is not None else None
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO events (milestone_id, payout_id, type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (milestone_id, payout_id, type, payload_json),
        )
        conn.commit()
        return cursor.lastrowid


def get_events_for_milestone(milestone_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE milestone_id = ? ORDER BY id ASC",
            (milestone_id,),
        ).fetchall()
        events = []
        for r in rows:
            ev = dict(r)
            if ev["payload_json"]:
                try:
                    ev["payload"] = json.loads(ev["payload_json"])
                except Exception:
                    ev["payload"] = ev["payload_json"]
            else:
                ev["payload"] = None
            events.append(ev)
        return events


def get_latest_verdict_for_milestone(milestone_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM events 
            WHERE milestone_id = ? AND type = 'VERDICT_RECORDED'
            ORDER BY id DESC LIMIT 1
            """,
            (milestone_id,),
        ).fetchone()
        if not row:
            return None
        ev = dict(row)
        if ev["payload_json"]:
            try:
                ev["payload"] = json.loads(ev["payload_json"])
            except Exception:
                ev["payload"] = ev["payload_json"]
        return ev


def get_all_events(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id ASC").fetchall()
        events = []
        for r in rows:
            ev = dict(r)
            if ev["payload_json"]:
                try:
                    ev["payload"] = json.loads(ev["payload_json"])
                except Exception:
                    ev["payload"] = ev["payload_json"]
            events.append(ev)
        return events


# ==============================================================================
# INBOUND MESSAGES
# ==============================================================================

def add_inbound_message(
    milestone_id: str,
    body: str,
    sender: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO inbound_messages (milestone_id, sender, body)
            VALUES (?, ?, ?)
            """,
            (milestone_id, sender, body),
        )
        conn.commit()
        msg_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM inbound_messages WHERE id = ?", (msg_id,)).fetchone()
        return dict(row) if row else {"id": msg_id, "milestone_id": milestone_id, "sender": sender, "body": body}


def get_inbound_messages_for_milestone(milestone_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM inbound_messages WHERE milestone_id = ? ORDER BY id ASC",
            (milestone_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ==============================================================================
# INVESTIGATOR CASES
# ==============================================================================

def create_case(
    milestone_id: str,
    reason: str,
    status: str = "OPEN",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO cases (milestone_id, reason, status)
            VALUES (?, ?, ?)
            """,
            (milestone_id, reason, status),
        )
        conn.commit()
        case_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return dict(row) if row else {"id": case_id, "milestone_id": milestone_id, "reason": reason, "status": status}


def get_case(case_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return dict(row) if row else None


def get_cases_for_milestone(milestone_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM cases WHERE milestone_id = ? ORDER BY id ASC",
            (milestone_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_cases(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM cases ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def update_case_status(
    case_id: int,
    status: str,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE cases SET status = ? WHERE id = ?", (status, case_id))
        conn.commit()
    return get_case(case_id, db_path=db_path)


# ==============================================================================
# DEMO RESEED HELPER
# ==============================================================================

def reseed_demo_data(
    db_path: Optional[str] = None,
    scenario: Optional[str] = "timeout",
) -> Dict[str, Any]:
    """
    Reseed demo data safely without violating append-only triggers on events.
    Drops triggers and tables (or recreates DB), then initializes schema.

    Scenarios:
    - "timeout": Milestone 1 contractor_email = DEMO_CONTRACTOR_EMAIL (valid sandbox account)
    - "unclaimed": Milestone 1 contractor_email = DEMO_TYPO_EMAIL (unregistered account)
    """
    with get_connection(db_path) as conn:
        # Dropping triggers and tables drops schema without firing DELETE triggers
        conn.execute("DROP TRIGGER IF EXISTS prevent_events_update;")
        conn.execute("DROP TRIGGER IF EXISTS prevent_events_delete;")
        conn.execute("DROP TABLE IF EXISTS events;")
        conn.execute("DROP TABLE IF EXISTS cases;")
        conn.execute("DROP TABLE IF EXISTS inbound_messages;")
        conn.execute("DROP TABLE IF EXISTS payouts;")
        conn.execute("DROP TABLE IF EXISTS milestones;")
        conn.execute("DROP TABLE IF EXISTS contracts;")
        conn.commit()

    init_db(db_path)

    contract = create_contract(
        id="contract-lumen-001",
        buyer_name="Lumen Studio",
        contractor_name="Dewi",
        currency="USD",
        db_path=db_path,
    )

    # Configurable emails
    default_contractor = (
        os.getenv("SPIKE_RECEIVER_EMAIL")
        or os.getenv("DEMO_CONTRACTOR_EMAIL")
        or os.getenv("SANDBOX_CONTRACTOR_EMAIL")
        or "sb-contractor@business.example.com"
    )

    # Typo email: one character changed in the local part so it does not exist in sandbox
    if os.getenv("DEMO_TYPO_EMAIL"):
        default_typo = os.getenv("DEMO_TYPO_EMAIL")
    else:
        parts = default_contractor.split("@")
        local_part = parts[0]
        domain = parts[1] if len(parts) > 1 else "example.com"
        # change last character of local_part
        typo_local = local_part[:-1] + ("x" if local_part[-1] != "x" else "y") if local_part else "unregistered"
        default_typo = f"{typo_local}@{domain}"

    # Configurable amounts
    amount_m1 = float(os.getenv("DEMO_AMOUNT_M1", 300.0))
    amount_m2 = float(os.getenv("DEMO_AMOUNT_M2", 200.0))

    if scenario == "unclaimed":
        m1_email = default_typo
        m2_email = default_contractor
    else:
        m1_email = default_contractor
        m2_email = default_typo

    m1 = create_milestone(
        id="m-001",
        contract_id="contract-lumen-001",
        title="UI/UX Architecture & Figma Specs",
        amount=amount_m1,
        contractor_email=m1_email,
        acceptance_json=json.dumps({"criteria": ["Interactive Figma prototype", "Component design tokens documentation"]}),
        db_path=db_path,
    )

    m2 = create_milestone(
        id="m-002",
        contract_id="contract-lumen-001",
        title="Frontend Integration & PayPal Payouts Hook",
        amount=amount_m2,
        contractor_email=m2_email,
        acceptance_json=json.dumps({"criteria": ["FastAPI Payouts verification", "E2E status synchronization"]}),
        db_path=db_path,
    )

    return {
        "scenario": scenario,
        "contract": contract,
        "milestones": [m1, m2],
    }
