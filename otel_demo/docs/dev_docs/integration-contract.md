# Integration Contract

## Existing Copilot boundary

```http
POST /ingest/events
X-Api-Key: <service-key>
Content-Type: application/json
```

Body is a JSON array of events. The current Copilot validates and stores fields including:

```json
{
  "id": "unique-event-id",
  "ts": "RFC3339 or epoch",
  "service": "checkout",
  "level": "ERROR",
  "route": "POST /checkout",
  "status": 500,
  "duration_ms": 123.4,
  "message": "safe structured message",
  "exception": "PaymentUnavailable"
}
```

## Adapter responsibilities

- Read from approved observability APIs only.
- Normalize service names and operation names.
- Preserve trace/span identifiers in structured message/context fields until native Copilot tools support them.
- Convert timestamps to UTC.
- Avoid raw secret/PII payloads.
- Batch events without blocking application traffic.
- Retry with bounded backoff and expose dropped-event counts.
- Mark source/backend and query time in metadata where possible.

## Planned adapter stages

1. **Smoke adapter:** poll Prometheus/Loki/Tempo APIs and emit request-level summaries.
2. **Trace-aware adapter:** emit selected spans and trace IDs for breached services.
3. **Future native tool integration:** a separate Copilot project may replace broad polling with read-only agent tools for targeted queries. This is outside the test-module deliverable.

## Future native tools (outside this test-module deliverable)

```text
query_prometheus(query, start, end)
query_loki(query, start, end, limit)
query_tempo(query, start, end, service, duration)
query_jaeger(service, start, end)
get_flag_state(flag)
search_application_docs(query)
```

All tools return JSON, never raise to the model, and identify their source and query window.
