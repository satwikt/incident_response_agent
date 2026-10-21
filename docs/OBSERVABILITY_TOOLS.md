# Observability Tools & Security Specification

## 1. Security Architecture: Injection-Proof Tools

To protect the production monitoring infrastructure from model manipulation or prompt injection, **the LLM is strictly prohibited from executing arbitrary PromQL, LogQL, or TraceQL queries**.

Instead, all tools are parameterized and fill **pre-compiled, hardened query templates**:

```
[ LLM Tool Call ]  -->  [ Strict Parameter Validation ]  -->  [ Fixed Template Interpolation ]  -->  [ Backend API ]
 (service, range)       (regex, range bounds, enums)          (escaped, parameterized)          (Prom/Loki/Tempo)
```

---

## 2. Tool Reference

### 2.1 Prometheus Metrics Tool: `query_service_metrics`
- **Signature:** `query_service_metrics(service_name: str, metric_type: str = "errors", minutes: float = 10.0) -> dict`
- **Security Validation:**
  - `service_name` must match `^[a-zA-Z0-9_\-\.]+$` (no quotes, semicolons, brackets, or PromQL operators).
  - `metric_type` must be in `{"errors", "request_rate", "latency_p95", "latency_p50"}`.
  - `minutes` clamped to `[1.0, 1440.0]`.
- **Pre-defined Templates:**
  - `errors`: `sum(rate(http_server_request_duration_seconds_count{service_name="{service}",http_response_status_code=~"5.."}[5m]))`
  - `request_rate`: `sum(rate(http_server_request_duration_seconds_count{service_name="{service}"}[5m]))`
  - `latency_p95`: `histogram_quantile(0.95, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{service_name="{service}"}[5m])))`
  - `latency_p50`: `histogram_quantile(0.50, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{service_name="{service}"}[5m])))`

### 2.2 Loki Logs Tool: `query_service_logs`
- **Signature:** `query_service_logs(service_name: str, level: str = "ERROR", minutes: float = 10.0, limit: int = 50) -> dict`
- **Security Validation:**
  - `level` restricted to `{"ERROR", "WARN", "INFO", "DEBUG"}`.
  - `limit` clamped to `[1, 100]`.
  - Individual log lines truncated to 2000 characters.
- **Template:** `{service_name="{service}"} |= "{level}"`

### 2.3 Tempo Traces Tool: `query_service_traces`
- **Signature:** `query_service_traces(service_name: str, minutes: float = 10.0, limit: int = 20) -> dict`
- **Function:** Searches for recent traces involving the given microservice in Tempo/Jaeger within the lookback window.

### 2.4 Trace Hierarchy Tool: `get_trace_by_id`
- **Signature:** `get_trace_by_id(trace_id: str) -> dict`
- **Security Validation:** Validates `trace_id` strictly matches `^[0-9a-fA-F]{16,32}$`.
- **Function:** Retrieves normalized span hierarchy and status codes from Tempo or Jaeger.

### 2.5 Feature Flags Inspection: `get_feature_flags`
- **Signature:** `get_feature_flags() -> dict`
- **Function:** Read-only GET query to `http://flagd:8080/feature/api/read` to identify active injected faults without exposing write or toggle endpoints.

### 2.6 Documentation RAG Tool: `search_docs`
- **Signature:** `search_docs(query: str, limit: int = 3, service: Optional[str] = None) -> dict`
- **Function:** Performs BM25 token-scored retrieval against the pre-indexed corpus (`Copilot/data/docs_index.json`), returning relevant runbook and architecture chunks with citations.

---

## 3. Standard Response Envelope

Every tool call returns a consistent JSON envelope and never throws an exception into the model:

```json
{
  "ok": true,
  "source": "prometheus",
  "window": {
    "start_min_ago": 10.0,
    "step_seconds": 15
  },
  "data": [ ... ],
  "truncated": false,
  "error": null
}
```

In the event of network failure or timeout:
```json
{
  "ok": false,
  "source": "prometheus",
  "window": null,
  "data": [],
  "truncated": false,
  "error": {
    "code": "backend_unavailable",
    "message": "Prometheus request failed: connection refused"
  }
}
```
