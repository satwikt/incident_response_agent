# Operations Runbook

## 1. Quick Start (Single Docker Compose)

The entire Astronomy Shop Lite mesh and observability stack starts with a single command from the root of the repository:

```bash
docker compose up -d
```

This brings up:
1. **Astronomy Shop Microservices:**
   - `frontend` (`:8080`): Storefront UI and API Gateway
   - `catalog` (`:5001`): Product catalog and cart service
   - `checkout` (`:5002`): Checkout orchestration and runtime fault engine
   - `astronomy-db` (`:5433` on host, `:5432` internal): PostgreSQL database
2. **Observability Stack:**
   - Prometheus (`:9090`): Metrics scraper & PromQL engine
   - Loki (`:3100`): Structured JSON logs
   - Tempo (`:3200` & `:4318`): Distributed OTLP tracing
   - Grafana (`:3001`): Dashboards & LGTM datasources
3. **Telemetry Adapter:** Continuously polling metrics, logs, and traces and forwarding events to Copilot
4. **Hindsight Memory Store:** Port `9999` (UI) / `8888` (API)
5. **AI SRE Incident Copilot:** Port `8001` (Chat UI & Incident Operations Dashboard)

---

## 2. Ports and UI URLs

| Service | Host URL | Description |
| :--- | :--- | :--- |
| **Astronomy Shop Store** | `http://localhost:8080/` | Live e-commerce shopping web application. |
| **Runtime Faults Control Deck** | `http://localhost:8080/faults` | Interactive fault injection console to trigger real-time anomalies. |
| **Copilot Incident Dashboard** | `http://localhost:8001/dashboard` | Live incident queue, timeline, memory recall, and fix verification. |
| **Copilot Chat Assistant** | `http://localhost:8001/` | Multi-turn conversational SRE copilot. |
| **Grafana** | `http://localhost:3001/` | Telemetry dashboards and correlation views (admin / admin). |
| **Prometheus** | `http://localhost:9090/` | Raw metrics PromQL evaluation console. |
| **Tempo** | `http://localhost:3200/` | Distributed tracing search and waterfall spans. |
| **Hindsight Memory UI** | `http://localhost:9999/` | Episodic memory bank and recall inspector. |

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
