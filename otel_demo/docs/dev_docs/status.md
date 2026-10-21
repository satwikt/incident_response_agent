# Status

Last updated: 2026-10-20

## Overall state

**The plug-and-play Astronomy Shop test module (overlay/Grafana, adapter loop, docs corpus, scenario catalog, and local checks) is implemented and locally verified; live stack execution remains blocked because Docker is not installed in this worker. Copilot-side changes are outside this deliverable.**

## Completed

- Inspected the current Incident Response Agent repository.
- Confirmed its current push-based telemetry boundary and Hindsight design.
- Evaluated Astronomy Shop, Online Boutique, Spring Petclinic, PostHog, Train Ticket, and smaller observability demos.
- Selected Astronomy Shop.
- Verified native flagd/OpenFeature scenarios and official documentation.
- Created requirements, architecture, integration contract, implementation plan, decisions, test scenarios, and runbook.
- Added isolated Astronomy Shop integration workspace under `integration/astronomy-shop/`.
- Added an LGTM/Jaeger Compose overlay template and Collector extras template.
- Added a backend-neutral telemetry event normalizer and bounded Copilot ingest sender.
- Cloned upstream Astronomy Shop into `vendor/opentelemetry-demo`.
- Pinned inspection baseline to commit `85c2cb253ae3519ef2b3a02984c32614106de5ea`.
- Fixed the overlay so the integration extras config is actually mounted at `/etc/otelcol-config-extras.yml`; without that mount the LGTM exporter was never loaded.
- Added read-only clients: `adapter/prometheus.py`, `adapter/loki.py`, `adapter/tempo.py`, `adapter/jaeger.py`, plus shared `_http.py`/`_common.py` helpers.
- Added `adapter/poller.py`: 500-event and 1 MiB body-aware batching, bounded exponential backoff, per-source failure isolation, accepted/duplicate/dropped accounting.
- Added `adapter/sources.py`: trailing-window source factories for Prometheus, Loki, Tempo, and Jaeger.
- Added `adapter/__main__.py`: runnable loop with flags/env, `--once` mode, and no invented default queries.
- Added deterministic event IDs for Prometheus samples, log records, and traces so repeated polls dedupe instead of duplicating.
- Added Grafana Tempo/Loki datasources with a trace-to-log pivot, mounted into the upstream Grafana by the overlay.
- Converted adapter tests to stdlib `unittest` because pytest is unavailable; added mocked client tests, poller tests, a real localhost ingest test, and static overlay contract tests.
- Built the documentation corpus from the pinned vendor READMEs, feature-flag definition, and Copilot docs: 37 documents / 233 chunks with provenance, deterministic IDs, and a `--check` staleness guard.
- Added the T-01…T-10 scenario catalog validated against the vendored flagd definitions, plus a run recorder that captures UTC window, evidence, Copilot result, Hindsight path, and recovery.
- Added seven Grafana unified alert rules for payment errors, checkout error rate, shipping latency, recommendation latency, product-catalog latency, Postgres deadlocks, and Kafka consumer lag; metric names and labels mirror the vendored dashboards.
- Added a flagd-ui control client and scenario runner that snapshots the full flag state, toggles the catalog variant, restores the snapshot in a `finally` block, and records the run automatically.
- Added a scenario report that joins recorded runs with the catalog, selects the latest run per scenario, lists missing runs, and emits Markdown or JSON.
- Added adapter query presets derived from the vendored dashboards (checkout, payment, shipping, recommendation, product catalog) with explicit service overrides, exposed via `--preset` and `--list-presets`.
- Added `docs/live-validation.md`: a step-by-step first-run checklist with commands, expected outputs, pass criteria, and a results table.
- Added the future-work tool contract as reference only: `docs/native-tools-spec.md` and `docs/native-tools.schema.json` (seven read-only tool schemas, common envelope, limits, safety, rollout, test plan).
- Added `tools/preflight.py`: read-only readiness checks for required files, vendor pin, corpus freshness, Docker availability, Compose config (`--execute`), and stack probes (`--probe`).
- Added the upstream-Grafana incident dashboard (`grafana/dashboards/astronomy-shop-investigation.json`) with checkout/payment/shipping/recommendation/product-catalog/Postgres/Kafka panels and a Loki logs panel, mounted through the overlay.
- Added a task runner (`Makefile`) with a consolidated `check` target plus test, corpus, preflight, report, dry-run, and adapter-help commands.
- Audited which observability backends are actually wired into the module and closed three gaps: the Jaeger query API is now addressed at the upstream `base_path` (`/jaeger/ui/api`), LGTM's Tempo and Loki are published on loopback so the adapter's default URLs resolve, and the preflight probes now cover the upstream Grafana, Prometheus, and both LGTM backends.
- Checked the module against the Copilot repository (`github.com/satwikt/incident_response_agent`, `0ce9844`) and closed the integration gaps that check exposed: ingest requests are now grouped per service so a batch never mixes services (`403 service mismatch` on the Copilot side), per-service ingest keys are configurable with `--ingest-keys`/`COPILOT_INGEST_KEYS`, evidence now reads the demo's real attribute names (`http.response.status_code`, `http_route`), and out-of-range status/duration values are dropped instead of rejecting a whole batch.
- Added the handoff package: `docs/IMPLEMENTATION-NOTES.md` (architecture/invariants), `docs/FILE-INVENTORY.md` (file-by-file state), `docs/VERIFICATION-MATRIX.md` (requirement→check mapping), and `docs/RESUME-PROMPT.md` (self-contained next-agent brief).
- Recorded decisions D-12…D-18 and added a tooling section to the operations runbook.
- Added `tests/copilot_contract.py`: a local stand-in for the Copilot's ingest endpoint with every rule transcribed from `Copilot/api/ingest.py` at `0ce9844` (auth, per-service rate limit, body size, batch size, atomic schema validation, service binding, dedupe). `tests/test_copilot_contract.py` runs the adapter's real HTTP client against it.
- Fixed three defects the contract test exposed: an over-long `route` is now truncated to 200 characters (the Copilot *rejects* a longer one and takes the whole batch with it), an event without an explicit id now gets a deterministic derived id (so re-polls still dedupe), and batches are split per service *and* per chunk, not merely made contiguous.
- Added `--watch-service`/`COPILOT_WATCH_SERVICE`: evidence for any service other than the one the Copilot watches is dropped and reported instead of being sent and refused. A startup warning names every source that targets another service.
- Resolved the two open assumptions from the pinned upstream source: the Loki `service_name` label (Loki's default OTLP index labels include `service.name`) and the Kafka metric name `kafka_consumer_group_lag` (the contrib receiver emits `kafka.consumer_group.lag` and Prometheus's OTLP translator joins the tokens with `_`).

## External validation not performed

- Live Compose startup/config validation (blocked: `docker` command not found in this environment).
- Live confirmation of Grafana datasource wiring, dashboard rendering, and trace-to-log pivot.
- Ingesting the generated corpus into the Copilot's own documentation index is outside this test-module deliverable.
- Native Copilot backend tools and registration are outside this test-module deliverable; the spec/schema is retained as future reference only.
- Live fault-injection test execution.
- Empirical backend API compatibility checks (response shapes, Loki timestamp units). The Jaeger API prefix, the Loki `service_name` label scheme, and the Kafka `kafka_consumer_group_lag` metric name are resolved from the pinned upstream source; the response *shapes* are still unobserved.
- Live delivery to a running Copilot: the per-service batching, per-service key routing, field-range clamping, and route truncation are verified against the Copilot's source and through `tests/copilot_contract.py`, but the contract test runs against a transcription, not the real FastAPI endpoint.

## Integration constraints that live outside this module

These are properties of the Copilot at `0ce9844`, not defects in the module. They bound what the module can demonstrate end to end.

- **The Copilot watches one service.** `APP_SERVICE_NAME` selects a single watched service, so multi-service *ingest* works but multi-service *detection* does not. Choose the service the curriculum targets and set `APP_SERVICE_NAME` to match the `service` field the adapter sends. `--watch-service`/`COPILOT_WATCH_SERVICE` drops the rest and warns at startup about any source that targets another service.
- **Keys are bound to one service.** `INGEST_KEYS=service:key,...`; a batch naming a different service is rejected with `403 service mismatch` and the whole batch is lost. The adapter sends one request per service to match.
- **A batch is validated atomically.** One invalid event rejects the whole batch (`422`). The adapter clamps `status` to 100–599 and `duration_ms` to 0–3 600 000 so a stray label cannot take a batch down.
- **Evidence shape decides what is detected.** Error-rate detection needs route and status; latency and slow-request detection need route and duration. Only Jaeger/Tempo trace evidence carries all three; Prometheus presets supply route and status but no duration, and Loki log lines supply neither.
- **A route with fewer than 20 requests in the window is "not enough data"**, never "unhealthy". The `MIN_REQUESTS` default therefore has to be met before any breach fires.
- **Timestamps must be within 5 minutes in the future and 7 days in the past.** The adapter's default 300-second window is well inside that; a much larger `--window` would be rejected.

## Verification

- `python3 -m py_compile integration/astronomy-shop/adapter/*.py integration/astronomy-shop/tools/*.py integration/astronomy-shop/tests/*.py` — passed.
- `cd integration/astronomy-shop && python3 -m unittest discover -s tests` — 139 tests, OK.
- `python3 tools/build_corpus.py --check` — corpus is up to date (37 documents, 233 chunks).
- `python3 tools/record_scenario.py` — covered by tests; writes a JSON run record.
- `python3 tools/run_scenario.py T-02 --dry-run` — exit 0, prints the plan, sends no request.
- Runner restore-on-interrupt, baseline no-write, invalid-variant, and unreachable-UI behavior covered by tests; flagd-ui client round-trip tested against a local HTTP server.
- `python3 tools/scenario_report.py --runs scenarios/runs` — exit 0; empty runs correctly reports 0/10 covered.
- `python3 -m adapter --list-presets` — exit 0; prints nine presets.
- Native tool schema/spec contract checked by `tests/test_native_tools.py` (names, parameters, limits, error codes, safety).
- `python3 tools/preflight.py --local-only` — 3 pass, 0 fail, 2 skip, exit 0.
- Final local module release check: `make check` — 139 tests, corpus up to date (37 documents / 233 chunks), and local preflight 3 pass, 0 warn, 0 fail, 2 skip; `make dry-run` made no request or record; adapter help, scenario report, Makefile help, and Python compilation passed; vendor checkout remained clean.
- Default `python3 tools/preflight.py` — exit 1 because Docker is absent; the missing executable is reported as a structured fail, not a traceback.
- `python3 tools/preflight.py --execute --probe` — exit 1 with `3 pass, 0 warn, 6 fail, 0 skip`; Docker/Compose and stack probes are reported as structured failures.
- `python3 -m adapter --help` — entrypoint loads, exit 0.
- `make help` — lists targets; `make -n test` prints the unittest command.
- CLI smoke: no sources → exit 2; unreachable Prometheus → exit 0 with `source_errors` recorded and no crash.
- `docker compose config -f compose.yaml -f compose.full.yaml -f compose.observability.yaml -f compose.extras.yaml -f ../../integration/astronomy-shop/compose.overlay.yaml` — **blocked**: `docker: command not found`.
- Copilot project remote verified read-only at `https://github.com/satwikt/incident_response_agent`; `main` currently resolves to `0ce98443db0b0b3c09c40a53849164c1f7160fe9`, but no checkout or runtime endpoint is present here.
- Overlay mounts, exporter names, and Grafana datasource UIDs checked statically by `tests/test_config.py`.
- Scenario flags/variants checked against `demo.flagd.json` by `tests/test_scenarios.py`.
- Alert rule count, datasource UID, metric names, scenario labels, and overlay mount checked statically by `tests/test_config.py`; Grafana has not loaded the rules live.
- Kafka `kafka_consumer_group_lag` is the receiver's documented name and is confirmed against the pinned upstream source; it is still unobserved in a running deployment.

## Optional live-validation next action

If live validation is requested on a host with Docker, follow `docs/live-validation.md` end to end. In short: run the overlay validation command from `docs/HANDOFF.md`, start minimal mode, and execute the adapter once:

```bash
cd integration/astronomy-shop
COPILOT_API_KEY=... python3 -m adapter --ingest-url ... --preset checkout-error-ratio --jaeger-service checkout --once
```

Confirm one metric and one trace reach Copilot ingest and dedupe on repeat polls; verify Loki/Tempo on their published loopback ports. Record exact commands/results here and in `change-log.md`.

## Status vocabulary

- **Planned:** design exists; not implemented.
- **Implemented:** code/config exists; live verification may still be pending.
- **Verified:** a reproducible check passed and is recorded.
- **Blocked:** a specific dependency or decision prevents progress.
