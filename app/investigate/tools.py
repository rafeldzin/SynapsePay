"""The investigator's four read-only tools (PRD 11.2).

Arguments are local IDs, and every tool checks that the ID belongs to the case
under investigation before reading anything, so the model cannot query
arbitrary PayPal resources (UAT-SEC-10). Nothing here writes: the database is
only SELECTed and PayPal is reached only through paypal.reads (INV-2, INV-3).

Each call, refused or not, becomes one Evidence item (E1, E2, ...). A refused
call is local evidence, since no PayPal read was made for it.
"""
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.contracts import Evidence

MAX_EVENTS = 50
LIVE_STATES = ("CREATING", "UNKNOWN", "SENT", "UNCLAIMED", "PAID")

TOOL_DEFINITIONS = [
    {"name": "paypal_get_payout_item",
     "description": "Live PayPal status of the payout's item: transaction status, receiver, "
                    "amount and errors. Read-only.",
     "parameters": {"type": "object", "properties": {"payout_id": {"type": "string"}},
                    "required": ["payout_id"]}},
    {"name": "paypal_get_payout_batch",
     "description": "Live PayPal status of the payout's batch: batch status, sender_batch_id "
                    "and items. Read-only.",
     "parameters": {"type": "object", "properties": {"payout_id": {"type": "string"}},
                    "required": ["payout_id"]}},
    {"name": "ledger_get_milestone_payouts",
     "description": "Local ledger: the contract's payee email and its verification, the "
                    "milestone, and every payout row for the milestone.",
     "parameters": {"type": "object", "properties": {"milestone_id": {"type": "string"}},
                    "required": ["milestone_id"]}},
    {"name": "case_get_messages_and_events",
     "description": "The contractor's messages, the last 50 events for the case and its "
                    "payout, and the diversion finding.",
     "parameters": {"type": "object", "properties": {"case_id": {"type": "string"}},
                    "required": ["case_id"]}},
]
TOOL_NAMES = [tool["name"] for tool in TOOL_DEFINITIONS]
_ARGUMENT = {tool["name"]: tool["parameters"]["required"][0] for tool in TOOL_DEFINITIONS}
_PAYPAL_TOOLS = {"paypal_get_payout_item", "paypal_get_payout_batch"}


class _Refused(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cursor = conn.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _untrusted(text: str) -> str:
    """A contractor message as a data block it cannot close (INV-8)."""
    text = re.sub(r"<(/?)\s*untrusted_message", r"&lt;\1untrusted_message", text,
                  flags=re.IGNORECASE)
    return f"<untrusted_message>\n{text}\n</untrusted_message>"


def _cents(amount_cents: int, currency: str) -> str:
    return f"{amount_cents // 100}.{amount_cents % 100:02d} {currency}"


def _default_reads():
    from app.paypal import reads  # Dev A's module (package A2)
    return reads


class CaseTools:
    """The four tools bound to one case. `reads` is paypal.reads or a fake of it."""

    def __init__(self, conn: sqlite3.Connection, case_id: str, *, reads: Any = None):
        cases = _rows(conn, "SELECT * FROM cases WHERE id = ?", (case_id,))
        if not cases:
            raise LookupError(f"unknown case {case_id!r}")
        self.conn = conn
        self.case = cases[0]
        self.reads = reads if reads is not None else _default_reads()
        self.evidence: list[Evidence] = []

    # ---- dispatch ---------------------------------------------------------

    def call(self, name: str, args: dict) -> dict:
        """Runs one tool and records it as evidence. Returns what the model sees."""
        source, data, summary = "local", {}, ""
        try:
            if name not in _ARGUMENT:
                raise _Refused(f"unknown tool {name!r}")
            argument = _ARGUMENT[name]
            if set(args) != {argument} or not isinstance(args[argument], str):
                raise _Refused(f"{name} takes exactly one string argument, {argument}")
            data, summary = getattr(self, f"_{name}")(args[argument])
            source = "paypal" if name in _PAYPAL_TOOLS else "local"
        except _Refused as refusal:
            data, summary = {"error": str(refusal)}, f"Refused: {refusal}"

        evidence = Evidence(id=f"E{len(self.evidence) + 1}", tool=name, source=source,
                            summary=summary, data=data, fetched_at=_now())
        self.evidence.append(evidence)
        return {"evidence_id": evidence.id, **data}

    def call_all(self) -> None:
        """Every tool once, on the case's own IDs (the fallback of PRD 11.4 step 6)."""
        own = {"payout_id": self.case["payout_id"], "milestone_id": self.case["milestone_id"],
               "case_id": self.case["id"]}
        for name in TOOL_NAMES:
            self.call(name, {_ARGUMENT[name]: own[_ARGUMENT[name]]})

    # ---- ownership --------------------------------------------------------

    def _payout(self, payout_id: str) -> dict:
        rows = _rows(self.conn, "SELECT * FROM payouts WHERE id = ?", (payout_id,))
        if not rows or rows[0]["milestone_id"] != self.case["milestone_id"]:
            raise _Refused(f"payout {payout_id!r} does not belong to this case")
        return rows[0]

    # ---- PayPal tools -----------------------------------------------------

    def _paypal_get_payout_item(self, payout_id: str) -> tuple[dict, str]:
        payout = self._payout(payout_id)
        if not payout["payout_item_id"]:
            reason = "no payout_item_id is stored for this payout yet"
            return {"available": False, "reason": reason}, f"PayPal item not readable: {reason}"
        try:
            item = self.reads.get_item(payout["payout_item_id"])
        except Exception as error:  # a failed read is evidence too
            reason = f"PayPal read failed: {type(error).__name__}"
            return {"available": False, "reason": reason}, reason
        data = {"available": True, **item.model_dump(exclude={"raw"})}
        return data, (f"Item {item.payout_item_id} is {item.transaction_status}, receiver "
                      f"{item.receiver}, {_cents(item.amount_cents, item.currency)}")

    def _paypal_get_payout_batch(self, payout_id: str) -> tuple[dict, str]:
        payout = self._payout(payout_id)
        if not payout["payout_batch_id"]:
            reason = "no payout_batch_id is stored for this payout yet"
            return {"available": False, "reason": reason}, f"PayPal batch not readable: {reason}"
        try:
            batch = self.reads.get_batch(payout["payout_batch_id"])
        except Exception as error:
            reason = f"PayPal read failed: {type(error).__name__}"
            return {"available": False, "reason": reason}, reason
        data = {"available": True, **batch.model_dump(exclude={"raw", "items"}),
                "items": [item.model_dump(exclude={"raw"}) for item in batch.items]}
        statuses = ", ".join(i.transaction_status for i in batch.items) or "no items yet"
        return data, (f"Batch {batch.payout_batch_id} ({batch.sender_batch_id}) is "
                      f"{batch.batch_status}; items: {statuses}")

    # ---- local tools ------------------------------------------------------

    def _ledger_get_milestone_payouts(self, milestone_id: str) -> tuple[dict, str]:
        if milestone_id != self.case["milestone_id"]:
            raise _Refused(f"milestone {milestone_id!r} does not belong to this case")
        milestone = _rows(self.conn, "SELECT id, contract_id, seq, title, amount_cents, state, "
                                     "verdict FROM milestones WHERE id = ?", (milestone_id,))[0]
        contract = _rows(self.conn, "SELECT id, payee_email, payee_email_verified_at, "
                                    "payee_email_verified_by, currency FROM contracts WHERE id = ?",
                         (milestone["contract_id"],))[0]
        payouts = _rows(self.conn, "SELECT * FROM payouts WHERE milestone_id = ? ORDER BY attempt",
                        (milestone_id,))
        states = ", ".join(f"{p['id']} {p['state']}" for p in payouts) or "none"
        return ({"contract": contract, "milestone": milestone, "payouts": payouts},
                f"Milestone {milestone_id} is {milestone['state']}; payouts: {states}; "
                f"payee {contract['payee_email']}")

    def _case_get_messages_and_events(self, case_id: str) -> tuple[dict, str]:
        if case_id != self.case["id"]:
            raise _Refused(f"case {case_id!r} is not the case under investigation")
        milestone = _rows(self.conn, "SELECT contract_id FROM milestones WHERE id = ?",
                          (self.case["milestone_id"],))[0]
        contract = _rows(self.conn, "SELECT payee_email FROM contracts WHERE id = ?",
                         (milestone["contract_id"],))[0]
        messages = _rows(self.conn, "SELECT id, from_email, body, created_at FROM messages "
                                    "WHERE contract_id = ? ORDER BY created_at, id",
                         (milestone["contract_id"],))
        for message in messages:
            message["body"] = _untrusted(message["body"])
        events = _rows(self.conn,
                       "SELECT * FROM (SELECT id, ts, type, actor, payout_id, case_id, "
                       "paypal_batch_id, paypal_item_id, payload_json FROM events "
                       "WHERE case_id = ? OR payout_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                       (case_id, self.case["payout_id"], MAX_EVENTS))
        for event in events:
            payload = json.loads(event.pop("payload_json") or "{}")
            # message text reaches the model only as an <untrusted_message> block
            event["payload"] = {k: v for k, v in payload.items() if k not in ("body", "message")}
        diversion = {"diversion_risk": bool(self.case["diversion_risk"]),
                     "requested_email": self.case["requested_email"],
                     "current_email": contract["payee_email"]}
        return ({"case": {k: self.case[k] for k in ("id", "trigger_rule", "state", "opened_at")},
                 "messages": messages, "events": events, "diversion": diversion},
                f"{len(messages)} message(s), {len(events)} event(s), "
                f"diversion risk {'flagged' if diversion['diversion_risk'] else 'not flagged'}")
