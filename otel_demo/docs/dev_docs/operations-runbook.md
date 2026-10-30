# Operations Runbook

## Start baseline

```bash
cd opentelemetry-demo
make start-minimal
```

Use the official Docker deployment command if `make` is unavailable. Confirm:

- web store/proxy
- Grafana
- Prometheus
- Jaeger
- Collector health
- load generator

## Inject native fault

1. Open `http://localhost:8080/feature`.
2. Select the flag.
3. Choose a non-`off` variant.
4. Apply/save.
5. Wait for traffic and telemetry.

## Recover

1. Set the flag to `off`.
2. Wait for configured healthy windows.
3. Confirm application metrics and traces return to baseline.
4. Record the incident and recovery timestamps.

## Full-mode scenarios

Use the full Compose layers for Kafka scenarios only. Do not mix minimal and full containers without a clean reset.

## Troubleshooting order

1. Check Collector health/logs.
2. Check Prometheus ingestion.
3. Check Loki/Tempo/Jaeger availability.
4. Check flagd state.
5. Check load-generator traffic.
6. Check application service health.
7. Only then debug the Copilot.

## Tooling

Run from `integration/astronomy-shop` unless noted:

```bash
make check
make dry-run
make adapter-help
make report
make help
```

`make check` runs the test suite, corpus freshness check, and local preflight without Docker.

Optional live readiness and stack probes (Docker host, running stack):

```bash
python3 tools/preflight.py --execute --probe
```

Scenario run with automatic restore:

```bash
python3 tools/run_scenario.py T-02 --duration 300
python3 tools/scenario_report.py --format md
```

Adapter smoke (Prometheus and Jaeger are reached through the frontend proxy; LGTM Tempo/Loki are on loopback):

```bash
COPILOT_API_KEY=... python3 -m adapter \
  --ingest-url http://localhost:8000/ingest/events \
  --preset checkout-error-ratio --jaeger-service checkout --once
```

With per-service keys, pass them as `service:key` pairs so each service is sent
with the key bound to it:

```bash
COPILOT_INGEST_KEYS=checkout:key-for-checkout,payment:key-for-payment \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --jaeger-service checkout --once
```

The Copilot watches one service (`APP_SERVICE_NAME`). Name it with
`COPILOT_WATCH_SERVICE` so evidence for the other services is dropped and
reported instead of being sent and refused:

```bash
COPILOT_WATCH_SERVICE=checkout \
COPILOT_INGEST_KEYS=checkout:key-for-checkout \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --jaeger-service checkout --once
```

## Reset

- Turn all flags off.
- Stop the stack.
- Remove only test volumes when a clean data reset is required.
- Restart in the selected mode.
- Record the reset command and timestamp.
