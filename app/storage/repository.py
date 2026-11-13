from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.schemas.workflow import (
    AuditEntry,
    DecisionResponse,
    ExecutionStatus,
    StateTransitionRecord,
)


class StorageSimulationError(Exception):
    """Raised when a simulated database write fault occurs mid-transaction."""


class ExecutionRepository:
    """
    SQLite-backed repository using WAL mode and explicit BEGIN IMMEDIATE transactions
    so execution state, transition logs, and rule audit trails persist atomically.
    """

    def __init__(self, db_path: str | Path = "scoreme_decisions.db") -> None:
        self.db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    payload_hash TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workflow_version TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    decision_summary TEXT NOT NULL,
                    request_payload_json TEXT NOT NULL,
                    enriched_data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS state_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    step_id TEXT,
                    reason TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (execution_id) REFERENCES executions(execution_id)
                );

                CREATE TABLE IF NOT EXISTS audit_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    step_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    expression TEXT NOT NULL,
                    data_referenced_json TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    action_taken TEXT NOT NULL,
                    reasoning TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (execution_id) REFERENCES executions(execution_id)
                );
                """
            )
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def find_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[tuple[str, DecisionResponse]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM executions WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if not row:
                return None
            return row["payload_hash"], self._hydrate_execution(conn, row)
        finally:
            conn.close()

    def get_execution(self, execution_id: str) -> Optional[tuple[Dict[str, Any], DecisionResponse]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if not row:
                return None
            raw_payload = json.loads(row["request_payload_json"])
            return raw_payload, self._hydrate_execution(conn, row)
        finally:
            conn.close()

    def list_executions(self, limit: int = 50) -> List[DecisionResponse]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM executions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._hydrate_execution(conn, r) for r in rows]
        finally:
            conn.close()

    def save_execution_atomic(
        self,
        record: DecisionResponse,
        payload_hash: str,
        request_payload: Dict[str, Any],
        simulate_storage_failure: bool = False,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO executions (
                    execution_id, idempotency_key, payload_hash, workflow_id,
                    workflow_version, config_hash, status, retry_count,
                    decision_summary, request_payload_json, enriched_data_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(execution_id) DO UPDATE SET
                    status = excluded.status,
                    retry_count = excluded.retry_count,
                    decision_summary = excluded.decision_summary,
                    enriched_data_json = excluded.enriched_data_json,
                    updated_at = excluded.updated_at
                """,
                (
                    record.execution_id,
                    record.idempotency_key,
                    payload_hash,
                    record.workflow_id,
                    record.workflow_version,
                    record.config_hash,
                    record.status.value,
                    record.retry_count,
                    record.decision_summary,
                    json.dumps(request_payload),
                    json.dumps(record.enriched_data),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )

            conn.execute(
                "DELETE FROM state_transitions WHERE execution_id = ?",
                (record.execution_id,),
            )
            for tr in record.state_history:
                conn.execute(
                    """
                    INSERT INTO state_transitions (
                        execution_id, from_state, to_state, step_id, reason, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.execution_id,
                        tr.from_state.value if tr.from_state else None,
                        tr.to_state.value,
                        tr.step_id,
                        tr.reason,
                        tr.timestamp.isoformat(),
                    ),
                )

            if simulate_storage_failure:
                raise StorageSimulationError(
                    "Simulated disk/DB failure before audit trail commit; rolling back transaction."
                )

            conn.execute(
                "DELETE FROM audit_entries WHERE execution_id = ?",
                (record.execution_id,),
            )
            for entry in record.audit_trail:
                conn.execute(
                    """
                    INSERT INTO audit_entries (
                        execution_id, sequence, step_id, rule_id, rule_name,
                        category, expression, data_referenced_json, passed,
                        action_taken, reasoning, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.execution_id,
                        entry.sequence,
                        entry.step_id,
                        entry.rule_id,
                        entry.rule_name,
                        entry.category,
                        entry.expression,
                        json.dumps(entry.data_referenced),
                        1 if entry.passed else 0,
                        entry.action_taken,
                        entry.reasoning,
                        entry.timestamp.isoformat(),
                    ),
                )

    def _hydrate_execution(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> DecisionResponse:
        exec_id = row["execution_id"]
        tr_rows = conn.execute(
            "SELECT * FROM state_transitions WHERE execution_id = ? ORDER BY id ASC",
            (exec_id,),
        ).fetchall()
        audit_rows = conn.execute(
            "SELECT * FROM audit_entries WHERE execution_id = ? ORDER BY sequence ASC",
            (exec_id,),
        ).fetchall()

        state_history = [
            StateTransitionRecord(
                from_state=ExecutionStatus(r["from_state"]) if r["from_state"] else None,
                to_state=ExecutionStatus(r["to_state"]),
                step_id=r["step_id"],
                reason=r["reason"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
            )
            for r in tr_rows
        ]

        audit_trail = [
            AuditEntry(
                sequence=r["sequence"],
                step_id=r["step_id"],
                rule_id=r["rule_id"],
                rule_name=r["rule_name"],
                category=r["category"],
                expression=r["expression"],
                data_referenced=json.loads(r["data_referenced_json"]),
                passed=bool(r["passed"]),
                action_taken=r["action_taken"],
                reasoning=r["reasoning"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
            )
            for r in audit_rows
        ]

        return DecisionResponse(
            execution_id=exec_id,
            idempotency_key=row["idempotency_key"],
            workflow_id=row["workflow_id"],
            workflow_version=row["workflow_version"],
            config_hash=row["config_hash"],
            status=ExecutionStatus(row["status"]),
            idempotent_replay=False,
            retry_count=row["retry_count"],
            decision_summary=row["decision_summary"],
            enriched_data=json.loads(row["enriched_data_json"]),
            audit_trail=audit_trail,
            state_history=state_history,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
