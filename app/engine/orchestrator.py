from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.adapters.external_services import ExternalDependencyError, SimulatedExternalAdapter
from app.config_loader import WorkflowRegistry
from app.engine.evaluator import (
    InputValidationError,
    RuleEvaluationError,
    SafeExpressionEvaluator,
    validate_payload_against_schema,
)
from app.engine.state_machine import ExecutionStateMachine
from app.schemas.workflow import (
    AuditEntry,
    DecisionRequest,
    DecisionResponse,
    ExecutionStatus,
    WorkflowConfig,
)
from app.storage.repository import ExecutionRepository


class IdempotencyConflictError(Exception):
    pass


class WorkflowNotFoundError(Exception):
    pass


class ExecutionNotRetryableError(Exception):
    pass


class DecisionOrchestrator:
    """
    Coordinates request intake, idempotency deduplication, schema validation,
    external enrichment with bounded retries, AST rule evaluation, state machine
    transitions, and atomic audit persistence.
    """

    MAX_STEP_TRANSITIONS = 50

    def __init__(
        self,
        registry: WorkflowRegistry,
        repository: ExecutionRepository,
        external_adapter: SimulatedExternalAdapter,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.external_adapter = external_adapter
        self.evaluator = SafeExpressionEvaluator()

    @staticmethod
    def compute_payload_hash(workflow_id: str, payload: Dict[str, Any]) -> str:
        canonical = json.dumps(
            {"workflow_id": workflow_id, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def process_request(
        self,
        request: DecisionRequest,
        idempotency_key: Optional[str] = None,
    ) -> DecisionResponse:
        effective_key = (
            idempotency_key
            or request.request_id
            or f"auto-{uuid.uuid4().hex}"
        )
        payload_hash = self.compute_payload_hash(request.workflow_id, request.payload)

        # 1. Idempotency check
        existing = self.repository.find_by_idempotency_key(effective_key)
        if existing is not None:
            stored_hash, stored_response = existing
            if stored_hash != payload_hash:
                raise IdempotencyConflictError(
                    f"Idempotency key '{effective_key}' already exists with a different payload."
                )
            replay = stored_response.model_copy(deep=True)
            replay.idempotent_replay = True
            return replay

        # 2. Load workflow definition
        workflow = self.registry.get(request.workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"Unknown workflow_id '{request.workflow_id}'")

        now = datetime.now(timezone.utc)
        execution_id = f"exec_{uuid.uuid4().hex[:12]}"
        fsm = ExecutionStateMachine()
        fsm.transition(ExecutionStatus.RECEIVED, "Request accepted for processing")

        # 3. Validate input schema
        try:
            validate_payload_against_schema(request.payload, workflow.input_schema)
        except InputValidationError as exc:
            fsm.transition(
                ExecutionStatus.REJECTED,
                f"Schema validation failed: {exc}",
                step_id="input_validation",
            )
            audit_entry = AuditEntry(
                sequence=1,
                step_id="input_validation",
                rule_id="schema_validation",
                rule_name="Input Schema Validation",
                category="mandatory",
                expression="payload matches workflow.input_schema",
                data_referenced={"provided_keys": sorted(request.payload.keys())},
                passed=False,
                action_taken="REJECTED",
                reasoning=f"Input validation failed: {exc}",
                timestamp=datetime.now(timezone.utc),
            )
            response = DecisionResponse(
                execution_id=execution_id,
                idempotency_key=effective_key,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.version,
                config_hash=workflow.config_hash,
                status=ExecutionStatus.REJECTED,
                retry_count=0,
                decision_summary=f"Rejected at intake due to invalid input: {exc}",
                enriched_data={},
                audit_trail=[audit_entry],
                state_history=fsm.history,
                created_at=now,
                updated_at=datetime.now(timezone.utc),
            )
            self.repository.save_execution_atomic(
                response,
                payload_hash=payload_hash,
                request_payload=request.payload,
                simulate_storage_failure=request.simulate_storage_failure,
            )
            return response

        fsm.transition(ExecutionStatus.VALIDATED, "Input payload validated against workflow schema")
        fsm.transition(ExecutionStatus.EVALUATING, "Starting workflow step execution")

        return self._execute_workflow(
            workflow=workflow,
            execution_id=execution_id,
            idempotency_key=effective_key,
            payload_hash=payload_hash,
            raw_payload=request.payload,
            fsm=fsm,
            created_at=now,
            prior_retry_count=0,
            simulate_storage_failure=request.simulate_storage_failure,
        )

    def retry_execution(
        self,
        execution_id: str,
        simulate_storage_failure: bool = False,
    ) -> DecisionResponse:
        found = self.repository.get_execution(execution_id)
        if found is None:
            raise WorkflowNotFoundError(f"Execution '{execution_id}' not found")

        raw_payload, existing = found
        if existing.status != ExecutionStatus.RETRY_PENDING:
            raise ExecutionNotRetryableError(
                f"Execution '{execution_id}' is in non-retryable state '{existing.status.value}'"
            )

        workflow = self.registry.get(existing.workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"Workflow '{existing.workflow_id}' no longer registered")

        fsm = ExecutionStateMachine(initial_history=existing.state_history)
        next_retry = existing.retry_count + 1
        fsm.transition(
            ExecutionStatus.EVALUATING,
            f"Retrying workflow execution (retry attempt #{next_retry})",
        )

        payload_hash = self.compute_payload_hash(existing.workflow_id, raw_payload)
        return self._execute_workflow(
            workflow=workflow,
            execution_id=existing.execution_id,
            idempotency_key=existing.idempotency_key,
            payload_hash=payload_hash,
            raw_payload=raw_payload,
            fsm=fsm,
            created_at=existing.created_at,
            prior_retry_count=next_retry,
            simulate_storage_failure=simulate_storage_failure,
        )

    def _execute_workflow(
        self,
        workflow: WorkflowConfig,
        execution_id: str,
        idempotency_key: str,
        payload_hash: str,
        raw_payload: Dict[str, Any],
        fsm: ExecutionStateMachine,
        created_at: datetime,
        prior_retry_count: int,
        simulate_storage_failure: bool,
    ) -> DecisionResponse:
        context: Dict[str, Any] = dict(raw_payload)
        enriched_data: Dict[str, Any] = {}
        audit_trail: List[AuditEntry] = []
        seq = 1

        step_index_map = {step.id: idx for idx, step in enumerate(workflow.steps)}
        current_idx = 0
        step_hops = 0
        final_status: Optional[ExecutionStatus] = None
        decision_summary = ""

        while current_idx < len(workflow.steps):
            step_hops += 1
            if step_hops > self.MAX_STEP_TRANSITIONS:
                final_status = ExecutionStatus.FAILED
                decision_summary = (
                    f"Circuit breaker tripped: exceeded {self.MAX_STEP_TRANSITIONS} step hops "
                    f"(possible cycle in workflow '{workflow.workflow_id}')."
                )
                fsm.transition(final_status, decision_summary)
                break

            step = workflow.steps[current_idx]

            if step.step_type == "external_check" and step.external_check is not None:
                ext = step.external_check
                ext_input = {k: context.get(k) for k in ext.input_fields if k in context}
                success = False
                last_error = ""
                attempts_used = 0

                for attempt in range(ext.max_retries + 1):
                    attempts_used = attempt + 1
                    try:
                        result = self.external_adapter.invoke(ext.service, ext_input)
                        context.update(result)
                        enriched_data.update(result)
                        success = True
                        break
                    except ExternalDependencyError as exc:
                        last_error = str(exc)
                        if not exc.transient or attempt >= ext.max_retries:
                            break
                        time.sleep((ext.retry_backoff_ms * (2**attempt)) / 1000.0)

                if success:
                    audit_trail.append(
                        AuditEntry(
                            sequence=seq,
                            step_id=step.id,
                            rule_id=ext.id,
                            rule_name=f"External Analyzer: {ext.service}",
                            category="external_check",
                            expression=f"invoke({ext.service})",
                            data_referenced={"inputs": ext_input, "outputs": dict(enriched_data)},
                            passed=True,
                            action_taken="CONTINUE",
                            reasoning=(
                                f"External analyzer '{ext.service}' succeeded in {attempts_used} "
                                f"attempt(s), enriching context with {list(enriched_data.keys())}."
                            ),
                            timestamp=datetime.now(timezone.utc),
                        )
                    )
                    seq += 1
                    current_idx += 1
                    continue

                target_status = ExecutionStatus(ext.on_failure)
                audit_trail.append(
                    AuditEntry(
                        sequence=seq,
                        step_id=step.id,
                        rule_id=ext.id,
                        rule_name=f"External Analyzer: {ext.service}",
                        category="external_check",
                        expression=f"invoke({ext.service})",
                        data_referenced={"inputs": ext_input, "attempts": attempts_used},
                        passed=False,
                        action_taken=target_status.value,
                        reasoning=(
                            f"External analyzer '{ext.service}' failed after {attempts_used} "
                            f"attempt(s): {last_error}. Routing execution to {target_status.value}."
                        ),
                        timestamp=datetime.now(timezone.utc),
                    )
                )
                final_status = target_status
                decision_summary = (
                    f"Halted at step '{step.id}' ({ext.service}): {last_error}. "
                    f"State set to {target_status.value}."
                )
                fsm.transition(target_status, decision_summary, step_id=step.id)
                break

            # Rule group evaluation
            jumped = False
            for rule in step.rules:
                try:
                    passed, referenced = self.evaluator.evaluate(rule.expression, context)
                except RuleEvaluationError as exc:
                    final_status = ExecutionStatus.FAILED
                    decision_summary = (
                        f"Rule evaluation error in step '{step.id}', rule '{rule.id}': {exc}"
                    )
                    fsm.transition(final_status, decision_summary, step_id=step.id)
                    break

                directive = rule.on_pass if passed else rule.on_fail
                reasoning = (
                    (rule.pass_reason or f"Rule '{rule.name}' passed ({rule.expression}).")
                    if passed
                    else f"{rule.fail_reason} [Observed: {referenced}]"
                )

                audit_trail.append(
                    AuditEntry(
                        sequence=seq,
                        step_id=step.id,
                        rule_id=rule.id,
                        rule_name=rule.name,
                        category=rule.category,
                        expression=rule.expression,
                        data_referenced=referenced,
                        passed=passed,
                        action_taken=directive,
                        reasoning=reasoning,
                        timestamp=datetime.now(timezone.utc),
                    )
                )
                seq += 1

                if directive == "NEXT":
                    continue
                if directive in ("APPROVE", "APPROVED"):
                    final_status = ExecutionStatus.APPROVED
                    decision_summary = f"Approved at step '{step.id}' via rule '{rule.id}': {reasoning}"
                    fsm.transition(final_status, decision_summary, step_id=step.id)
                    break
                if directive in ("REJECT", "REJECTED"):
                    final_status = ExecutionStatus.REJECTED
                    decision_summary = f"Rejected at step '{step.id}' via rule '{rule.id}': {reasoning}"
                    fsm.transition(final_status, decision_summary, step_id=step.id)
                    break
                if directive == "MANUAL_REVIEW":
                    final_status = ExecutionStatus.MANUAL_REVIEW
                    decision_summary = (
                        f"Escalated to manual review at step '{step.id}' via rule '{rule.id}': {reasoning}"
                    )
                    fsm.transition(final_status, decision_summary, step_id=step.id)
                    break
                if directive in step_index_map:
                    current_idx = step_index_map[directive]
                    jumped = True
                    break

            if final_status is not None:
                break
            if not jumped:
                current_idx += 1

        if final_status is None:
            final_status = ExecutionStatus(workflow.default_outcome)
            decision_summary = (
                f"Completed all workflow stages for '{workflow.workflow_id}' (v{workflow.version}); "
                f"outcome: {final_status.value}."
            )
            fsm.transition(final_status, decision_summary)

        response = DecisionResponse(
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            config_hash=workflow.config_hash,
            status=final_status,
            idempotent_replay=False,
            retry_count=prior_retry_count,
            decision_summary=decision_summary,
            enriched_data=enriched_data,
            audit_trail=audit_trail,
            state_history=fsm.history,
            created_at=created_at,
            updated_at=datetime.now(timezone.utc),
        )

        self.repository.save_execution_atomic(
            response,
            payload_hash=payload_hash,
            request_payload=raw_payload,
            simulate_storage_failure=simulate_storage_failure,
        )
        return response
