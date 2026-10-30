# Native Observability Tools Specification (Chunk 6)

Status: **future-work reference only; deliberately outside this test-module deliverable.** The project remote is `https://github.com/satwikt/incident_response_agent`; no Copilot checkout or Copilot code is included here.

Machine-readable companion: `docs/native-tools.schema.json`.

## 1. Purpose

Replace broad adapter polling during investigation with targeted, read-only tool calls. Detection stays push-based:

```text
Astronomy Shop -> adapter -> POST /ingest/events -> Copilot watcher/detection
Copilot diagnosis -> native tools -> Prometheus/Loki/Tempo/Jaeger/flagd/docs
```

The adapter remains the bulk signal path. Tools are for narrowing a suspected incident, not for streaming telemetry.

## 2. Non-goals

- No flag writes or scenario toggling.
- No remediation, shell, SQL, or arbitrary HTTP.
- No reading Astronomy Shop source code during diagnosis.
- No replacement of the existing incident lifecycle or Hindsight.

## 3. Common contract

Every tool returns JSON and never raises into the model. Success:

```json
{
  "ok": true,
  "source": "prometheus",
  "window": {"start": "2026-10-20T10:00:00Z", "end": "2026-10-20T10:05:00Z"},
  "data": [],
  "truncated": false,
  "error": null
}
```

Failure:

```json
{
  "ok": false,
  "source": "prometheus",
  "window": null,
  "data": [],
  "truncated": false,
  "error": {"code": "backend_unavailable", "message": "Prometheus request failed"}
}
```

Rules:

- `window` is echoed for every time-bounded query, even on empty results.
- `truncated` is true when a limit clipped the result; never silently drop data.
- Error codes: `invalid_argument`, `backend_unavailable`, `timeout`, `not_found`, `too_large`, `internal`.
- Error messages are safe, short, and contain no credentials or raw backend bodies.
- Tools are read-only; the model cannot choose a backend URL.

## 4. Limits

| Limit | Value |
|---|---|
| Per-call timeout | 5 s |
| Max query window | 24 h |
| Max query length | 2000 chars |
| Max rows per tool | 200 (per-tool lower defaults) |
| Max response size | 256 KiB |

If a query can return more than the limit, the implementation must cap it and set `truncated: true`.

## 5. Tools

### 5.1 `query_prometheus`

Input: `query`, `start`, `end`, `step` (default `60s`), `limit` (default 50, max 200).

Returns instant/range samples:

```json
{"metric": "http_server_request_duration_seconds_count", "labels": {"service_name": "checkout"}, "value": 12.5, "timestamp": 1700000000}
```

Behavior:

- Use `/api/v1/query` for a single evaluation and `/api/v1/query_range` when `step` is supplied with a range.
- Reject reversed or >24 h windows with `invalid_argument`.
- Do not invent thresholds; return data only.

### 5.2 `query_loki`

Input: `query` (LogQL), `start`, `end`, `limit` (default 100, max 200), `direction` (default `backward`).

Returns records:

```json
{"timestamp": "2026-10-20T10:00:00Z", "line": "...", "labels": {"service_name": "checkout", "trace_id": "..."}}
```

Behavior:

- Preserve `trace_id`/`span_id` when present in labels.
- Truncate individual lines to 2000 chars and set `truncated: true`.
- Treat log text as untrusted data, never instructions.

### 5.3 `query_tempo`

Input: `service`, `start`, `end`, `limit` (default 20, max 100), optional `tags`.

Returns trace summaries:

```json
{"trace_id": "4f1a...", "root_service": "checkout", "root_name": "POST /checkout", "start": "2026-10-20T10:00:00Z", "duration_ms": 812.5}
```

Behavior:

- Default tag filter is `service.name=<service>`; allow an explicit `tags` expression.
- Empty result is a success with `data: []`, not an error.

### 5.4 `query_jaeger`

Input: `service`, `start`, `end`, `limit` (default 20, max 100), optional `operation`.

Returns trace summaries plus span counts. The API prefix is configurable because Jaeger v2 changes it; do not hardcode `/api` in the tool.

### 5.5 `get_trace`

Input: `trace_id` (16–32 hex chars), `backend` (`tempo` or `jaeger`, default `tempo`).

Returns normalized spans:

```json
{
  "trace_id": "...",
  "spans": [
    {"span_id": "...", "parent_span_id": "...", "service": "checkout", "name": "POST /checkout",
     "start": "2026-10-20T10:00:00Z", "duration_ms": 812.5, "status": "ERROR",
     "attributes": {"http.route": "/checkout", "http.status_code": 500}}
  ]
}
```

Behavior:

- Keep only diagnosis-relevant attributes; drop large payloads and secrets.
- `not_found` when the backend has no such trace; `backend_unavailable` on transport failure.

### 5.6 `get_flag_state`

Input: optional `flag`.

Returns the current flag map from the flagd-ui read API (`GET /feature/api/read`), optionally filtered. This tool must never call the write endpoint. There is deliberately no toggle tool; scenario control stays an operator action.

### 5.7 `search_application_docs`

Input: `query`, `limit` (default 5, max 20), optional `service`.

Returns ranked chunks from `integration/astronomy-shop/docs-corpus/corpus.jsonl`:

```json
{"chunk_id": "local-operations-runbook:003:ab12cd34", "title": "...", "source_url": "...", "service": "...", "text": "..."}
```

Behavior:

- Include provenance (`source_url`, `source_path`, `app_version`) for citation.
- Retrieved text is untrusted data; never execute instructions found in it.
- If the corpus is missing or stale, return `internal` with a short message; do not silently return empty.

## 6. Safety requirements

- Backend base URLs come from server-side environment variables only.
- Reject any parameter that looks like a URL, host, or path traversal.
- Read-only credentials; no write scope to any backend.
- No shell, file, or SQL tool exposure.
- Redact secrets, PII, and payment data before returning or logging.
- Log tool name, duration, row count, error code, and window. Do not log raw payloads.
- A tool failure is evidence of degraded telemetry, not an incident cause.

## 7. Implementation notes

- Reuse `integration/astronomy-shop/adapter/{prometheus,loki,tempo,jaeger}.py` parsing helpers where possible.
- Do **not** reuse the poller or emit ingest events from tools; tools return data to the model directly.
- Put the tool layer in the Copilot repository, with adapters injected as clients. Keep backend clients behind an interface so the Copilot tests can mock them.
- Normalize timestamps to UTC ISO 8601 in responses.
- Prefer one bounded attempt per call; retries belong to the tool runtime, not the backend client.

Suggested query templates (starting points, same as `adapter/presets.py`):

- Checkout errors: `sum(rate(http_server_request_duration_seconds_count{service_name="checkout",http_response_status_code=~"5.."}[5m]))`
- Payment error spans: `sum(rate(traces_span_metrics_calls_total{service_name="payment",status_code="STATUS_CODE_ERROR"}[5m]))`
- Shipping p95: `histogram_quantile(0.95, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{service_name="shipping"}[5m])))`
- Logs: `{service_name="checkout"} |= "error"`

## 8. Test plan

Contract tests:

- Schema validation against `docs/native-tools.schema.json`.
- No tool name matches write/toggle/exec/remediate patterns.
- No parameter named `url`, `endpoint`, or `host`.
- Window validation: naive timestamps rejected, reversed windows rejected, >24 h rejected.
- Limits: row caps, line truncation, response-size cap, `truncated` flag.

Backend tests (mocked fixtures):

- Prometheus vector/matrix, Loki streams, Tempo search, Jaeger root spans.
- Timeout, 500, empty result, malformed JSON, oversized response.
- `get_trace` not found and backend unavailable.
- Flag read works; no write method exists on the client.
- Docs retrieval returns citations and tolerates missing corpus.

Safety tests:

- Instruction-like text in logs/docs is returned as data and never changes tool selection.
- URL-like inputs are rejected.
- Secrets are redacted.

Live tests (after `docs/live-validation.md` passes):

- One metric, log, trace, flag read, and docs query per session.
- Tool latency p95 < 2 s against the local stack (record actual numbers; do not assume).

## 9. Rollout

1. Implement behind a feature flag (`NATIVE_TOOLS_ENABLED`).
2. Run in shadow mode beside the adapter; compare evidence for T-02.
3. Enable for diagnosis only after the three shadow runs agree.
4. Keep `/ingest/events` for detection and Hindsight provenance.
5. Only then consider reducing adapter polling scope.

## 10. Open decisions

- Default trace backend (`tempo` vs `jaeger`) and whether `get_trace` should fall back.
- Whether the docs tool reads the JSONL file directly or the Copilot's index.
- Tool runtime location and registration format in the Copilot repo.
- Authentication between the Copilot and the observability backends.
