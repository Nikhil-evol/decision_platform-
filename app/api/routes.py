from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.config_loader import WorkflowConfigError
from app.engine.orchestrator import (
    DecisionOrchestrator,
    ExecutionNotRetryableError,
    IdempotencyConflictError,
    WorkflowNotFoundError,
)
from app.schemas.workflow import (
    DecisionRequest,
    DecisionResponse,
    FailureInjectionRequest,
    RawWorkflowUpdateRequest,
)
from app.storage.repository import StorageSimulationError

router = APIRouter(prefix="/api/v1", tags=["ScoreMe Decision Platform"])


def get_orchestrator(request: Request) -> DecisionOrchestrator:
    return request.app.state.orchestrator


@router.get("/health")
def health_check() -> dict:
    return {"status": "ok", "service": "scoreme-workflow-decision-platform"}


@router.get("/workflows")
def list_workflows(orchestrator: DecisionOrchestrator = Depends(get_orchestrator)) -> dict:
    workflows = orchestrator.registry.list_workflows()
    return {
        "count": len(workflows),
        "workflows": [
            {
                "workflow_id": wf.workflow_id,
                "version": wf.version,
                "name": wf.name,
                "description": wf.description,
                "config_hash": wf.config_hash,
                "default_outcome": wf.default_outcome,
                "input_schema": {k: v.model_dump() for k, v in wf.input_schema.items()},
                "steps": [s.model_dump() for s in wf.steps],
            }
            for wf in workflows
        ],
    }


@router.get("/workflows/{workflow_id}/raw")
def get_workflow_raw(
    workflow_id: str,
    orchestrator: DecisionOrchestrator = Depends(get_orchestrator),
) -> dict:
    raw = orchestrator.registry.get_raw_yaml(workflow_id)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )
    wf = orchestrator.registry.get(workflow_id)
    return {
        "workflow_id": workflow_id,
        "version": wf.version if wf else "",
        "config_hash": wf.config_hash if wf else "",
        "yaml_content": raw,
    }


@router.put("/workflows/{workflow_id}/raw")
def update_workflow_raw(
    workflow_id: str,
    body: RawWorkflowUpdateRequest,
    orchestrator: DecisionOrchestrator = Depends(get_orchestrator),
) -> dict:
    try:
        updated = orchestrator.registry.update_raw_yaml(workflow_id, body.yaml_content)
        return {
            "updated": True,
            "workflow_id": updated.workflow_id,
            "version": updated.version,
            "config_hash": updated.config_hash,
        }
    except WorkflowConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/workflows/reload")
def reload_workflows(orchestrator: DecisionOrchestrator = Depends(get_orchestrator)) -> dict:
    orchestrator.registry.reload()
    return {
        "reloaded": True,
        "workflows": [
            {"workflow_id": wf.workflow_id, "version": wf.version, "config_hash": wf.config_hash}
            for wf in orchestrator.registry.list_workflows()
        ],
    }


@router.post("/decisions", response_model=DecisionResponse, status_code=status.HTTP_201_CREATED)
def submit_decision(
    body: DecisionRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    orchestrator: DecisionOrchestrator = Depends(get_orchestrator),
) -> DecisionResponse:
    try:
        return orchestrator.process_request(body, idempotency_key=idempotency_key)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except StorageSimulationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Atomic persistence failure (rolled back cleanly): {exc}",
        ) from exc


@router.get("/executions", response_model=List[DecisionResponse])
def list_executions(
    limit: int = 30,
    orchestrator: DecisionOrchestrator = Depends(get_orchestrator),
) -> List[DecisionResponse]:
    return orchestrator.repository.list_executions(limit=limit)


@router.get("/executions/{execution_id}", response_model=DecisionResponse)
def get_execution(
    execution_id: str,
    orchestrator: DecisionOrchestrator = Depends(get_orchestrator),
) -> DecisionResponse:
    found = orchestrator.repository.get_execution(execution_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution '{execution_id}' not found",
        )
    return found[1]


@router.post("/executions/{execution_id}/retry", response_model=DecisionResponse)
def retry_execution(
    execution_id: str,
    simulate_storage_failure: bool = False,
    orchestrator: DecisionOrchestrator = Depends(get_orchestrator),
) -> DecisionResponse:
    try:
        return orchestrator.retry_execution(
            execution_id=execution_id,
            simulate_storage_failure=simulate_storage_failure,
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ExecutionNotRetryableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except StorageSimulationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Atomic persistence failure on retry: {exc}",
        ) from exc


@router.get("/simulator/status")
def get_simulator_status(orchestrator: DecisionOrchestrator = Depends(get_orchestrator)) -> dict:
    return {"services": orchestrator.external_adapter.get_status_snapshot()}


@router.post("/simulator/failures")
def configure_simulator_failure(
    body: FailureInjectionRequest,
    orchestrator: DecisionOrchestrator = Depends(get_orchestrator),
) -> dict:
    orchestrator.external_adapter.configure_failure(
        service=body.service,
        fail_times=body.fail_times,
        permanent=body.permanent,
        error_message=body.error_message,
    )
    return {"configured": True, "services": orchestrator.external_adapter.get_status_snapshot()}


@router.post("/simulator/reset")
def reset_simulator(orchestrator: DecisionOrchestrator = Depends(get_orchestrator)) -> dict:
    orchestrator.external_adapter.reset_failures()
    return {"reset": True, "services": orchestrator.external_adapter.get_status_snapshot()}
