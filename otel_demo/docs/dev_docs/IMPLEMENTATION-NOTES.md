# Implementation Notes

Companion to `docs/HANDOFF.md`. This file explains **how the integration works, which invariants must not be broken, and what is still unverified**. Read it before changing code.

## 1. System map

```text
Astronomy Shop services (vendor checkout, unmodified)
  |  OTLP
  v
OpenTelemetry Collector
  |-- traces  -> Jaeger (upstream), LGTM Tempo
  |-- metrics -> Prometheus (upstream), LGTM Prometheus
  |-- logs    -> OpenSearch (upstream), LGTM Loki
  v
Prometheus / Loki / Tempo / Jaeger / Grafana
  ^
  | read-only queries
integration/astronomy-shop/adapter  ->  POST /ingest/events  ->  Copilot
  ^
  | operator tooling
tools/ (corpus, scenarios, preflight, reports)
```

Two distinct paths:

1. **Detection / memory path (push):** adapter reads backends and posts normalized events. This must keep working.
2. **Investigation path (pull):** future Copilot native tools may query backends on demand. The specification is reference-only and outside this test-module deliverable.

Do not collapse these. Tools are not a replacement for ingest, and the adapter is not the model's tool API.

## 2. Integration-owned vs vendor-owned

- `vendor/opentelemetry-demo/` is a vendored snapshot of upstream `85c2cb253ae3519ef2b3a02984c32614106de5ea`, committed into this repository. Never edit it as source.
- Runtime flag changes via flagd-ui rewrite `src/flagd/demo.flagd.json`. That is expected and can make the checkout dirty because the UI re-encodes JSON. Restore it only after confirming flags are `off`.
- All Copilot-owned code/config lives under `integration/astronomy-shop/`; design records live under `docs/`.

## 3. Module responsibilities

### Adapter

| Module | Responsibility | Invariants |
|---|---|---|
| `events.py` | `Evidence` model, `to_event`, deterministic IDs | service/message required; level in DEBUG/INFO/WARNING/ERROR/CRITICAL; id matches `[A-Za-z0-9._:-]{1,64}` |
| `client.py` | build event payloads, POST with `X-Api-Key` | no retry here; callers own retry |
| `_http.py` | bounded JSON GET | timeout default 5 s; no retries |
| `_common.py` | level normalization, service resolution, stable IDs, attribute lookup | service must resolve or raise |
| `prometheus.py` | PromQL query/range, sample parsing | sample evidence needs a service label or explicit override |
| `loki.py` | LogQL range, stream parsing | line truncated to 2000 chars; structured level extracted |
| `tempo.py` | trace search / fetch | trace ID preserved |
| `jaeger.py` | trace search, root-span selection | API prefix configurable (`/api` default) |
| `poller.py` | batching, per-service grouping and keys, retry, accounting | max 500 events, max 1 MiB body, retries only 429/5xx/transport, per-source isolation |
| `sources.py` | trailing windows | windows computed at fetch time; injectable `now` for tests |
| `presets.py` | dashboard-derived queries | every preset carries an explicit service |
| `__main__.py` | CLI | no invented default queries; `--preset`/`--list-presets`/`--ingest-keys` |

Key adapter behavior:

- `build_events()` is called once per source, then `chunk_events()` splits by count **and** serialized size. Re-converting per batch would change IDs/timestamps and break dedupe.
- Events are grouped by service and each service is chunked separately, so one request never names two services.
- A source exception becomes `source_errors[name]`; other sources still run.
- A failed batch is dropped and counted; there is no infinite retry.
- A service with no ingest key is dropped and reported, not sent with a key bound elsewhere.
- The ingest receipt (`accepted`, `duplicates`) is parsed when present; otherwise all events are assumed accepted.

### Tooling

| Tool | Responsibility |
|---|---|
| `build_corpus.py` | deterministic JSONL corpus; `--check` staleness guard; term-overlap `search` for validation |
| `record_scenario.py` | writes a manual run record; validates UTC and ordering |
| `flagd_ui.py` | read/write flag state through the vendored API |
| `run_scenario.py` | snapshot → toggle → hold → restore → record; dry-run |
| `scenario_report.py` | latest-run-per-scenario coverage report (Markdown/JSON) |
| `preflight.py` | local/Docker/Compose/probe readiness checks; read-only |

Scenario runner invariants:

- Snapshot file is written **before** any mutation.
- Restore runs in `finally`; `KeyboardInterrupt` is included.
- Only `defaultVariant` changes. Targeted flags are not handled.
- The write API replaces the entire document and expects `$schema`; the runner injects the standard schema URL.
- Concurrent writers can clobber each other. Do not run two runners at once.

### Grafana

- `provisioning/datasources/lgtm.yaml` adds Tempo (`lgtm-traces`) and Loki (`lgtm-logs`), plus the trace→log pivot.
- `provisioning/alerting/astronomy-shop-alerts.yaml` has seven rules against `webstore-metrics`.
- `dashboards/astronomy-shop-investigation.json` has eight panels; it is mounted into the upstream `demo` provider.
- Metric names and labels come from the vendored dashboards: `http_server_request_duration_seconds_{count,bucket}`, `traces_span_metrics_{calls_total,duration_milliseconds_bucket}`, `postgresql_deadlocks_total`, `service_name`, `http_response_status_code`, `status_code="STATUS_CODE_ERROR"`.
- `kafka_consumer_group_lag` is the name the pinned upstream produces: the contrib `kafkametrics` receiver emits `kafka.consumer_group.lag`, and Prometheus's OTLP translator joins the tokens with `_`. Its *threshold* is still uncalibrated.

## 4. Ingest contract

```http
POST /ingest/events
X-Api-Key: <key>
Content-Type: application/json
```

Body: JSON array, max 500 events / 1 MiB. Event fields: `id`, `ts`, `service`, `level`, `route`, `status`, `duration_ms`, `message`, `exception`. Duplicate IDs are ignored by the ingest store. The adapter embeds `source=`, `trace_id=`, `span_id=` in `message` until a separately authorized Copilot implementation changes that convention.

The endpoint binds each key to one service and rejects a batch naming another (`403 service mismatch`), so the adapter sends one request per service and uses that service's key. The batch is validated atomically, so the adapter clamps `status` to 100–599 and `duration_ms` to 0–3 600 000 rather than letting one out-of-range value reject the whole batch. `route` is truncated to 200 characters and `exception` to 8192 for the same reason: the endpoint *rejects* an over-long route rather than shortening it.

The endpoint watches one service (`APP_SERVICE_NAME`). `--watch-service`/`COPILOT_WATCH_SERVICE` names it: evidence for any other service is dropped and reported instead of being sent and refused.

`tests/copilot_contract.py` is a local transcription of these rules, and `tests/test_copilot_contract.py` runs the adapter's real HTTP client against it.

## 5. Test strategy

- stdlib `unittest` only; pytest is unavailable in this worker.
- Run from `integration/astronomy-shop`: `python3 -m unittest discover -s tests` (or `make test`).
- Mocked backend payload tests cover parsing and conversion.
- One real localhost `http.server` test covers the ingest POST and API key.
- `tests/test_copilot_contract.py` runs the adapter's real client against a local server enforcing the Copilot's ingest rules (transcribed from the Copilot source), covering auth, rate limit, body size, batch size, schema, service binding, and dedupe.
- Static config tests guard overlay mounts, exporter names, datasource UIDs, alert/metric contracts, dashboard structure, and native-tool schemas.
- Corpus tests enforce metadata, coverage, determinism, retrieval, and `--check`.

What the tests do **not** prove: real backend response shapes, Grafana rendering, Compose resolution, or live Copilot ingest.

## 6. Environment constraints

- No Docker, pytest, PyYAML, or pip in the current worker. Do not add dependencies that need them.
- Do not rely on the root workspace being a Git repository; it is not.
- Compose overlays use nested file mounts inside a directory mount; keep target paths exact.
- The overlay requires `compose.observability.yaml` because it references `otlp_grpc/jaeger` and `otlp_http/prometheus`.
- LGTM bundles Loki (3100) and Tempo (3200); the overlay publishes all three on loopback (`127.0.0.1:3001`, `:3100`, `:3200`).
- The upstream stack runs Jaeger v2 with `base_path: /jaeger/ui`; Jaeger registers its REST API under that prefix, so the query API is `/jaeger/ui/api/...`. The adapter's default `--jaeger-url` carries it.

## 7. Unverified assumptions (resolve live, in order)

1. Compose resolution with all five layers.
2. LGTM Grafana health and bundled Tempo/Loki readiness.
3. Prometheus metric availability and label spelling at runtime.
4. Ingest acceptance and dedupe against a real Copilot.
5. Grafana datasource/alert/dashboard loading.
6. Alert thresholds against real baseline traffic.

Resolved from the pinned upstream source (not observed at runtime):

- Loki label scheme — `service_name`; Loki's default OTLP index labels include `service.name`, normalized to an underscore.
- Kafka consumer-lag metric name — `kafka_consumer_group_lag`.

## 8. Failure modes and first checks

| Symptom | First check |
|---|---|
| Collector exits | `--config` file names/arrays; overlay extras mount at `/etc/otelcol-config-extras.yml` |
| No LGTM data | collector `otlphttp/lgtm` exporter; `lgtm` service health |
| Adapter `source_errors` | backend URL/port, then response shape, then label names |
| Ingest 401/403 | `X-Api-Key`, key-to-service binding |
| Ingest 413/422 | batch bytes/count, required fields, id charset |
| Duplicates every poll | deterministic IDs were reverted or timestamp changes |
| Flags stuck on | restore snapshot manually; check `scenarios/runs/*.snapshot.json` |
| Grafana panel empty | datasource UID, metric name, service label |
| Scenario runner 2 | flagd-ui unreachable; check base URL and `/feature` prefix |

## 9. What not to do

- Do not edit `vendor/opentelemetry-demo` as source.
- Do not replace deterministic IDs with random UUIDs per poll.
- Do not raise batch limits above 500 events or 1 MiB.
- Do not add retries inside backend clients; the poller owns them.
- Do not add write tools to the Copilot tool surface.
- Do not claim live validation from unit tests.
- Do not weaken or delete tests to make a check pass.

## 10. Command cheat sheet

```bash
cd integration/astronomy-shop
make help
make test
make corpus-check
make preflight
make report
make dry-run
make adapter-help

python3 tools/preflight.py --execute --probe   # Docker + running stack
python3 -m adapter --list-presets
```
