# ScoreMe — Configurable Workflow Decision Platform

A deterministic, configuration-driven workflow and rule decision engine built with **Python**, **FastAPI**, and **SQLite**, featuring an interactive **ScoreMe Decision Studio Web UI**, CLI runner, and automated test suite.

It processes structured credit, GST vendor onboarding, and CKYC verification workflows—evaluating declarative YAML rules, enriching context via external service adapters, enforcing payload-fingerprinted idempotency, handling transient and prolonged dependency outages, and recording atomic audit trails.

---

## Project Structure

```text
scoreme-decision-platform/
├── app/
│   ├── adapters/
│   │   └── external_services.py   # Simulated Bureau/BSA, GSTN & CKYC analyzers + fault injection
│   ├── api/
│   │   └── routes.py              # REST API endpoints (/decisions, /executions, /workflows, /simulator)
│   ├── engine/
│   │   ├── evaluator.py           # Declarative schema validator + safe AST expression evaluator
│   │   ├── orchestrator.py        # Idempotency, retries, step branching, and atomic persistence
│   │   └── state_machine.py       # Explicit lifecycle FSM & transition guardrails
│   ├── schemas/
│   │   └── workflow.py            # Pydantic v2 models for configs, requests, states, and audit logs
│   ├── static/
│   │   ├── index.html             # ScoreMe Decision Studio Web UI (zero build step)
│   │   ├── styles.css
│   │   └── app.js
│   ├── storage/
│   │   └── repository.py          # SQLite repository (WAL mode + BEGIN IMMEDIATE atomic commits)
│   ├── config_loader.py           # Hot-reloadable YAML registry with SHA-256 config versioning
│   └── main.py                    # FastAPI application factory & static UI mounting
├── configs/
│   └── workflows/
│       ├── msme_credit_underwriting.yaml
│       ├── gst_vendor_onboarding.yaml
│       └── kyc_document_verification.yaml
├── docs/
│   └── DECISION_EXAMPLES.md       # Concrete input -> rule trace -> audit explanation examples
├── scripts/
│   └── cli.py                     # CLI runner for headless execution
├── tests/
│   ├── conftest.py
│   ├── test_happy_path_and_branching.py
│   ├── test_invalid_input_and_rejections.py
│   ├── test_idempotency.py
│   ├── test_failures_and_retries.py
│   └── test_rule_change_configurability.py
├── ARCHITECTURE.md                # System design, data flow, trade-offs, and scaling considerations
├── start_windows.bat              # One-click Windows setup & server + UI launcher
├── run_tests.bat                  # One-click Windows pytest runner
└── requirements.txt
```

---

## Quick Start on Windows (PowerShell / CMD)

### Option A: One-Click Windows Launcher
Double-click **`start_windows.bat`** (or run `.\start_windows.bat` in Command Prompt / PowerShell). It automatically creates `.venv`, installs dependencies, starts the FastAPI server on `http://127.0.0.1:8000`, and opens the **ScoreMe Decision Studio Web UI** in your browser.

### Option B: Manual PowerShell Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run automated test suite
pytest -v

# Start REST API + Web UI
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- **Web Console UI**: `http://127.0.0.1:8000/`
- **Interactive Swagger API Docs**: `http://127.0.0.1:8000/docs`

### Headless CLI Execution (PowerShell)

```powershell
python scripts/cli.py `
  --workflow msme_credit_underwriting `
  --idempotency-key cli-msme-001 `
  --payload '{"applicant_name":"Aarav Sharma","age":32,"monthly_net_inflow":145000,"existing_monthly_emi":15000,"proposed_emi":28000,"loan_amount":800000,"kyc_verified":true,"borrower_segment":"msme_proprietor"}'
```

---

## REST API Reference

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `GET` | `/` | Serves the interactive ScoreMe Decision Studio Web UI |
| `GET` | `/api/v1/health` | Service health check |
| `GET` | `/api/v1/workflows` | List loaded YAML workflows, input schemas, steps, and `config_hash` |
| `GET` / `PUT` | `/api/v1/workflows/{workflow_id}/raw` | Inspect or live-edit workflow YAML rules with schema validation |
| `POST` | `/api/v1/workflows/reload` | Hot-reload workflow YAML files from disk |
| `POST` | `/api/v1/decisions` | Evaluate a workflow request (supports `Idempotency-Key` header) |
| `GET` | `/api/v1/executions` | List persisted executions in SQLite |
| `GET` | `/api/v1/executions/{execution_id}` | Fetch full state transition history and rule audit trace |
| `POST` | `/api/v1/executions/{execution_id}/retry` | Resume an execution halted in `RETRY_PENDING` |
| `POST` | `/api/v1/simulator/failures` | Inject transient or permanent faults on simulated external analyzers |
| `POST` | `/api/v1/simulator/reset` | Clear simulated faults and call counters |
