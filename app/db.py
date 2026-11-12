"""SQLite access (PRD 4.4, 5): stdlib sqlite3, WAL, one connection per call, BEGIN IMMEDIATE for writes."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Opens a new connection. Autocommit mode, so transactions are explicit (see tx())."""
    path = db_path or get_settings().db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    """One write transaction: BEGIN IMMEDIATE, commit on success, roll back on any error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _statements(sql: str):
    """Splits a script into statements. Triggers hold ';' inside BEGIN...END, so use complete_statement."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            buf = ""
            if stmt and not stmt.upper().startswith("PRAGMA"):  # pragmas cannot run in a transaction
                yield stmt


def init_schema(conn: sqlite3.Connection) -> None:
    """Creates every table, index and trigger of PRD 5 in one transaction."""
    with tx(conn):
        for stmt in _statements(SCHEMA_PATH.read_text(encoding="utf-8")):
            conn.execute(stmt)


def schema_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone() is not None


def reset(conn: sqlite3.Connection) -> None:
    """Drops every table and re-runs schema.sql in one transaction (PRD 5).

    Dropping a table does not fire row triggers, so this is the only way events disappear.
    Seeding, the run_id and the RESET event belong to the reset flow that calls this.
    """
    conn.execute("PRAGMA foreign_keys = OFF")  # cannot change inside a transaction
    try:
        with tx(conn):
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall():
                conn.execute(f'DROP TABLE "{name}"')
            for stmt in _statements(SCHEMA_PATH.read_text(encoding="utf-8")):
                conn.execute(stmt)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
