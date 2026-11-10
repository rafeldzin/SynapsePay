import json
from enum import Enum
from typing import Any, Dict, List, Optional
from app.core.database import get_connection, init_db


class ContractState(str, Enum):
    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    M_I_CAPTURED = "M_i_CAPTURED"
    M_I_FAILED = "M_i_FAILED"
    VOIDED = "VOIDED"
    EXPIRED = "EXPIRED"


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal state transition is attempted."""
    pass


class ContractStateMachine:
    """
    Append-Only Contract State Machine Engine.
    Enforces valid state transitions and guarantees immutability via append-only logs.
    """

    ALLOWED_TRANSITIONS: Dict[Optional[str], List[str]] = {
        # Initial transition when contract is registered
        None: [ContractState.CREATED.value],
        "NONE": [ContractState.CREATED.value],
        "INITIAL": [ContractState.CREATED.value],
        
        # From CREATED
        ContractState.CREATED.value: [
            ContractState.AUTHORIZED.value,
            ContractState.VOIDED.value,
            ContractState.EXPIRED.value,
        ],
        # From AUTHORIZED
        ContractState.AUTHORIZED.value: [
            ContractState.M_I_CAPTURED.value,
            ContractState.M_I_FAILED.value,
            ContractState.VOIDED.value,
            ContractState.EXPIRED.value,
        ],
        # From M_i_CAPTURED
        ContractState.M_I_CAPTURED.value: [
            ContractState.M_I_CAPTURED.value,
            ContractState.M_I_FAILED.value,
            ContractState.VOIDED.value,
            ContractState.EXPIRED.value,
        ],
        # From M_i_FAILED
        ContractState.M_I_FAILED.value: [
            ContractState.M_I_CAPTURED.value,
            ContractState.M_I_FAILED.value,
            ContractState.VOIDED.value,
            ContractState.EXPIRED.value,
        ],
        # Terminal states have no outbound transitions
        ContractState.VOIDED.value: [],
        ContractState.EXPIRED.value: [],
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        init_db(self.db_path)

    def get_current_state(self, contract_id: str) -> Optional[str]:
        """
        Get the latest state for a given contract from the append-only log.
        """
        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT to_state 
                FROM state_logs 
                WHERE contract_id = ? 
                ORDER BY id DESC 
                LIMIT 1
                """,
                (contract_id,),
            )
            row = cursor.fetchone()
            return row["to_state"] if row else None

    def get_contract_history(self, contract_id: str) -> List[Dict[str, Any]]:
        """
        Get full chronological audit trail of state transitions for a given contract.
        """
        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, contract_id, milestone_id, from_state, to_state, event, payload_json, timestamp
                FROM state_logs
                WHERE contract_id = ?
                ORDER BY id ASC
                """,
                (contract_id,),
            )
            rows = cursor.fetchall()
            history = []
            for r in rows:
                payload = None
                if r["payload_json"]:
                    try:
                        payload = json.loads(r["payload_json"])
                    except Exception:
                        payload = r["payload_json"]
                history.append({
                    "id": r["id"],
                    "contract_id": r["contract_id"],
                    "milestone_id": r["milestone_id"],
                    "from_state": r["from_state"],
                    "to_state": r["to_state"],
                    "event": r["event"],
                    "payload": payload,
                    "timestamp": r["timestamp"],
                })
            return history

    def transition(
        self,
        contract_id: str,
        to_state: str | ContractState,
        event: str,
        milestone_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Validate and execute state transition, appending a record to state_logs.
        Throws InvalidStateTransitionError if the transition is prohibited.
        """
        target_state = to_state.value if isinstance(to_state, ContractState) else str(to_state)
        current_state = self.get_current_state(contract_id)

        allowed_targets = self.ALLOWED_TRANSITIONS.get(current_state, [])
        if target_state not in allowed_targets:
            raise InvalidStateTransitionError(
                f"Invalid state transition for contract '{contract_id}': "
                f"cannot move from {current_state} to {target_state} on event '{event}'."
            )

        payload_json = json.dumps(payload) if payload is not None else None
        from_state_record = current_state if current_state is not None else "NONE"

        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO state_logs (contract_id, milestone_id, from_state, to_state, event, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    contract_id,
                    milestone_id,
                    from_state_record,
                    target_state,
                    event,
                    payload_json,
                ),
            )
            conn.commit()
            record_id = cursor.lastrowid

        return {
            "id": record_id,
            "contract_id": contract_id,
            "milestone_id": milestone_id,
            "from_state": from_state_record,
            "to_state": target_state,
            "event": event,
            "payload": payload,
        }
