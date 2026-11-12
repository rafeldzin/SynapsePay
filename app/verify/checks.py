"""Deterministic facts about a CSV deliverable (PRD 9.1).

No model is involved. The verifier sends these facts to the model instead of the
CSV body, and hard checks are evaluated against them (INV-9).
"""
import csv
import hashlib
import io

from app.contracts import Fact

REQUIRED_COLUMNS = ("customer_id", "name", "email", "country", "signup_date")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def _read(csv_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    """Returns (header, data rows). Raises ValueError or csv.Error if unreadable."""
    text = csv_bytes.decode("utf-8-sig")  # a leading byte order mark is not part of the header
    rows = [row for row in csv.reader(io.StringIO(text, newline="")) if row]
    if not rows:
        raise ValueError("no header row")
    return [column.strip() for column in rows[0]], rows[1:]


def compute_facts(csv_bytes: bytes) -> list[Fact]:
    """The six facts of PRD 9.1, in the PRD's order.

    Never raises: an unreadable CSV yields schema_ok=false and zero counts.
    """
    row_count = null_emails = duplicate_emails = 0
    try:
        header, rows = _read(csv_bytes)
    except (ValueError, csv.Error):  # UnicodeDecodeError is a ValueError
        missing = list(REQUIRED_COLUMNS)
    else:
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        row_count = len(rows)
        if "email" in header:
            email_at = header.index("email")
            seen: set[str] = set()
            for row in rows:
                email = normalize_email(row[email_at]) if email_at < len(row) else ""
                if not email:
                    null_emails += 1
                elif email in seen:
                    duplicate_emails += 1
                else:
                    seen.add(email)

    return [
        Fact(id="sha256", value=hashlib.sha256(csv_bytes).hexdigest()),
        Fact(id="row_count", value=row_count),
        Fact(id="schema_ok", value=not missing),
        Fact(id="missing_columns", value=missing),
        Fact(id="null_email_count", value=null_emails),
        Fact(id="duplicate_email_count", value=duplicate_emails),
    ]
