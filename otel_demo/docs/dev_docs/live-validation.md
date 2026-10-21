# Live Validation Checklist

First run on a Docker host. Each step lists the command, the expected result, and what to record. Do not mark anything **verified** until the observed output is recorded in `status.md` and `change-log.md`.

Estimates assume Docker Compose v2, enough RAM for the minimal stack plus LGTM (roughly 6–8 GB free), and access to a running Copilot ingest endpoint.

## 0. Preconditions

Run commands from the workspace root unless a step changes directory.

```bash
docker compose version
git -C vendor/opentelemetry-demo status --short --branch
cd integration/astronomy-shop
python3 -m unittest discover -s tests
python3 tools/build_corpus.py --check
python3 tools/preflight.py --local-only
```

Expected:

- Compose v2 available.
- Vendored snapshot clean (`git status --porcelain -- vendor/opentelemetry-demo` is empty).
- 139 tests pass.
- Corpus is up to date.
- Preflight reports 3 pass, 0 fail, 2 skip.

Record the Docker and Python versions. If the vendor file is dirty from a prior run, restore it **after confirming all flags are `off`**:

```bash
git -C ../../vendor/opentelemetry-demo restore src/flagd/demo.flagd.json
```

## 1. Compose configuration

```bash
cd ../../vendor/opentelemetry-demo
docker compose config \
  -f compose.yaml \
  -f compose.full.yaml \
  -f compose.observability.yaml \
  -f compose.extras.yaml \
  -f ../../integration/astronomy-shop/compose.overlay.yaml \
  > /tmp/compose-resolved.yaml
echo "EXIT=$?"
```

Expected: `EXIT=0` and a resolved file containing `astronomy-lgtm`, the extras mount at `/etc/otelcol-config-extras.yml`, and the Grafana mounts for `lgtm.yaml` and `astronomy-shop-alerts.yaml`.

If it fails, fix only `integration/astronomy-shop/` config paths and record the error.

## 2. Start minimal + observability + overlay

```bash
docker compose -f compose.yaml -f compose.observability.yaml -f compose.extras.yaml \
  -f ../../integration/astronomy-shop/compose.overlay.yaml up -d
docker compose ps
```

Expected: web store, frontend proxy, load generator, Collector, Prometheus, Grafana, Jaeger, flagd, flagd-ui, and `astronomy-lgtm` are running. Verify with:

```bash
python3 ../../integration/astronomy-shop/tools/preflight.py --execute --probe
```

Expected: required files, vendor, corpus, docker, Compose config, and all probes pass.

URLs (through the frontend proxy unless noted):

| Surface | URL |
|---|---|
| Store | `http://localhost:8080` |
| flagd UI | `http://localhost:8080/feature` |
| Jaeger UI | `http://localhost:8080/jaeger/ui` |
| Upstream Grafana | `http://localhost:8080/grafana` |
| LGTM Grafana | `http://127.0.0.1:3001` |
| LGTM Tempo | `http://127.0.0.1:3200` |
| LGTM Loki | `http://127.0.0.1:3100` |
| Prometheus | host port from `docker compose port prometheus 9090` |

Check flag state:

```bash
curl -s http://localhost:8080/feature/api/read | head -c 300
```

Expected: `{"flags":{...}}` with `paymentFailure` present and `defaultVariant` equal to `off`.

## 3. Signal checks

Metrics:

```bash
PROM=$(docker compose port prometheus 9090 | sed 's/.*://')
curl -sG "http://localhost:${PROM}/api/v1/query" \
  --data-urlencode 'query=sum(rate(http_server_request_duration_seconds_count[5m]))'
```

Expected: `"status":"success"` and a non-zero value. If the selector is empty, confirm the load generator is running and wait one scrape interval.

Logs and traces (LGTM backends; verify in LGTM Grafana Explore or against the published loopback ports):

- Loki: `{service_name="checkout"}` returns recent lines.
- Tempo: search `service.name=checkout` returns traces.
- Tempo trace → Loki pivot opens related logs.

Readiness checks for the two backends the upstream stack does not ship:

```bash
curl -s http://127.0.0.1:3200/ready
curl -s http://127.0.0.1:3100/ready
```

Expected: `ready` from both. The overlay publishes them on loopback so the adapter's default `--tempo-url`/`--loki-url` resolve.

Jaeger API. The upstream stack runs Jaeger v2 with `base_path: /jaeger/ui`, which registers the REST API under that prefix, so the proxy path is `/jaeger/ui/api/...`:

```bash
curl -s http://localhost:8080/jaeger/ui/api/services | head -c 300
```

Expected: `{"data":[...]}` containing `checkout`, `payment`, and other services.

To reach Jaeger directly instead of through the proxy, point the adapter at the container port and carry the base path in the API prefix:

```bash
--jaeger-url http://localhost:16686 --jaeger-api-prefix /jaeger/ui/api
```

LGTM Grafana health:

```bash
curl -s http://127.0.0.1:3001/api/health
```

Expected: `{"database":"ok"}`.

Upstream Grafana: open `http://localhost:8080/grafana` and confirm Prometheus, Jaeger, OpenSearch, LGTM Tempo, and LGTM Loki are provisioned, and that the `Astronomy Shop Incident Investigation` dashboard renders its eight panels. If admin credentials are required, use the values from the upstream demo docs rather than guessing.

## 4. Adapter smoke

```bash
cd ../../integration/astronomy-shop
COPILOT_API_KEY=... python3 -m adapter \
  --ingest-url http://localhost:8000/ingest/events \
  --preset checkout-error-ratio \
  --jaeger-service checkout \
  --once
```

If the Copilot endpoint is configured with per-service keys
(`INGEST_KEYS=checkout:key-for-checkout,...`), pass the same pairs so each
service is sent with the key bound to it:

```bash
COPILOT_INGEST_KEYS=checkout:key-for-checkout,payment:key-for-payment \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --jaeger-service checkout --once
```

Prometheus and Jaeger are reached through the frontend proxy; the overlay publishes LGTM's Tempo and Loki on loopback, so every backend the module uses is reachable with the default URLs.

Expected report:

- `fetched > 0` and `accepted > 0`;
- `source_errors` empty;
- `failed_batches` 0.

Run it a second time. Expected: `duplicates > 0` (or `accepted` smaller than the first run), proving deterministic IDs dedupe.

If a source errors, isolate it with `--preset` plus one backend at a time:

- Prometheus: check the Prometheus host port and the preset query.
- Loki: check the `LOKI_URL` and the `service_name` label actually used by the LGTM pipeline. The overlay publishes it on `127.0.0.1:3100`.
- Tempo: check the `TEMPO_URL`; the overlay publishes it on `127.0.0.1:3200`.
- Jaeger: check the API prefix. The default `--jaeger-url http://localhost:8080/jaeger/ui` already carries the upstream `base_path`; use `--jaeger-api-prefix` when pointing at a different deployment.

Verify on the Copilot side that the events exist (SQLite/event store or API, per the Copilot repo). Record one `trace_id` from an adapter message and confirm it is queryable.

## 5. Baseline and one fault

```bash
python3 tools/run_scenario.py T-01 --duration 300
python3 tools/run_scenario.py T-02 --duration 300
python3 tools/scenario_report.py --format md
```

Expected:

- T-01 produces no incident.
- T-02 raises the checkout/payment alerts in Grafana and a Copilot incident whose evidence points at payment.
- The T-02 snapshot is restored; `defaultVariant` for `paymentFailure` is back to `off`.
- `scenarios/runs/` contains one record and one snapshot per run.

Confirm flags are off:

```bash
curl -s http://localhost:8080/feature/api/read | grep -o '"paymentFailure"[^}]*' | head -c 200
```

If the vendor file is dirty after the run, restore it only after this check passes.

## 6. Full mode (only for T-08)

```bash
cd ../../vendor/opentelemetry-demo
docker compose -f compose.yaml -f compose.full.yaml -f compose.observability.yaml \
  -f compose.extras.yaml -f ../../integration/astronomy-shop/compose.overlay.yaml up -d
cd ../../integration/astronomy-shop
python3 tools/run_scenario.py T-08 --duration 300
```

Expected: consumer lag rises and the `AstronomyShopKafkaConsumerLag` rule becomes pending/firing. If `kafka_consumer_group_lag` is empty, inspect the Collector's Kafka receiver metric names before changing the alert.

## 7. Record results

Copy this table into `docs/status.md` or a run note and fill it in:

| Step | Command | Expected | Observed | Pass | Notes |
|---|---|---|---|---|---|
| Compose config | `docker compose config ...` | exit 0 | | | |
| Stack up | `docker compose ps` | all services running | | | |
| Prometheus | range query | non-zero series | | | |
| Loki | Grafana Explore | log lines | | | |
| Tempo | Grafana Explore | traces | | | |
| Jaeger | `/jaeger/ui/api/services` | service list | | | |
| Adapter run 1 | `--preset ... --once` | accepted > 0 | | | |
| Adapter run 2 | repeat | duplicates > 0 | | | |
| T-01 | runner | no incident | | | |
| T-02 | runner | payment incident | | | |
| Flags restored | read API | all `off` | | | |

## Known unknowns to resolve during this pass

- Alert thresholds versus observed baseline traffic.

Resolved during the backend audit, from the pinned upstream configuration:

- Tempo and Loki host exposure — the overlay publishes both on loopback (`127.0.0.1:3200`, `127.0.0.1:3100`).
- Exact Jaeger v2 API prefix — the upstream `src/jaeger/config.yml` sets `base_path: /jaeger/ui`, and Jaeger v2 registers its REST API under that prefix, so the query API is `/jaeger/ui/api/...`. The upstream Grafana Jaeger datasource uses the same base URL.
- Loki label scheme for OTLP logs — Loki's default `default_resource_attributes_as_index_labels` includes `service.name` as an index label, normalized to `service_name`.
- Kafka consumer-lag metric name — the contrib `kafkametrics` receiver emits `kafka.consumer_group.lag`, and Prometheus's OTLP translator joins the tokens with `_`, giving `kafka_consumer_group_lag`.
