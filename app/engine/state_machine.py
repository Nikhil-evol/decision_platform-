from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from app.schemas.workflow import ExecutionStatus, StateTransitionRecord


class InvalidStateTransitionError(Exception):
    pass


ALLOWED_TRANSITIONS: Dict[ExecutionStatus, Set[ExecutionStatus]] = {
    ExecutionStatus.RECEIVED: {
        ExecutionStatus.VALIDATED,
        ExecutionStatus.REJECTED,
        ExecutionStatus.FAILED,
    },
    ExecutionStatus.VALIDATED: {
        ExecutionStatus.EVALUATING,
        ExecutionStatus.FAILED,
    },
    ExecutionStatus.EVALUATING: {
        ExecutionStatus.APPROVED,
        ExecutionStatus.REJECTED,
        ExecutionStatus.MANUAL_REVIEW,
        ExecutionStatus.RETRY_PENDING,
        ExecutionStatus.FAILED,
    },
    ExecutionStatus.RETRY_PENDING: {
        ExecutionStatus.EVALUATING,
        ExecutionStatus.FAILED,
    },
    ExecutionStatus.APPROVED: set(),
    ExecutionStatus.REJECTED: set(),
    ExecutionStatus.MANUAL_REVIEW: set(),
    ExecutionStatus.FAILED: set(),
}


class ExecutionStateMachine:
    """Enforces valid lifecycle transitions and records an append-only transition history."""

    def __init__(self, initial_history: Optional[List[StateTransitionRecord]] = None) -> None:
        self.history: List[StateTransitionRecord] = list(initial_history or [])
        self.current_state: Optional[ExecutionStatus] = (
            self.history[-1].to_state if self.history else None
        )

    def transition(
        self,
        to_state: ExecutionStatus,
        reason: str,
        step_id: Optional[str] = None,
    ) -> StateTransitionRecord:
        if self.current_state is not None:
            allowed = ALLOWED_TRANSITIONS.get(self.current_state, set())
            if to_state not in allowed:
                raise InvalidStateTransitionError(
                    f"Illegal transition from {self.current_state.value} to {to_state.value}"
                )

        record = StateTransitionRecord(
            from_state=self.current_state,
            to_state=to_state,
            step_id=step_id,
            reason=reason,
            timestamp=datetime.now(timezone.utc),
        )
        self.history.append(record)
        self.current_state = to_state
        return record
