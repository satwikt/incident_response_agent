# Telemetry Adapter

The adapter reads normalized evidence from approved observability APIs and submits Copilot-compatible events to `POST /ingest/events`.

It is intentionally small and read-only:

- no shell execution;
- no application source access;
- no remediation calls;
- no direct database access;
- bounded request timeouts and bounded retries;
- explicit source metadata in emitted messages.

## Implemented

- `events.py` — backend-neutral `Evidence`, schema conversion, deterministic event IDs.
- `client.py` — bounded JSON batch sender with `X-Api-Key`.
- `prometheus.py` — `query` / `query_range` plus evidence conversion.
- `loki.py` — `query_range` plus structured-level extraction.
- `tempo.py` — trace search / fetch plus evidence conversion.
- `jaeger.py` — trace search plus root-span evidence conversion.
- `poller.py` — count- and byte-aware batching (max 500 events / 1 MiB), per-service request grouping, bounded exponential backoff, per-source failure isolation, dropped/duplicate accounting.
- `sources.py` — trailing-window source factories for each backend.
- `__main__.py` — runnable polling loop.

## Run

```bash
cd integration/astronomy-shop
python3 -m adapter --list-presets
COPILOT_API_KEY=... python3 -m adapter \
  --ingest-url http://localhost:8000/ingest/events \
  --preset checkout-error-ratio \
  --preset checkout-error-logs \
  --tempo-service checkout \
  --jaeger-service checkout \
  --once
```

Presets in `presets.py` are dashboard-derived starting points for checkout, payment, shipping, recommendation, and product-catalog signals. They carry an explicit service so aggregate queries remain valid evidence.

Loki and Tempo come from the LGTM overlay, which publishes them on loopback (`127.0.0.1:3100` and `127.0.0.1:3200`); the Loki presets and `--tempo-service` work once that overlay is running.

`--ingest-url`/`--api-key` fall back to `COPILOT_INGEST_URL`/`COPILOT_API_KEY`. Backend URLs default to localhost ports and are overridable with `PROMETHEUS_URL`, `LOKI_URL`, `TEMPO_URL`, and `JAEGER_URL`.

## Per-service ingest keys

The Copilot binds every ingest key to exactly one service and rejects a batch
that names any other service, so the adapter groups events by service and sends
one request per service:

```bash
COPILOT_INGEST_KEYS=checkout:key-for-checkout,payment:key-for-payment \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --once
```

`--ingest-keys`/`COPILOT_INGEST_KEYS` takes the same `service:key,service:key`
form the Copilot's own `INGEST_KEYS` uses. `--api-key` becomes optional when
it is set, and remains the fallback for a service that has no entry. A service
with no key at all is dropped and reported in `source_errors` rather than sent
with a key bound to a different service.

## The watched service

The Copilot watches exactly one service (`APP_SERVICE_NAME`). `--watch-service`/
`COPILOT_WATCH_SERVICE` names it: evidence for any other service is dropped and
counted in `source_errors` instead of being sent and refused, and the adapter
warns at startup about every source that targets a different service.

## Field limits

The Copilot validates a batch atomically, so one bad event loses the whole
batch. The adapter clamps before sending:

- `status` — dropped when outside 100–599;
- `duration_ms` — dropped when outside 0–3 600 000 or not finite;
- `route` — truncated to 200 characters (the Copilot *rejects* a longer route
  rather than truncating it, which would take the batch with it);
- `exception` — truncated to 8192 characters.

An event with no explicit id gets a deterministic one derived from its own
fields, so polling the same evidence twice still dedupes on the Copilot side.

## Evidence shape

Only some backends carry the fields the Copilot's detection needs:

- route — `http.route` on spans, `http_route` on Prometheus metrics;
- status — `http.response.status_code` on spans, `http_response_status_code` on
  Prometheus metrics;
- duration — span duration (Jaeger/Tempo).

Events without a route and status cannot drive the error-rate breach, and
events without a duration cannot drive the latency or slow-request breaches.

The default `--jaeger-url` is `http://localhost:8080/jaeger/ui`. The upstream stack runs Jaeger v2 with `base_path: /jaeger/ui`, which registers the REST API under that prefix, so the query API is reached at `/jaeger/ui/api/traces`. Pointing at a Jaeger deployed at the root instead takes `--jaeger-api-prefix /api`; reaching Jaeger directly takes `--jaeger-url http://localhost:16686 --jaeger-api-prefix /jaeger/ui/api`.

No default queries are invented: enable a backend by supplying its query or service flag.

## Tests

```bash
python3 -m unittest discover -s tests
```

Tests use mocked HTTP for clients and a real localhost HTTP server for the ingest path. `tests/copilot_contract.py` is a local stand-in for the Copilot's ingest endpoint with every rule transcribed from the Copilot at `0ce9844`; `tests/test_copilot_contract.py` runs the adapter's real client against it. Live backend compatibility (response shapes, Loki timestamp units) is still unverified.
