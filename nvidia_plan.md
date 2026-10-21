# NVIDIA Incident Response Agent: Comprehensive Execution Checklist (nvidia_plan.md)

This document provides an exhaustive, step-by-step checklist of all components, files, functions, schemas, and tests to be built.

---

## Step 1: NVIDIA Nemotron Model Tier Architecture

### 1.1 Configuration & Environment Setup
- [x] **Configure Environment Variables** in `Copilot/agent/config.py`:
  - [x] Add `NVIDIA_API_KEY`: API key for NVIDIA NIM (`https://integrate.api.nvidia.com/v1`).
  - [x] Add `NEMOTRON_NANO_MODEL`: Default `nvidia/nemotron-3-nano-30b-a3b` (or `nvidia/nemotron-3-nano`).
  - [x] Add `NEMOTRON_SUPER_MODEL`: Default `nvidia/nemotron-3-super-120b-a12b` (or `nvidia/nemotron-3.5-super-vl-120b-a12b`).
  - [x] Add `NEMOTRON_ULTRA_MODEL`: Default `nvidia/nemotron-3-ultra-550b-a55b` (or `nvidia/llama-3.1-nemotron-70b-instruct`).
  - [x] Add `NVIDIA_BASE_URL`: Default `https://integrate.api.nvidia.com/v1`.
- [x] **Update `.env.example`** with clear instructions for `NVIDIA_API_KEY` and the three Nemotron model slugs.

### 1.2 Model Factory & LiteLLM / NIM Integration
- [x] **Refactor `Copilot/agent/llm.py`**:
  - [x] Implement `build_nemotron_model(tier: str)` supporting:
    - `"nano"` $\rightarrow$ uses `NEMOTRON_NANO_MODEL`
    - `"super"` $\rightarrow$ uses `NEMOTRON_SUPER_MODEL`
    - `"ultra"` $\rightarrow$ uses `NEMOTRON_ULTRA_MODEL`
  - [x] Configure LiteLLM / OpenAI parameters for NVIDIA NIM compatibility:
    - Base URL: `https://integrate.api.nvidia.com/v1`
    - Pass `api_key=os.getenv("NVIDIA_API_KEY")`
    - Set `drop_params=True` so provider-unsupported flags are ignored safely.
  - [x] Retain and enhance `strip_thought_parts` to keep request payloads clean across multi-turn tool conversations.
  - [x] Retain daily token budgeting, rate-limit retries (429 handling with jittered backoff), and circuit-breaker integration.

### 1.3 Automated Unit Tests for Step 1
- [x] Create `Copilot/tests/test_nemotron_llm.py`:
  - [x] Test model building for each tier (`nano`, `super`, `ultra`).
  - [x] Test fallback when `NVIDIA_API_KEY` is not present (graceful warning/mock for unit test suites).
  - [x] Test that temperature, max tokens, and thought-stripping work cleanly with Nemotron responses.

---

## Step 2: Laya Decision Engine Integration

### 2.1 Laya Engine Service Module
- [x] **Create `Copilot/agent/laya_classifier.py`**:
  - [x] Define `LayaClassifier` class with lazy singleton initialization of `laya.Router()`.
  - [x] Handle package import and runtime dependency checks (`pip install laya`).

### 2.2 Decision Task 1: Incident Context Classification
- [x] Implement `classify_incident_context(current_context: str, current_evidence: list, past_context: str, past_evidence: list) -> str`:
  - [x] Formulate structured input state comparing the historical vs. current release, config revision, error signatures, and symptoms.
  - [x] Run Laya single forward-pass choice query:
    ```python
    questions = {
        "context_similarity": {
            "type": "choice",
            "options": ["same_context", "different_context"],
        }
    }
    ```
  - [x] Route output:
    - `"same_context"` $\rightarrow$ directs RCA Agent to **Nemotron Nano**.
    - `"different_context"` $\rightarrow$ directs RCA Agent to **Nemotron Super**.

### 2.3 Decision Task 2: User Chat Intent Routing
- [x] Implement `classify_chat_intent(user_message: str) -> str`:
  - [x] Formulate intent classification question:
    ```python
    questions = {
        "intent": {
            "type": "choice",
            "options": ["rca", "query"],
        }
    }
    ```
  - [x] Route output:
    - `"rca"` $\rightarrow$ directs user message to **Reactive RCA Agent**.
    - `"query"` $\rightarrow$ directs user message to **Reactive Query Agent**.

### 2.4 Fallback & Resilience
- [x] Implement deterministic heuristic fallback in `LayaClassifier`:
  - [x] Context fallback: Compares release version, config rev, and exception signature strings directly.
  - [x] Intent fallback: Token and keyword inspection (e.g. `get logs`, `show traces`, `health` $\rightarrow$ `query`; `outage`, `broken`, `root cause`, `diagnose` $\rightarrow$ `rca`).
  - [x] Ensures zero crashes even if model download is interrupted or offline in CI environments.

### 2.5 Automated Unit Tests for Step 2
- [x] Create `Copilot/tests/test_laya_classifier.py`:
  - [x] Test context classification with identical runtime contexts $\rightarrow$ returns `"same_context"`.
  - [x] Test context classification with differing releases/exceptions $\rightarrow$ returns `"different_context"`.
  - [x] Test chat intent classification on telemetry queries (`"fetch logs for the last 10 minutes"`) $\rightarrow$ returns `"query"`.
  - [x] Test chat intent classification on RCA queries (`"why is todo-app throwing 500 errors?"`) $\rightarrow$ returns `"rca"`.
  - [x] Test fallback execution when the underlying engine is mocked or unavailable.

---

## Step 3: Three-Agent Architecture

### 3.1 Proactive Agent (Detection & Alerting)
- [x] **Refactor `Copilot/agent/watcher.py` & `Copilot/agent/cycle.py`**:
  - [x] Ensure background watcher loop operates strictly as the **Proactive Agent**:
    - Periodically samples live service telemetry (`error_rate`, `latency`, `slow_requests`, `health`).
    - Evaluates thresholds and deduplicates breaches into incidents via `process_cycle`.
  - [x] Proactive notification dispatch:
    - Posts immediate alert to Discord webhook on incident opening or regression.
    - Submits incident to the RCA execution queue without blocking the detection loop.

### 3.2 Reactive RCA Agent (Diagnostic Reasoning)
- [x] **Create/Refactor `Copilot/agent/rca_agent.py`**:
  - [x] Implement diagnostic pipeline:
    1. Query Hindsight memory using `cycle.recall_for(incident)`.
    2. Branch model selection:
       - **Branch A (Completely new incident)**: If no matching past incident is found in Hindsight $\rightarrow$ dispatch **Nemotron Ultra** for complete from-scratch RCA.
       - **Branch B (Incident seen before)**: Pass recalled incident + current incident to `LayaClassifier.classify_incident_context`:
         - If `same_context` $\rightarrow$ dispatch **Nemotron Nano** (adjust prior resolution, low-overhead).
         - If `different_context` $\rightarrow$ dispatch **Nemotron Super** (reference prior incident as hypothesis, mid-level reasoning to locate context shifts).
  - [x] Diagnostic tools attached to RCA Agent:
    - `get_error_rate`, `get_latency`, `get_recent_logs`, `get_slow_requests`, `get_service_health`.
  - [x] Output formatting:
    - Symptoms, Evidence, Root Cause, Proposed Action (from `ALLOWED_ACTIONS`).
    - Explicit user follow-up prompt: *"Proposed action: `<action>`. Did this resolve the issue? Please provide feedback."*

### 3.3 Reactive Query Agent (Telemetry & Documentation Lookups)
- [x] **Create `Copilot/agent/query_agent.py`**:
  - [x] Specialized system instruction for answering operator queries without diagnosing incidents or altering incident state machines.
  - [x] Dedicated toolset attached:
    - `get_logs`, `get_logs_for_service`
    - `get_traces`, `get_traces_for_service`
    - `get_service_health`, `get_error_rate`, `get_latency`
    - `search_docs` (RAG tool)
    - `query_prometheus`, `query_loki`, `query_tempo` (optional external tools)
  - [x] Guardrails: Read-only, quotes literal data, reports missing data neutrally.

### 3.4 Chat Orchestration & Routing
- [x] **Update `Copilot/api/router.py` (`POST /ask/{id}`)**:
  - [x] Receive user message.
  - [x] Execute `LayaClassifier.classify_chat_intent(message)`.
  - [x] If `"rca"` $\rightarrow$ delegate conversation turn to Reactive RCA Agent.
  - [x] If `"query"` $\rightarrow$ delegate conversation turn to Reactive Query Agent.
  - [x] Record metadata (which agent and model handled the turn) in message history.

### 3.5 Automated Unit Tests for Step 3
- [x] Create `Copilot/tests/test_agents.py`:
  - [x] Test Proactive Agent breach detection and alert queueing.
  - [x] Test Reactive RCA Agent model routing (Ultra for new, Nano for same, Super for different).
  - [x] Test Reactive Query Agent execution on telemetry lookups.
  - [x] Test `/ask/{id}` dual routing via Laya.

---

## Step 4: Observability Tools & Standalone Docs Indexer

### 4.1 Ingested Event Store Tools
- [x] **Enhance `Copilot/agent/tools.py`**:
  - [x] Implement `get_logs(minutes: float = 10, limit: int = 50) -> dict`:
    - Retrieves log lines across all services in lookback window, ordered newest first.
  - [x] Implement `get_logs_for_service(service: str, minutes: float = 10, keyword: Optional[str] = None, limit: int = 50) -> dict`:
    - Filters logs by target service and optional keyword.
  - [x] Implement `get_traces(minutes: float = 10, min_duration_ms: float = 0, limit: int = 20) -> dict`:
    - Extracts request spans (trace ID, route, duration, status, error, timestamp).
  - [x] Implement `get_traces_for_service(service: str, minutes: float = 10, min_duration_ms: float = 0, limit: int = 20) -> dict`:
    - Filters trace spans by service and latency threshold.

### 4.2 External Observability Tools (Prometheus, Loki, Tempo, Flagd)
- [x] **Create `Copilot/agent/external_obs_tools.py`**:
  - [x] Implement parameterized `query_service_metrics` with fixed PromQL templates (errors, request rate, p95/p50 latency) and strict parameter validation preventing injection.
  - [x] Implement parameterized `query_service_logs` with fixed LogQL templates and log level restrictions.
  - [x] Implement parameterized `query_service_traces` and `get_trace_by_id` with strict hex trace ID validation.
  - [x] Implement `get_feature_flags` read-only querying of Flagd API.

### 4.3 Standalone Documentation Indexing Script
- [x] **Create `Copilot/scripts/index_docs.py`**:
  - [x] CLI argument parser supporting `--docs-path`, `--corpus-path`, and `--index-path`.
  - [x] Ingest markdown files and JSONL corpus chunks with headings and service metadata.
  - [x] Compute token frequencies, IDF, and BM25 statistics.
  - [x] Output serialized JSON index to `data/docs_index.json`.

### 4.4 Documentation RAG Tool
- [x] **Add `search_docs` to `Copilot/agent/tools.py`**:
  - [x] Reads pre-built index from `data/docs_index.json`.
  - [x] Executes BM25 search with optional service filter.
  - [x] Returns ranked excerpts with document titles and headings.

### 4.5 Automated Unit Tests for Step 4
- [x] Create `Copilot/tests/test_expanded_tools.py`:
  - [x] Test injection rejection on metrics, logs, and trace queries.
  - [x] Test `get_logs` and `get_traces` with SQLite event store fixtures.
  - [x] Test `query_service_metrics`, `query_service_logs`, `query_service_traces`, and `get_feature_flags` with mocked responses.
  - [x] Test `search_docs` RAG tool.
- [x] Create `Copilot/tests/test_docs_indexer.py`:
  - [x] Test tokenization and stop word filtering.
  - [x] Test markdown file chunking.
  - [x] Test index building and BM25 search ranking.

---

## Step 5: Incident Dashboard Backend API

### 5.1 Dashboard API Endpoints
- [x] **Create `Copilot/api/dashboard.py`** (and register in `Copilot/main.py`):
  - [x] **`GET /api/incidents`**:
    - Query parameters: `status` (optional), `service` (optional), `limit` (default 50).
    - Returns list of incidents with summary fields: ID, service, status, severity, timestamps (`opened_at`, `resolved_at`, `last_seen_at`), fingerprints, evidence summary, root cause text, proposed action, and action history.
  - [x] **`GET /api/incidents/{incident_id}`**:
    - Returns detailed incident dossier: full evidence, audit trail of events/transitions, recalled past incidents, model diagnoses, and applied action history.
  - [x] **`GET /api/memory/past-incidents`**:
    - Interfaces with Hindsight memory store (`HINDSIGHT_URL` / `HINDSIGHT_BANK`).
    - Returns stored episodic memories, incident summaries, verified fixes (`fix:*`), and failed actions (`failed:*`).
    - Fails open with informative fallback if Hindsight is unreachable.
  - [x] **`GET /api/telemetry/overview`**:
    - Returns live dashboard health snapshot: current traffic volume, 5xx rate, p95 latency, active incidents count, and watcher status.

### 5.2 Schema Definitions
- [x] **Update `Copilot/api/schema.py`**:
  - [x] Define Pydantic models:
    - `IncidentSummary`
    - `IncidentDetail`
    - `PastIncidentsMemoryResponse`
    - `TelemetryOverviewResponse`

### 5.3 Automated Unit Tests for Step 5
- [x] Create `Copilot/tests/test_dashboard_api.py`:
  - [x] Test `GET /api/incidents` returns active and resolved incidents.
  - [x] Test `GET /api/incidents/{id}` returns complete incident details and handles 404.
  - [x] Test `GET /api/memory/past-incidents` returns structured memory objects.
  - [x] Test `GET /api/telemetry/overview` returns live metrics.

---

## Step 6: Interactive Fix Verification & Feedback Loop in Hindsight

### 6.1 Feedback API Endpoint
- [x] **Implement `POST /api/incidents/{incident_id}/feedback` in `Copilot/api/dashboard.py`**:
  - [x] Accept `IncidentFeedbackRequest`:
    ```json
    {
      "action": "restart_worker",
      "worked": true,
      "notes": "Pool exhaustion resolved, latency normalized."
    }
    ```
  - [x] Validate that `action` is a recognized action in `ALLOWED_ACTIONS`.

### 6.2 Success Path (`worked == True`)
- [x] Record action outcome in incident state:
  - Add `{ "action": action, "worked": True, "notes": notes, "ts": now }` to `incidents.actions`.
  - Transition incident state to `RESOLVED` via `db.incidents.transition`.
- [x] Retain in Hindsight:
  - Call `memory.retain(...)` with:
    - Tag: `fix:<action>`
    - Tag: `outcome:resolved`
    - Narrative including the diagnosis, verified fix, and operator notes.
  - Update incident `memory_status = "retained"`.
- [x] Return confirmation response with updated incident status.

### 6.3 Failure Path (`worked == False`)
- [x] Record failure in incident state:
  - Add `{ "action": action, "worked": False, "notes": notes, "ts": now }` to `incidents.actions`.
  - Keep incident in `REMEDIATING` (or `OPEN`).
- [x] Retain negative procedural memory in Hindsight:
  - Call `memory.retain(...)` with tag: `failed:<action>`.
- [x] Trigger Reactive RCA Agent for Alternative Recommendation:
  - Filter out already failed actions from `ALLOWED_ACTIONS`.
  - Prompt Reactive RCA Agent:
    > *"Action `<action>` was attempted and did NOT resolve the issue. Based on current telemetry and known failed actions, recommend the next alternative action."*
  - Record the new proposed action on the incident.
  - Return response containing the new recommendation.

### 6.4 Automated Unit Tests for Step 6
- [x] Create `Copilot/tests/test_fix_feedback.py`:
  - [x] Test successful fix submission: verifies incident transitions to `RESOLVED`, tags memory with `fix:<action>`, and writes narrative.
  - [x] Test failed fix submission: verifies incident records failed action, tags memory with `failed:<action>`, and requests new recommendation.
  - [x] Test conflict handling and unknown incident handling (404).

---

## Step 7: End-to-End Verification & Quality Assurance

- [x] **Syntax & Type Verification**:
  - [x] Run full lint and syntax checks across all modified and new files (`flake8` / `ruff` / python compilation).
  - [x] Ensure 100% absence of syntax errors.
- [x] **Complete Test Suite Run**:
  - [x] Execute `pytest -v Copilot/tests`.
  - [x] Ensure all 269 existing tests pass without regressions + all new tests pass (314 total tests passing).
- [x] **Documentation Update**:
  - [x] Update `readme.md` to document the new Nemotron tier configuration, Laya classifier, 3-agent roles, indexer script usage, and dashboard API routes.
