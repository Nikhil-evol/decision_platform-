#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app
from app.schemas.workflow import DecisionRequest


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="CLI for ScoreMe Configurable Workflow Decision Platform"
    )
    parser.add_argument("--workflow", required=True, help="Workflow ID (e.g., msme_credit_underwriting)")
    parser.add_argument("--payload", required=True, help="JSON string of input payload")
    parser.add_argument("--idempotency-key", default=None, help="Optional idempotency key")
    parser.add_argument("--db", default="scoreme_decisions.db", help="SQLite DB path")
    args = parser.parse_args()

    app = create_app(db_path=args.db)
    orchestrator = app.state.orchestrator

    req = DecisionRequest(
        workflow_id=args.workflow,
        payload=json.loads(args.payload),
    )
    result = orchestrator.process_request(req, idempotency_key=args.idempotency_key)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
