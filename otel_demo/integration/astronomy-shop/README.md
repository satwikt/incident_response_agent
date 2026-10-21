# Astronomy Shop Integration

This directory contains configuration and tooling owned by the AI SRE Copilot integration. The upstream Astronomy Shop application is vendored at `../../vendor/opentelemetry-demo`; it remains an external dependency and is not modified here.

## Layout

- `compose.overlay.yaml` — LGTM backend overlay to combine with upstream Compose files.
- `otelcol-config-extras.yml` — Collector merge layer for dual-exporting telemetry.
- `grafana/provisioning/datasources/lgtm.yaml` — Tempo/Loki datasources for the upstream Grafana.
- `grafana/provisioning/alerting/astronomy-shop-alerts.yaml` — unified alerts for the fault curriculum.
- `grafana/dashboards/astronomy-shop-investigation.json` — incident investigation dashboard.
- `adapter/` — read-only telemetry clients, polling loop, query presets, and `python3 -m adapter` entrypoint.
- `tools/build_corpus.py` — deterministic documentation corpus builder.
- `tools/record_scenario.py` — fault-run recorder.
- `tools/flagd_ui.py`, `tools/run_scenario.py` — snapshot/restore-driven scenario runner.
- `tools/scenario_report.py` — run coverage report.
- `tools/preflight.py` — read-only live-stack readiness checks.
- `docs-corpus/` — generated JSONL corpus and manifest.
- `scenarios/` — catalog validated against flagd, plus recorded runs.
- `tests/` — adapter, corpus, scenario, and config tests.

## Local quick start

The module has no third-party Python dependency and does not require Docker for its local checks:

```bash
cd integration/astronomy-shop
make check
make dry-run
make adapter-help
```

`make check` runs the 139-test stdlib `unittest` suite, verifies the generated corpus, and runs local preflight checks. `make dry-run` validates the T-02 scenario plan without contacting flagd-ui or writing a run record.

The module does not modify the vendored application source. `../../vendor/opentelemetry-demo` is a vendored snapshot of the pinned revision `85c2cb253ae3519ef2b3a02984c32614106de5ea`; the pin is recorded in `vendor/opentelemetry-demo/.pinned-commit`, and `make check` fails if that record drifts or if the local copy differs from the committed one.

## Live prerequisites

Live checks additionally require Docker Compose v2, enough resources for the Astronomy Shop stack and LGTM, and a running Copilot ingest endpoint plus API key for adapter delivery. The Copilot repository itself is not required to run this test module.

From the pinned upstream checkout, start the minimal stack with observability and this overlay:

```bash
cd vendor/opentelemetry-demo
docker compose -f compose.yaml -f compose.observability.yaml \
  -f compose.extras.yaml -f ../../integration/astronomy-shop/compose.overlay.yaml up -d
cd ../../integration/astronomy-shop
make live-preflight
```

`make live-preflight` is read-only: it resolves the Compose layers and probes the running store, flagd-ui, upstream Grafana, Jaeger UI, Prometheus, and the LGTM Tempo/Loki backends. It does not start or stop services.

Once the stack and Copilot endpoint are available, the adapter can be run explicitly:

```bash
COPILOT_API_KEY=... python3 -m adapter \
  --ingest-url http://localhost:8000/ingest/events \
  --preset checkout-error-ratio \
  --jaeger-service checkout \
  --once
```

### Connecting to the Copilot ingest endpoint

The Copilot binds every ingest key to exactly one service and rejects a whole
batch that names any other service (`403 service mismatch`). The adapter
therefore sends one request per service, each with that service's key:

```bash
# Copilot side: one key per watched service.
INGEST_KEYS=checkout:key-for-checkout,payment:key-for-payment

# Adapter side: the same service:key pairs.
COPILOT_INGEST_KEYS=checkout:key-for-checkout,payment:key-for-payment \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --jaeger-service checkout --once
```

`--ingest-keys` / `COPILOT_INGEST_KEYS` is optional: with a single key
(`COPILOT_API_KEY`) the adapter still sends one request per service, so a
service the key is not bound to fails on its own instead of taking the whole
batch down. Services listed in neither are dropped and reported in
`source_errors` rather than sent with a key bound elsewhere.

### Only one service is watched

The Copilot's `APP_SERVICE_NAME` selects a single watched service, so evidence
for any other service can never raise a breach there. Set
`--watch-service` / `COPILOT_WATCH_SERVICE` to that service: everything else is
dropped and reported in `source_errors`, and the adapter prints a startup
warning naming every source that targets a different service.

```bash
COPILOT_WATCH_SERVICE=checkout \
COPILOT_INGEST_KEYS=checkout:key-for-checkout \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --jaeger-service checkout --once
```

The nine presets span five services. Without `--watch-service` the adapter
sends them all and the Copilot drops what it does not watch; with it, the
adapter drops them first and says so.

The upstream stack ships Prometheus and Jaeger but neither Tempo nor Loki; the overlay adds LGTM for those and publishes them on loopback (`127.0.0.1:3200` for Tempo, `127.0.0.1:3100` for Loki), so the adapter's default URLs resolve without further setup. See `docs/live-validation.md` for the complete live checklist.

## Scope and verification boundary

This directory is the plug-and-play test module: overlay, telemetry adapter, documentation corpus, scenario catalog/runner, Grafana configuration, preflight checks, and local tests. Local parsing, batching, safety, static configuration, and mocked HTTP behavior are verified. Live Compose behavior, backend compatibility, Copilot ingest/deduplication, fault execution, and alert calibration require the external runtime prerequisites above and remain unverified here.
