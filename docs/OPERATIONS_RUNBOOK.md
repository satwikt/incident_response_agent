# Operations Runbook

## 1. Setup & Startup Workflow

### Step 1: Environment Configuration
Copy the template environment file and set your API keys:
```bash
cp .env.example .env
```
Ensure `NVIDIA_API_KEY` and `HINDSIGHT_LLM_API_KEY` are populated in `.env`.

### Step 2: Vectorize Documentation for RAG
Index the Markdown runbooks and architecture docs into ChromaDB and build the BM25 fallback index:
```bash
chmod +x scripts/index_docs.sh
./scripts/index_docs.sh
```
*Or run via Python:*
```bash
python3 Copilot/scripts/index_docs.py --docs-path otel_demo/docs --chroma-path Copilot/data/chroma_db
```

### Step 3: Build Container Images
Build the microservices and copilot containers locally:
```bash
docker compose build
```

### Step 4: Start the Stack
Bring up all services, observability agents, and databases in detached mode:
```bash
docker compose up -d
```

This brings up:
1. **Astronomy Shop Microservices:**
   - `frontend` (`:8080`): Storefront UI, Faults console, and API Gateway
   - `catalog` (`:5001`): Product catalog and cart service
   - `checkout` (`:5002`): Checkout orchestration and runtime fault engine
   - `astronomy-db` (`:5433` on host, `:5432` internal): PostgreSQL database
2. **Observability Stack:**
   - Prometheus (`:9090`): Metrics scraper & PromQL engine
   - Loki (`:3100`): Structured JSON logs
   - Tempo (`:3200` HTTP, `:4318` OTLP traces): Distributed tracing
   - Grafana (`:3001`): Dashboards & LGTM datasources (`admin` / `admin`)
3. **Telemetry Adapter:** Continuously polling metrics, logs, and traces and forwarding events to Copilot
4. **Hindsight Memory Store:** Port `9999` (UI) / `8888` (API)
5. **AI SRE Incident Copilot:** Port `8001` (Chat UI at `/` & Incident Operations Dashboard at `/dashboard`)

---

## 2. Ports and UI URLs

| Service | Container | Host URL | Description |
| :--- | :--- | :--- | :--- |
| **Astronomy Shop Store** | `astronomy-frontend` | `http://localhost:8080/` | Live e-commerce shopping web application. |
| **Runtime Faults Control Deck** | `astronomy-frontend` | `http://localhost:8080/faults` | Interactive fault injection console to trigger real-time anomalies. |
| **Copilot Incident Dashboard** | `incident-copilot` | `http://localhost:8001/dashboard` | Live incident queue, timeline, memory recall, and fix verification. |
| **Copilot Chat Assistant** | `incident-copilot` | `http://localhost:8001/` | Multi-turn conversational SRE copilot. |
| **Grafana** | `grafana` | `http://localhost:3001/` | Telemetry dashboards and correlation views (`admin` / `admin`). |
| **Prometheus** | `prometheus` | `http://localhost:9090/` | Raw metrics PromQL evaluation console. |
| **Tempo** | `astronomy-tempo` | `http://localhost:3200/` | Distributed tracing search and waterfall spans. |
| **Loki** | `astronomy-loki` | `http://localhost:3100/` | Structured JSON log query endpoint. |
| **Hindsight Memory UI** | `incident-hindsight` | `http://localhost:9999/` | Episodic memory bank and recall inspector. |
| **Catalog API** | `astronomy-catalog` | `http://localhost:5001/` | Product catalog and cart service endpoint. |
| **Checkout API** | `astronomy-checkout` | `http://localhost:5002/` | Order processing and fault injection API. |
| **PostgreSQL Database** | `astronomy-db` | `localhost:5433` | PostgreSQL 16 database (internal container port `5432`). |

---

## 3. Injecting Runtime Fault Scenarios

Faults are toggled dynamically from the **Faults Dashboard** at `http://localhost:8080/faults` without requiring service restarts:

### Scenario A: Payment Failure (Checkout 500 Error Spike)
1. Navigate to `http://localhost:8080/faults`.
2. Toggle **Payment Failure** to **ON**.
3. Click **"Trigger Test Order"** or browse the store and check out.
4. **Observed Behavior:**
   - Checkout returns HTTP 500 (`PaymentGatewayException: Transaction declined by upstream clearinghouse`).
   - Copilot Proactive Agent detects the 5xx breach and registers an incident in the dashboard.
   - Reactive RCA Agent evaluates memory, queries Loki/Tempo, and proposes `enable_fallback` or `restart_worker`.
5. Click **"Reset All Faults"** to restore health.

### Scenario B: High Latency Spike
1. In the Faults Dashboard, toggle **High Latency Spike** to **ON**.
2. Click **"Burst 10 Requests"**.
3. Copilot watcher detects p95 latency breach (>1000ms).
4. Query Agent responds with exact latency percentiles and affected routes.
5. In Grafana / Tempo (`http://localhost:3200`), view the extended duration span in the trace waterfall.

### Scenario C: Database Connection Pool Exhaustion
1. Toggle **DB Pool Exhaustion** to **ON**.
2. Subsequent checkouts fail with HTTP 503 (`DatabaseConnectionTimeout: Connection pool exhausted`).
3. Copilot identifies pool starvation and proposes `flush_pool`.

---

## 4. Resolving Incidents & Fix Verification

1. Open the Copilot Dashboard at `http://localhost:8001/dashboard`.
2. Click on the open incident in the incident table.
3. Review the root cause diagnosis and proposed action.
4. After applying the remediation, click:
   - **"Fix Worked"**: Marks incident as `RESOLVED` and writes a verified procedure tag `fix:<action>` into Hindsight memory.
   - **"Fix Failed"**: Marks incident as `REMEDIATING` and writes `failed:<action>` into Hindsight memory to prompt an alternate fix.
