# System Architecture & Engineering Design

## 1. Problem Structuring & Architectural Boundaries

Financial and compliance decisioning systems fail in production when business policy gets tangled with orchestration code, or when partial external failures leave records in inconsistent states. This platform enforces four strict boundaries:

1. **Declarative Policy vs. Deterministic Execution Engine**: Domain schemas, external enrichment steps, threshold rules, and branching directives live exclusively in YAML configurations (`configs/workflows/*.yaml`). The Python engine contains zero domain-specific `if/else` underwriting or GST logic.
2. **Explicit Lifecycle State Machine (`app/engine/state_machine.py`)**: Every execution follows a directed state graph (`RECEIVED -> VALIDATED -> EVALUATING -> terminal/retryable state`). Illegal transitions (such as transitioning out of `APPROVED` or `REJECTED`) are blocked at the domain layer.
3. **Atomic Persistence (`app/storage/repository.py`)**: Execution status, state transitions, and step-by-step rule audit records commit inside a single SQLite `BEGIN IMMEDIATE` transaction. A decision cannot be persisted without its audit trail.
4. **Resilience Under Partial & External Failures**: Upstream dependencies are isolated behind `SimulatedExternalAdapter` with bounded exponential-backoff retries, persistent `RETRY_PENDING` checkpoints, and SHA-256 payload-fingerprinted idempotency.

---

## 2. Component Architecture

```text
+-------------------------------------------------------------------+
|                   Client / Evaluator / Operator                   |
|        (ScoreMe Web Studio UI, REST API Client, or CLI)           |
+---------------------------------+---------------------------------+
                                  | POST /api/v1/decisions
                                  | Header: Idempotency-Key
                                  v
+-------------------------------------------------------------------+
|                     API Layer (app/api/routes.py)                 |
|  - HTTP transport, status mapping (201, 400, 404, 409, 503)       |
+---------------------------------+---------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|               Decision Orchestrator (app/engine/orchestrator.py)  |
|  1. Idempotency lookup & canonical SHA-256 payload fingerprinting |
|  2. Workflow lookup & mtime hot-reload (WorkflowRegistry)         |
|  3. Declarative input validation (validate_payload_against_schema)|
|  4. Step loop: external enrichment + safe AST rule evaluation     |
|  5. Atomic DB commit of state history + rule audit trace          |
+--------+------------------------+------------------------+--------+
         |                        |                        |
         v                        v                        v
+-----------------+    +--------------------+    +------------------+
| WorkflowRegistry|    |  SafeExpression    |    | ExternalAdapter  |
| (YAML + SHA256) |    |    Evaluator (AST)   |    | (Bureau/GST/KYC) |
+-----------------+    +--------------------+    +------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|           Execution Repository (app/storage/repository.py)        |
|  - SQLite WAL mode + BEGIN IMMEDIATE atomic transactions          |
|  - Tables: executions, state_transitions, audit_entries           |
+-------------------------------------------------------------------+
```

---

## 3. End-to-End Request & Failure Handling Flow

1. **Idempotency Deduplication**:
   - Resolves `idempotency_key` from the `Idempotency-Key` HTTP header (falling back to `request_id` or a generated UUID).
   - Computes a canonical SHA-256 digest of `(workflow_id, sorted_payload)`.
   - If the key exists with the same `payload_hash`, returns the stored `DecisionResponse` immediately (`idempotent_replay: true`) without re-invoking external services.
   - If the key exists with a different `payload_hash`, rejects with `HTTP 409 Conflict` to prevent silent data corruption from key reuse.
2. **Input Schema Validation**:
   - Validates types (`string`, `int`, `float`, `bool`, `list`—explicitly guarding against Python's `isinstance(True, int)` quirk), required flags, numeric bounds (`min_value`, `max_value`), and allowed enums.
   - Invalid inputs transition `RECEIVED -> REJECTED`, record a `schema_validation` audit entry, and commit atomically.
3. **Multi-Step Execution & External Dependency Resilience**:
   - Transitions `RECEIVED -> VALIDATED -> EVALUATING`.
   - For `external_check` steps, invokes the registered provider (`bureau_bsa_aggregator`, `gstn_analyzer`, `ckyc_registry`) with bounded exponential backoff (`max_retries`).
   - If transient errors clear within `max_retries`, returned attributes merge into the evaluation context (`enriched_data`) and the workflow continues seamlessly.
   - If retries exhaust (or a permanent failure occurs), the execution transitions to the step's `on_failure` state (`RETRY_PENDING` or `MANUAL_REVIEW`), persists its full trace, and can be resumed later via `POST /api/v1/executions/{execution_id}/retry`.
4. **Safe AST Rule Evaluation & Branching**:
   - Parses rule expressions via `ast.parse(..., mode="eval")` against a strict allowlist of AST nodes (`Compare`, `BoolOp`, `BinOp`, `UnaryOp`, `Name`, `Constant`, `List`, `Tuple`, `Set`).
   - Records every variable accessed (`data_referenced`) during evaluation into the `AuditEntry`.
   - Routes execution according to `on_pass` / `on_fail` (`NEXT`, `APPROVE`, `REJECT`, `MANUAL_REVIEW`, or jumping to a target `step_id`), guarded by a `MAX_STEP_TRANSITIONS = 50` circuit breaker against misconfigured cycles.
5. **Atomic Transaction Guarantee**:
   - `save_execution_atomic()` writes `executions`, `state_transitions`, and `audit_entries` inside a single `BEGIN IMMEDIATE` SQLite transaction. Any mid-flight storage error rolls back all tables cleanly.

---

## 4. Key Engineering Trade-Offs

| Decision | Chosen Approach | Alternative Considered | Trade-Off & Justification |
| :--- | :--- | :--- | :--- |
| **Rule Engine** | Restricted Python AST expression evaluator over YAML | Python `eval()` or heavy external rule servers (Drools/OPA) | `eval()` creates severe arbitrary code execution vulnerabilities. External rule servers add deployment friction. An AST allowlist provides full arithmetic/boolean expressiveness, zero external dependencies, and automatic variable extraction for audit trails. |
| **Storage Engine** | SQLite in WAL mode with `BEGIN IMMEDIATE` transactions | In-memory dicts or external PostgreSQL/Redis cluster | In-memory dicts lose durability and cannot prove atomic rollback on partial save failures. SQLite runs zero-config on Windows/Linux while enforcing real ACID transactions and foreign-key integrity. |
| **Retry Strategy** | Inline bounded retries + persistent `RETRY_PENDING` state with `/retry` API | Long synchronous HTTP waits or mandatory Kafka/Celery cluster | Holding HTTP connections open for minutes degrades API availability; requiring external brokers complicates local setup. Hybrid bounded retries absorb brief blips immediately while checkpointing prolonged outages for clean recovery. |
| **Idempotency** | Client `Idempotency-Key` + SHA-256 canonical payload hash | Deduplicating solely on key string without payload comparison | Key-only deduplication hides client bugs when an idempotency key is reused with altered loan amounts. Payload fingerprinting safely replays identical retries (`201`) and rejects mutated payloads (`409 Conflict`). |
| **Config Versioning** | Live YAML `mtime` reload + `PUT /workflows/{id}/raw` + `config_hash` pinning | Hardcoded rules or unversioned reloads | Pinning the 16-char SHA-256 `config_hash` on every execution allows instant rule updates without restarts while guaranteeing historical decisions remain traceable to the exact rule snapshot active at evaluation time. |

---

## 5. Production Scaling Considerations

1. **Multi-Instance Database Concurrency**: Swap `ExecutionRepository` to PostgreSQL (`asyncpg`) and enforce atomic idempotency via `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING` so concurrent duplicate bursts across stateless API pods never race.
2. **Asynchronous Retry Workers**: Replace manual `/retry` triggers with a background worker fleet polling `RETRY_PENDING` executions via `SELECT ... FOR UPDATE SKIP LOCKED` (or an SQS/Kafka dead-letter retry topic).
3. **Centralized Config Store**: Store workflow YAML versions in PostgreSQL or S3 with Redis pub/sub cache invalidation so all API nodes receive rule updates simultaneously.
4. **Audit Table Partitioning**: Partition `audit_entries` and `state_transitions` by month (`created_at`) and archive historical records to object storage (Parquet/S3) for regulatory compliance.
