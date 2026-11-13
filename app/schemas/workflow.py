from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    EVALUATING = "EVALUATING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    RETRY_PENDING = "RETRY_PENDING"
    FAILED = "FAILED"


TERMINAL_STATUSES = {
    ExecutionStatus.APPROVED,
    ExecutionStatus.REJECTED,
    ExecutionStatus.MANUAL_REVIEW,
    ExecutionStatus.FAILED,
}


class FieldSpec(BaseModel):
    type: Literal["string", "int", "float", "bool", "list"]
    required: bool = True
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allowed_values: Optional[List[Any]] = None


class ExternalCheckSpec(BaseModel):
    id: str
    service: str
    input_fields: List[str] = Field(default_factory=list)
    max_retries: int = 2
    retry_backoff_ms: int = 40
    on_failure: Literal["RETRY_PENDING", "MANUAL_REVIEW", "REJECTED"] = "RETRY_PENDING"


class RuleSpec(BaseModel):
    id: str
    name: str
    category: Literal["mandatory", "threshold", "branch", "compliance"] = "threshold"
    expression: str
    on_pass: str = "NEXT"  # "NEXT", "APPROVE", "MANUAL_REVIEW", "REJECT", or target step_id
    on_fail: str = "REJECT"  # "NEXT", "APPROVE", "MANUAL_REVIEW", "REJECT", or target step_id
    fail_reason: str
    pass_reason: Optional[str] = None


class WorkflowStepSpec(BaseModel):
    id: str
    name: str
    step_type: Literal["external_check", "rule_group"]
    external_check: Optional[ExternalCheckSpec] = None
    rules: List[RuleSpec] = Field(default_factory=list)


class WorkflowConfig(BaseModel):
    workflow_id: str
    version: str
    name: str
    description: str
    input_schema: Dict[str, FieldSpec]
    steps: List[WorkflowStepSpec]
    default_outcome: Literal["APPROVED", "REJECTED", "MANUAL_REVIEW"] = "APPROVED"
    config_hash: str = ""


class DecisionRequest(BaseModel):
    workflow_id: str = Field(..., min_length=1)
    request_id: Optional[str] = Field(
        default=None,
        description="Optional client request ID; used as idempotency key if Idempotency-Key header is omitted.",
    )
    payload: Dict[str, Any]
    simulate_storage_failure: bool = Field(
        default=False,
        description="Fault-injection flag to simulate a mid-transaction DB error and verify atomic rollback.",
    )


class StateTransitionRecord(BaseModel):
    from_state: Optional[ExecutionStatus]
    to_state: ExecutionStatus
    step_id: Optional[str] = None
    reason: str
    timestamp: datetime


class AuditEntry(BaseModel):
    sequence: int
    step_id: str
    rule_id: str
    rule_name: str
    category: str
    expression: str
    data_referenced: Dict[str, Any]
    passed: bool
    action_taken: str
    reasoning: str
    timestamp: datetime


class DecisionResponse(BaseModel):
    execution_id: str
    idempotency_key: str
    workflow_id: str
    workflow_version: str
    config_hash: str
    status: ExecutionStatus
    idempotent_replay: bool = False
    retry_count: int = 0
    decision_summary: str
    enriched_data: Dict[str, Any] = Field(default_factory=dict)
    audit_trail: List[AuditEntry] = Field(default_factory=list)
    state_history: List[StateTransitionRecord] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class FailureInjectionRequest(BaseModel):
    service: str
    fail_times: int = Field(default=0, ge=0)
    permanent: bool = False
    error_message: str = "Simulated upstream 503 Service Unavailable"


class RawWorkflowUpdateRequest(BaseModel):
    yaml_content: str = Field(..., min_length=10)
