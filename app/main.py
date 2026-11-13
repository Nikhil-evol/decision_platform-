from __future__ import annotations

import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.adapters.external_services import SimulatedExternalAdapter
from app.api.routes import router
from app.config_loader import WorkflowRegistry
from app.engine.orchestrator import DecisionOrchestrator
from app.storage.repository import ExecutionRepository

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    config_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> FastAPI:
    resolved_config_dir = config_dir or os.getenv("WORKFLOW_CONFIG_DIR", "configs/workflows")
    resolved_db_path = db_path or os.getenv("DECISION_DB_PATH", "scoreme_decisions.db")

    app = FastAPI(
        title="ScoreMe Configurable Workflow Decision Platform",
        version="1.0.0",
        description="Deterministic, auditable lending & compliance workflow decision engine.",
    )

    registry = WorkflowRegistry(config_dir=resolved_config_dir)
    repository = ExecutionRepository(db_path=resolved_db_path)
    external_adapter = SimulatedExternalAdapter()
    orchestrator = DecisionOrchestrator(
        registry=registry,
        repository=repository,
        external_adapter=external_adapter,
    )

    app.state.registry = registry
    app.state.repository = repository
    app.state.external_adapter = external_adapter
    app.state.orchestrator = orchestrator

    app.include_router(router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def serve_console() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
