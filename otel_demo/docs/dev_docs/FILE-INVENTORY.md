# File Inventory

State vocabulary: **verified** (a reproducible check passed), **implemented** (code/config exists, not live-verified), **blocked** (needs Docker/Copilot), **generated** (build artifact).

## Root and vendor

| Path | Purpose | State |
|---|---|---|
| `vendor/opentelemetry-demo/` | Vendored snapshot of upstream `85c2cb253ae3519ef2b3a02984c32614106de5ea` (pin recorded in `.pinned-commit`) | verified clean |
| `integration/astronomy-shop/` | Plug-and-play Astronomy Shop test module | verified locally; live validation optional |
| `docs/` | Living design and delivery record | verified |

## Docs

| Path | Purpose | State |
|---|---|---|
| `docs/HANDOFF.md` | Primary resume guide: status, artifacts, next action, estimates | verified |
| `docs/IMPLEMENTATION-NOTES.md` | How the system works, invariants, unverified assumptions, failure modes | verified |
| `docs/FILE-INVENTORY.md` | This file-by-file map | verified |
| `docs/VERIFICATION-MATRIX.md` | Requirement → artifact → check → status, and test coverage map | verified |
| `docs/RESUME-PROMPT.md` | Copy-paste prompt for the next AI agent | verified |
| `docs/status.md` | Implemented / verified / blocked state | verified |
| `docs/change-log.md` | Dated history with exact commands and results | verified |
| `docs/live-validation.md` | First live-run checklist with expected outputs | implemented |
| `docs/native-tools-spec.md` | Future Copilot read-only tool contract | reference only; outside this deliverable |
| `docs/native-tools.schema.json` | Machine-readable tool parameters/limits | verified by tests |
| `docs/requirements.md` | Functional, telemetry, security, NFR requirements | verified |
| `docs/architecture.md` | Target topology and component responsibilities | verified |
| `docs/integration-contract.md` | Ingest boundary and adapter responsibilities | verified |
| `docs/implementation-plan.md` | Chunk plan and exit criteria | verified |
| `docs/decisions.md` | Accepted decisions and rejected alternatives | verified |
| `docs/test-scenarios.md` | T-01…T-10 curriculum and expected evidence | verified |
| `docs/operations-runbook.md` | Start, inject, recover, reset, tooling | implemented |
| `docs/README.md` | Documentation index | verified |

## Integration root and Compose

| Path | Purpose | State |
|---|---|---|
| `integration/astronomy-shop/Makefile` | Task runner (`make check`, `make help`) | verified |
| `integration/astronomy-shop/README.md` | Layout and status | verified |
| `integration/astronomy-shop/compose.overlay.yaml` | LGTM service, collector extras mount, Grafana mounts | implemented; blocked live |
| `integration/astronomy-shop/otelcol-config-extras.yml` | OTLP/HTTP exporter to `lgtm:4318`, preserves upstream exporters | implemented; blocked live |

## Grafana

| Path | Purpose | State |
|---|---|---|
| `grafana/provisioning/datasources/lgtm.yaml` | Tempo (`lgtm-traces`) + Loki (`lgtm-logs`), trace→log pivot | implemented; blocked live |
| `grafana/provisioning/alerting/astronomy-shop-alerts.yaml` | Seven unified alert rules for T-02…T-08 | implemented; blocked live |
| `grafana/dashboards/astronomy-shop-investigation.json` | Eight-panel incident dashboard | implemented; blocked live |

## Adapter

| Path | Purpose | State |
|---|---|---|
| `adapter/events.py` | `Evidence`, `to_event`, deterministic IDs, ingest field clamping | verified |
| `adapter/client.py` | Build events, POST `/ingest/events` | verified (localhost HTTP) |
| `adapter/_http.py` | Bounded JSON GET | verified via clients |
| `adapter/_common.py` | Levels, service resolution, stable IDs, attribute lookup | verified |
| `adapter/prometheus.py` | Query/range + evidence mapping | verified with mocks |
| `adapter/loki.py` | LogQL range + evidence mapping | verified with mocks |
| `adapter/tempo.py` | Search/fetch + evidence mapping | verified with mocks |
| `adapter/jaeger.py` | Trace search + root-span mapping | verified with mocks |
| `adapter/poller.py` | Batching, per-service grouping and keys, watched-service filter, retry, source isolation, accounting | verified |
| `adapter/sources.py` | Trailing-window source factories | verified |
| `adapter/presets.py` | Nine dashboard-derived query presets | verified |
| `adapter/__main__.py` | CLI (`--preset`, `--list-presets`, `--ingest-keys`, `--watch-service`, `--once`) | verified |

## Tools

| Path | Purpose | State |
|---|---|---|
| `tools/build_corpus.py` | Deterministic corpus builder + `--check` | verified |
| `tools/record_scenario.py` | Manual run recorder | verified |
| `tools/flagd_ui.py` | flagd-ui read/write client | verified (local HTTP) |
| `tools/run_scenario.py` | Snapshot/toggle/restore/record runner | verified with mocks |
| `tools/scenario_report.py` | Run coverage report | verified |
| `tools/preflight.py` | Readiness checks | verified local; Docker/Compose blocked |

## Generated corpus

| Path | Purpose | State |
|---|---|---|
| `docs-corpus/corpus.jsonl` | 233 chunks / 37 documents | generated, `--check` clean |
| `docs-corpus/manifest.json` | Document inventory and provenance | generated, `--check` clean |
| `docs-corpus/README.md` | Format, provenance, regeneration | verified |

## Scenarios

| Path | Purpose | State |
|---|---|---|
| `scenarios/catalog.json` | T-01…T-10 mapped to flagd flags/variants | verified against `demo.flagd.json` |
| `scenarios/README.md` | Runner, recorder, report, safety | verified |
| `scenarios/runs/` | Runtime records and recovery snapshots | created at runtime |

## Tests (stdlib `unittest`)

| Path | Covers |
|---|---|
| `tests/test_events.py` | Schema, validation, explicit/deterministic IDs, field clamping |
| `tests/test_clients.py` | Prometheus, Loki, Tempo, Jaeger parsing and mapping |
| `tests/test_poller.py` | Batching, retries, isolation, receipt parsing, sources, per-service keys, watched-service filter |
| `tests/test_ingest_http.py` | Real localhost POST, API key, JSON array |
| `tests/copilot_contract.py` | Local stand-in for the Copilot's ingest endpoint, transcribed from the Copilot at `0ce9844` |
| `tests/test_copilot_contract.py` | The adapter's real client against that contract: auth, rate limit, body size, batch size, schema, service binding, dedupe |
| `tests/test_config.py` | Overlay mounts, exporter names, datasources, alerts |
| `tests/test_dashboard.py` | Dashboard structure, UIDs, metrics, mount |
| `tests/test_corpus.py` | Metadata, coverage, determinism, retrieval, pin |
| `tests/test_scenarios.py` | Catalog validity, recorder, timestamps |
| `tests/test_run_scenario.py` | Toggle/restore, interruption, CLI, flagd-ui HTTP |
| `tests/test_scenario_report.py` | Latest run, missing runs, escaping, CLI |
| `tests/test_presets.py` | Preset integrity, service overrides, CLI, per-service keys, watch-service warning |
| `tests/test_native_tools.py` | Future-tool schema/spec reference contract and read-only guarantee |
| `tests/test_preflight.py` | Files/vendor/corpus, Compose, Docker, probes, report |

## External prerequisites / intentionally absent

- Copilot runtime checkout (remote `https://github.com/satwikt/incident_response_agent`); intentionally outside this test-module deliverable.
- Docker daemon (optional live validation).
- Loki/Tempo live response shapes from LGTM (the overlay publishes both on loopback; the `service_name` label scheme is resolved from upstream source but unobserved at runtime).
