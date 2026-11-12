"""Deterministic prompt-injection scan over a report and every CSV cell (PRD 9.2).

Runs before and independently of the model. Any finding caps the overall verdict
at NEEDS_REVIEW (PRD 9.4 step 5). The scanned text is untrusted data (INV-8):
nothing here interprets it, it is only matched and quoted.
"""
import csv
import io
import re

from app.contracts import InjectionFinding

MAX_EXCERPT_CHARS = 200
MAX_CELL_CHARS = 500
# Keeps a hostile upload from storing one finding per cell. One is enough to flag.
MAX_FINDINGS_PER_PATTERN = 5

_FLAGS = re.IGNORECASE | re.MULTILINE | re.DOTALL

# The PRD 9.2 table. A space in the PRD pattern matches any run of whitespace here.
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("INJ-1", re.compile(r"^[ \t]*(?:system|assistant|developer)[ \t]*:", _FLAGS)),
    ("INJ-2", re.compile(
        r"ignore\s+(?:(?:all|any)\s+)?(?:previous|prior|above)\s+(?:instructions|rules)", _FLAGS)),
    ("INJ-3", re.compile(
        r"mark\s+(?:all|every)\s+(?:criteria|criterion|checks?)\s+(?:as\s+)?(?:passed|pass|met)",
        _FLAGS)),
    ("INJ-4", re.compile(
        r"(?:release|approve|authorize|send)\s+(?:the\s+)?(?:payment|payout|funds)", _FLAGS)),
    # An HTML comment, closed or left open to the end of the text.
    ("INJ-5", re.compile(r"<!--.*?(?:-->|\Z)", _FLAGS)),
    # Zero-width characters U+200B to U+200D and U+FEFF.
    ("INJ-5", re.compile("[​-‍﻿]")),
)


def _excerpt(text: str, start: int, end: int) -> str:
    """The whole lines around a match, or 200 chars from the match if that is too long."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    lines = text[line_start:len(text) if line_end == -1 else line_end].strip()
    if len(lines) <= MAX_EXCERPT_CHARS:
        return lines
    return text[start:start + MAX_EXCERPT_CHARS]


def _add(found: dict[str, list[InjectionFinding]], source: str, pattern_id: str,
         excerpt: str) -> None:
    bucket = found.setdefault(pattern_id, [])
    if len(bucket) < MAX_FINDINGS_PER_PATTERN:
        bucket.append(InjectionFinding(source=source, pattern_id=pattern_id, excerpt=excerpt))


def _scan_text(found: dict[str, list[InjectionFinding]], source: str, text: str) -> None:
    for pattern_id, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if len(found.get(pattern_id, ())) >= MAX_FINDINGS_PER_PATTERN:
                break
            _add(found, source, pattern_id, _excerpt(text, match.start(), match.end()))


def _ordered(found: dict[str, list[InjectionFinding]]) -> list[InjectionFinding]:
    return [finding for pattern_id in sorted(found) for finding in found[pattern_id]]


def _cells(csv_bytes: bytes) -> list[str]:
    """Every cell, header included. Never raises, whatever the bytes are."""
    text = csv_bytes.decode("utf-8", errors="replace").removeprefix("﻿")
    try:
        return [cell for row in csv.reader(io.StringIO(text, newline="")) for cell in row]
    except csv.Error:
        # The csv module gave up (for example a field over its size limit).
        return [cell for line in text.splitlines() for cell in line.split(",")]


def scan(report_text: str, csv_bytes: bytes) -> list[InjectionFinding]:
    """Findings for INJ-1 to INJ-5, report first, then CSV, each ordered by pattern ID.

    A leading byte order mark is treated as encoding, not as a zero-width character.
    """
    report: dict[str, list[InjectionFinding]] = {}
    _scan_text(report, "report", report_text.removeprefix("﻿"))

    cells: dict[str, list[InjectionFinding]] = {}
    for cell in _cells(csv_bytes):
        _scan_text(cells, "csv", cell)
        if len(cell) > MAX_CELL_CHARS:
            _add(cells, "csv", "INJ-5", cell[:MAX_EXCERPT_CHARS])

    return _ordered(report) + _ordered(cells)
