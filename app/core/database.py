import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional


DEFAULT_DB_URL = "sqlite:///./db/synapsepay.db"


def parse_sqlite_path(db_url: Optional[str] = None) -> str:
    """
    Parse a SQLite database URL or file path.
    Supports 'sqlite:///relative/path.db', 'sqlite:////abs/path.db', and ':memory:'.
    """
    url = db_url or os.getenv("DATABASE_URL", DEFAULT_DB_URL)
    if url == ":memory:" or url.startswith("sqlite:///:memory:"):
        return ":memory:"
    
    if url.startswith("sqlite:///"):
        path_str = url[len("sqlite:///"):]
    elif url.startswith("sqlite://"):
        path_str = url[len("sqlite://"):]
    else:
        path_str = url

    # Ensure parent directory exists for file-based database
    db_path = Path(path_str)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


def init_db(db_path: Optional[str] = None) -> None:
    """
    Initialize SQLite database and create required append-only tables.
    """
    path = parse_sqlite_path(db_path)
    with sqlite3.connect(path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS state_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id TEXT NOT NULL,
                milestone_id TEXT,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                event TEXT NOT NULL,
                payload_json TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_state_logs_contract_id 
            ON state_logs (contract_id);
            """
        )
        conn.commit()


@contextmanager
def get_connection(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager providing a SQLite database connection with row factory.
    """
    path = parse_sqlite_path(db_path)
    conn = sqlite3.connect(path, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
