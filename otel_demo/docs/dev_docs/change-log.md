# Change Log

## 2026-10-20 — Documentation baseline

### Added

- `docs/README.md`
- `docs/requirements.md`
- `docs/architecture.md`
- `docs/integration-contract.md`
- `docs/implementation-plan.md`
- `docs/decisions.md`
- `docs/test-scenarios.md`
- `docs/operations-runbook.md`
- `docs/status.md`

### Decisions recorded

- Astronomy Shop is the primary testbed.
- Minimal mode is the first deployment.
- Native flagd faults precede external chaos.
- Existing Copilot `/ingest/events` remains the first integration boundary.
- Loki/Tempo/Jaeger support is a deployment overlay, not an assumption about the upstream default.

### Verification

- Research and repository inspection completed.
- No Astronomy Shop or Copilot integration code has been changed in this workspace.

## 2026-10-20 — Chunk 1 integration skeleton

### Added

- `integration/astronomy-shop/README.md`
- `integration/astronomy-shop/compose.overlay.yaml`
- `integration/astronomy-shop/otelcol-config-extras.yml`
- `integration/astronomy-shop/adapter/`
- `integration/astronomy-shop/tests/test_events.py`

### Design

- Upstream application remains untouched.
- Adapter normalizes evidence into the existing Copilot ingest contract.
- Correlation IDs are preserved in event context until native query tools are added.
- No remediation, shell execution, source inspection, or direct database access is included.

### Verification

- Pytest was attempted but is unavailable in the worker (`No module named pytest`).
- Live Compose validation has not been run.

## 2026-10-20 — Upstream application checkout

### Added

- `vendor/opentelemetry-demo/`

### Pinned revision

```text
85c2cb253ae3519ef2b3a02984c32614106de5ea
```

### Policy

- Do not edit upstream application files during the initial integration.
- Keep Copilot-owned overlays and adapter code under `integration/astronomy-shop/`.
- Record any future upstream revision change here and in `docs/status.md`.

### Verification

- `git status --short --branch` is clean in the vendor checkout.

## 2026-10-20 — Backend clients and overlay fix

### Changed

- `integration/astronomy-shop/compose.overlay.yaml`: mounted the integration extras config at `/etc/otelcol-config-extras.yml` so the LGTM exporter is loaded by the collector.
- Added `adapter/_http.py` and `adapter/_common.py` shared helpers.
- Added `adapter/prometheus.py`: `query`/`query_range`, sample parsing, evidence conversion.
- Added `adapter/loki.py`: `query_range`, stream parsing, structured-level extraction, evidence conversion.
- Added `adapter/tempo.py`: `search`, `get_trace`, trace-summary evidence conversion.
- Added `adapter/jaeger.py`: `find_traces`, root-span selection, evidence conversion.
- Widened `Evidence.timestamp` to accept epoch seconds as well as ISO strings.
- Converted `tests/test_events.py` from pytest to stdlib `unittest`.
- Added `tests/test_clients.py` and `tests/test_config.py`.

### Commands/checks

```bash
python3 -m py_compile integration/astronomy-shop/adapter/*.py integration/astronomy-shop/tests/*.py
cd integration/astronomy-shop && python3 -m unittest discover -s tests -v
docker compose config -f compose.yaml -f compose.full.yaml -f compose.observability.yaml -f compose.extras.yaml -f ../../integration/astronomy-shop/compose.overlay.yaml
```

### Result

- Compilation: passed.
- Unit tests: 14 tests, OK.
- Compose validation: blocked, `docker: command not found`.

### Open issues

- Live API compatibility (response shapes, Jaeger API prefix, Loki timestamp units) still needs verification against a running stack.
- The overlay assumes it is combined with `compose.observability.yaml`; otherwise `otlp_grpc/jaeger` is undefined.

### Status update

- Updated `docs/status.md`.

## 2026-10-20 — Polling loop, deterministic IDs, Grafana datasources

### Changed

- Added `adapter/poller.py`: batch chunking by count (max 500) and serialized body size (1 MiB ingest cap), bounded exponential backoff, retry classification, per-source failure isolation, accepted/duplicate/dropped accounting.
- Split `client.py` into `build_events`/`send_events` so events are normalized once before size-aware chunking.
- Added `adapter/sources.py`: trailing-window factories for Prometheus, Loki, Tempo, and Jaeger.
- Added `adapter/__main__.py`: runnable loop with flags/env and `--once`; no default queries are invented.
- Added deterministic event IDs in `events.py`/`_common.py` so repeated polls dedupe against the ingest store.
- Added `grafana/provisioning/datasources/lgtm.yaml` (Tempo, Loki) with a trace-to-log pivot, mounted into the upstream Grafana by the overlay.
- Added `tests/test_poller.py` and `tests/test_ingest_http.py`; extended client/config/event tests.

### Commands/checks

```bash
python3 -m py_compile integration/astronomy-shop/adapter/*.py integration/astronomy-shop/tests/*.py
cd integration/astronomy-shop && python3 -m unittest discover -s tests
python3 -m adapter --help
```

### Result

- Compilation: passed.
- Unit tests: 33 tests, OK.
- CLI entrypoint: `--help` exit 0.
- CLI smoke: no sources → exit 2; unreachable Prometheus → exit 0 with `source_errors` populated and the other sources unaffected.
- Live Compose validation: still blocked (`docker: command not found`).

### Open issues

- Grafana Tempo/Loki URLs (`lgtm:3200`, `lgtm:3100`) and trace-to-log pivot are unverified against a running LGTM container.
- Jaeger API prefix and Loki timestamp units remain unverified live.
- No live fault scenario has been executed.

### Status update

- Updated `docs/status.md` and `integration/astronomy-shop/adapter/README.md`.

## 2026-10-20 — Documentation corpus and scenario catalog

### Added

- `tools/build_corpus.py`: deterministic JSONL corpus builder covering the pinned vendor READMEs, feature-flag definitions, and Copilot docs; `--check` staleness guard.
- `docs-corpus/corpus.jsonl` and `docs-corpus/manifest.json`: 34 documents, 173 chunks, with GitHub provenance at commit `85c2cb253ae3519ef2b3a02984c32614106de5ea`.
- `scenarios/catalog.json`: T-01…T-10 mapped to flagd flags/variants, modes, expected services/causes, evidence hints, and recovery.
- `tools/record_scenario.py`: writes a run record with UTC window, evidence, Copilot result, Hindsight path, and recovery.
- `tests/test_corpus.py`, `tests/test_scenarios.py`, and READMEs for `docs-corpus/` and `scenarios/`.

### Commands/checks

```bash
cd integration/astronomy-shop
python3 tools/build_corpus.py
python3 tools/build_corpus.py --check
python3 -m unittest discover -s tests
```

### Result

- Corpus: 173 chunks from 34 documents, `--check` passes.
- Unit tests: 52 tests, OK.
- Scenario flags/variants validated against the vendored `demo.flagd.json`.
- Live execution: still blocked (`docker: command not found`).

### Open issues

- The Copilot's documentation indexer is not in this workspace, so corpus ingestion is not verified end to end.
- The retrieval helper is term-overlap only; it validates coverage, not production RAG quality.
- No scenario has been executed live.

### Status update

- Updated `docs/status.md` and the integration READMEs.

## 2026-10-20 — Curriculum alert rules

### Added

- `grafana/provisioning/alerting/astronomy-shop-alerts.yaml`: seven unified alert rules covering T-02/T-04 payment and checkout errors, T-05 shipping latency, T-06 recommendation latency, T-07 product-catalog latency and Postgres deadlocks, and T-08 Kafka consumer lag.
- Overlay mount for the alerting file.
- Static alert contract test in `tests/test_config.py`.

### Design

- Metric names and labels were extracted from the vendored dashboards and existing cart alert (`http_server_request_duration_seconds_*`, `traces_span_metrics_*`, `postgresql_deadlocks_total`, `service_name`, `http_response_status_code`).
- Rules query the existing `webstore-metrics` datasource; no contact point is added, so the upstream default policy applies.

### Commands/checks

```bash
cd integration/astronomy-shop
python3 -m unittest discover -s tests
```

### Result

- Unit tests: 53 tests, OK.
- Grafana has not loaded the rules live (Docker unavailable).
- YAML was not machine-parsed locally (no PyYAML/pip/Docker); structure mirrors the vendored Grafana provisioning files.

### Open issues

- Thresholds are starting points and need calibration against live baseline traffic.
- `kafka_consumer_group_lag` is unverified for this collector configuration (full mode only).

### Status update

- Updated `docs/status.md` and the integration README layout.

## 2026-10-20 — Scenario runner

### Added

- `tools/flagd_ui.py`: client for the vendored flagd-ui `GET /api/read` and `POST /api/write` endpoints.
- `tools/run_scenario.py`: snapshots the full flag state, applies the catalog variant, waits, restores the snapshot in `finally`, and writes a run record plus a recovery snapshot.
- `tests/test_run_scenario.py`: toggle/restore, interruption, baseline, invalid variant, dry-run, unreachable-UI, and local HTTP round-trip tests.
- Scenario README now documents automated runs, safety behavior, and manual recording.

### Commands/checks

```bash
cd integration/astronomy-shop
python3 tools/run_scenario.py T-02 --dry-run
python3 -m unittest discover -s tests
```

### Result

- Unit tests: 63 tests, OK.
- Dry run: exit 0, no requests sent.
- Unreachable flagd-ui: exit 2 with a one-line error, no files written.
- Live toggle: not executed (no Docker stack).

### Open issues

- `POST /api/write` replaces the whole flag document; concurrent writers can clobber each other and snapshot restore overwrites intervening changes.
- Only `defaultVariant` is changed; targeted flags (none in the current curriculum) would need targeting-aware updates.
- T-09/T-10 still require a manual operator action during the window.

### Status update

- Updated `docs/status.md`, `integration/astronomy-shop/README.md`, and `scenarios/README.md`.

## 2026-10-20 — Scenario report and handoff refresh

### Added

- `tools/scenario_report.py`: joins recorded runs with the catalog, uses the latest run per scenario, lists missing scenarios, and emits Markdown or JSON.
- `tests/test_scenario_report.py`: latest-run selection, snapshot exclusion, pipe escaping, empty-directory behavior, and CLI output.
- Rewrote `docs/HANDOFF.md` as a complete resume guide with chunk status, artifacts, verified commands, blockers, environment gotchas, exact next action, and remaining-work estimates.

### Commands/checks

```bash
cd integration/astronomy-shop
python3 -m unittest discover -s tests
python3 tools/scenario_report.py --runs /tmp/does-not-exist
```

### Result

- Unit tests: 68 tests, OK.
- Report CLI: exit 0; empty run directory reports 0/10 covered.
- Live execution remains blocked (`docker: command not found`).

### Status update

- Updated `docs/status.md`, `docs/HANDOFF.md`, `integration/astronomy-shop/README.md`, and `scenarios/README.md`.

## 2026-10-20 — Query presets and live validation checklist

### Added

- `adapter/presets.py`: nine dashboard-derived presets for checkout, payment, shipping, recommendation, and product-catalog signals, each carrying an explicit service override.
- `adapter/__main__.py`: `--preset` and `--list-presets`; aggregate queries stay valid evidence without guessing `service_name`.
- `docs/live-validation.md`: exact first-run checklist with commands, expected outputs, pass criteria, known unknowns, and a results table.
- `tests/test_presets.py`: preset integrity, backend coverage, CLI listing, unknown-preset rejection, source building, and service overrides.

### Commands/checks

```bash
cd integration/astronomy-shop
python3 -m unittest discover -s tests
python3 -m adapter --list-presets
```

### Result

- Unit tests: 75 tests, OK.
- `--list-presets`: exit 0, nine presets printed.
- Presets are starting points, not tuned thresholds; live execution remains blocked.

### Status update

- Updated `docs/status.md`, `docs/HANDOFF.md`, `adapter/README.md`, and `integration/astronomy-shop/README.md`.

## 2026-10-20 — Chunk 6 native tool contract

### Added

- `docs/native-tools-spec.md`: seven read-only tool contracts, common response envelope, limits, safety requirements, implementation notes, test plan, rollout, and open decisions.
- `docs/native-tools.schema.json`: machine-readable parameters and limits for `query_prometheus`, `query_loki`, `query_tempo`, `query_jaeger`, `get_trace`, `get_flag_state`, and `search_application_docs`.
- `tests/test_native_tools.py`: read-only tool set, closed schemas, required fields, row caps, error codes, envelope shapes, and spec coverage.

### Commands/checks

```bash
cd integration/astronomy-shop
python3 -m unittest discover -s tests
```

### Result

- Unit tests: 81 tests, OK.
- No Copilot code was written: the repository is not in this workspace.
- The spec is implementation-ready and marked blocked, not implemented.

### Status update

- Updated `docs/status.md` and `docs/HANDOFF.md`.

## 2026-10-20 — Preflight checker

### Added

- `tools/preflight.py`: read-only checks for required files, pinned vendor commit and cleanliness, corpus freshness, Docker availability, `docker compose config` (`--execute`), and stack HTTP probes (`--probe`).
- `tests/test_preflight.py`: files/vendor/corpus, Compose command and failure detail, Docker presence, local HTTP probes, summary/JSON output, and local-only exit code.
- `docs/live-validation.md`, `docs/HANDOFF.md`, and the integration README now reference the preflight step.

### Commands/checks

```bash
cd integration/astronomy-shop
python3 -m unittest discover -s tests
python3 tools/preflight.py --local-only
python3 tools/preflight.py
```

### Result

- Unit tests: 95 tests, OK.
- Local-only preflight: exit 0 (3 pass, 2 skip).
- Default preflight: exit 1 only because Docker is missing; correctly reported as a failure.

### Status update

- Updated `docs/status.md`, `docs/HANDOFF.md`, and `docs/live-validation.md`.

## 2026-10-20 — Incident dashboard

### Added

- `grafana/dashboards/astronomy-shop-investigation.json`: eight panels covering checkout 5xx ratio, payment error spans, shipping/recommendation/product-catalog p95, Postgres deadlocks, Kafka consumer lag, and a Loki logs panel.
- Overlay mount into the upstream Grafana demo dashboard provider.
- `tests/test_dashboard.py`: JSON shape, panel/grid contracts, provisioned datasource UIDs, verified metric names, logs panel, and mount path.

### Commands/checks

```bash
cd integration/astronomy-shop
python3 -m unittest discover -s tests
python3 tools/preflight.py --local-only
```

### Result

- Unit tests: 101 tests, OK.
- Preflight local: exit 0.
- Grafana has not loaded the dashboard (Docker unavailable).

### Status update

- Updated `docs/status.md`, `docs/HANDOFF.md`, `docs/live-validation.md`, and the integration README.

## 2026-10-20 — Task runner and handoff package

### Added

- `integration/astronomy-shop/Makefile`: `help`, `test`, `corpus`, `corpus-check`, `preflight`, `report`, `dry-run`, `adapter-help`.
- `docs/IMPLEMENTATION-NOTES.md`: architecture, module contracts, invariants, unverified assumptions, failure modes.
- `docs/FILE-INVENTORY.md`: file-by-file purpose and verification state.
- `docs/VERIFICATION-MATRIX.md`: requirement → artifact → check → status, and the test coverage map.
- `docs/RESUME-PROMPT.md`: self-contained brief for the next agent, including read order, phases, contracts, and definition of done.

### Changed

- `docs/HANDOFF.md`: companion-document section, Makefile artifact, Makefile checks.
- `docs/decisions.md`: D-12…D-17 (deterministic IDs, byte-aware batching, explicit service overrides, flagd-ui scenario control, read-only tools, vendored-metric-derived config).
- `docs/operations-runbook.md`: tooling and scenario commands.
- `docs/README.md`: index entries for the new documents.

### Commands/checks

```bash
cd integration/astronomy-shop
make help
make -n test
make test
make corpus-check
make preflight
```

### Result

- `make help` and `make -n test`: exit 0.
- Unit tests: 101 tests, OK.
- Corpus: regenerated to 175 chunks / 34 documents after editing two corpus sources (`decisions.md`, `operations-runbook.md`); `--check` passes.
- Preflight local: 3 pass, 0 fail, 2 skip; exit 0.
- Documentation-only change; no runtime code was modified.

### Open issues

- Live stack, Grafana loading, fault execution, and Copilot integration remain blocked on Docker and the Copilot repository.

### Status update

- Updated `docs/status.md` and `docs/HANDOFF.md`.

## 2026-10-20 — Chunk 0 local baseline revalidation and Copilot remote

### Changed

- Reconciled current documentation with the verified local test count: `docs/live-validation.md` now expects 101 tests, `docs/HANDOFF.md` lists 101 tests, and `docs/VERIFICATION-MATRIX.md` matches the non-verbose Makefile command.
- Updated `docs/README.md`, `docs/status.md`, `docs/HANDOFF.md`, `docs/FILE-INVENTORY.md`, `docs/VERIFICATION-MATRIX.md`, `docs/native-tools-spec.md`, and `docs/RESUME-PROMPT.md` to identify the Copilot remote and distinguish its known remote from the absent local runtime checkout.

### Commands/checks

```bash
cd integration/astronomy-shop
make test
make corpus-check
make preflight
python3 tools/run_scenario.py T-02 --dry-run
python3 -m adapter --list-presets
make help
python3 tools/scenario_report.py --runs scenarios/runs
python3 -m py_compile adapter/*.py tools/*.py tests/*.py
cd ../..
git -C vendor/opentelemetry-demo status --short --branch
git -C vendor/opentelemetry-demo rev-parse HEAD
git ls-remote https://github.com/satwikt/incident_response_agent.git HEAD refs/heads/main refs/heads/master
```

### Result

- The first attempt from the workspace root failed because the integration Makefile and adapter module are under `integration/astronomy-shop`; the commands were rerun from the documented directory.
- `make test`: `Ran 101 tests in 1.575s` / `OK`.
- `make corpus-check`: `corpus is up to date`.
- `make preflight`: `3 pass, 0 warn, 0 fail, 2 skip`; Docker and Compose are explicitly skipped by `--local-only`.
- The T-02 dry-run printed its plan with `note: dry run; no request sent and nothing recorded`.
- `--list-presets` printed all nine presets; `make help` listed the expected targets; the empty scenario report reported `0 of 10` covered; Python compilation completed with exit 0.
- Vendor state: `## main...origin/main`; HEAD `85c2cb253ae3519ef2b3a02984c32614106de5ea`; no diff stat output.
- Copilot remote access succeeded read-only; `HEAD` and `main` both resolved to `0ce98443db0b0b3c09c40a53849164c1f7160fe9`.

### Open issues

- `docker: command not found`; Compose resolution, startup, Grafana/backend compatibility, and live fault execution remain blocked.
- The Copilot runtime checkout, documentation index, ingest endpoint, and API key are not present in this worker, so real ingest/dedupe and native-tool registration remain blocked.
- No scenario run records exist yet; the empty report is local scaffolding evidence only.

### Status update

- Updated `docs/status.md`, `docs/change-log.md`, `docs/HANDOFF.md`, `docs/FILE-INVENTORY.md`, `docs/VERIFICATION-MATRIX.md`, `docs/live-validation.md`, `docs/README.md`, `docs/native-tools-spec.md`, and `docs/RESUME-PROMPT.md`.

## 2026-10-20 — Preflight missing-Docker robustness

### Changed

- `integration/astronomy-shop/tools/preflight.py`: catch `OSError` when `docker compose config` cannot start and return a structured failed check instead of propagating a traceback.
- `integration/astronomy-shop/tests/test_preflight.py`: added a regression test for a missing Docker executable.
- Updated current documentation references from 101 to 102 integration tests; earlier change-log entries retain the counts observed when they were written.

### Commands/checks

```bash
cd integration/astronomy-shop
python3 -m unittest discover -s tests
python3 tools/build_corpus.py --check
python3 tools/preflight.py --local-only
python3 tools/preflight.py --execute --probe
```

### Result

- Unit tests: `Ran 102 tests in 1.073s` / `OK`.
- Corpus check: up to date.
- Local-only preflight: `3 pass, 0 warn, 0 fail, 2 skip`; exit 0.
- Full preflight with probes: no traceback; it reported `3 pass, 0 warn, 6 fail, 0 skip` and exit 1 because Docker is unavailable and the stack probes were refused.
- The Compose check now reports `unable to run docker: No such file or directory` when the executable is absent.

### Open issues

- Docker remains unavailable, so Compose resolution, stack startup, live backend compatibility, Grafana loading, Copilot ingest/dedupe, and fault execution remain blocked.

### Status update

- Updated `docs/status.md`, `docs/HANDOFF.md`, `docs/VERIFICATION-MATRIX.md`, `docs/live-validation.md`, and `docs/RESUME-PROMPT.md`.

## 2026-10-20 — Plug-and-play module packaging

### Changed

- `integration/astronomy-shop/Makefile`: added a single `make check` target for the local test, corpus, and preflight checks.
- `integration/astronomy-shop/README.md`: documented local quick start, live prerequisites, read-only live preflight, adapter invocation, and the explicit verification boundary.
- Updated the handoff, file inventory, verification matrix, and status references for the consolidated target.

### Commands/checks

```bash
cd integration/astronomy-shop
make check
make dry-run
make adapter-help
make report
python3 -m py_compile adapter/*.py tools/*.py tests/*.py
make help
```

### Result

- `make check`: 102 tests passed; corpus is up to date; local preflight reports `3 pass, 0 warn, 0 fail, 2 skip`.
- T-02 dry-run: exit 0; no request sent and nothing recorded.
- Adapter help: exit 0 and lists the read-only CLI options.
- Scenario report: exit 0; no live runs are recorded, so coverage is `0 of 10`.
- Python compilation and Makefile help: passed.

### Open issues

- The module remains locally plug-and-play; live execution still requires Docker, a running Astronomy Shop stack, and a Copilot ingest endpoint/API key.

### Status update

- Updated `integration/astronomy-shop/README.md`, `integration/astronomy-shop/Makefile`, `docs/status.md`, `docs/HANDOFF.md`, `docs/FILE-INVENTORY.md`, and `docs/VERIFICATION-MATRIX.md`.

## 2026-10-20 — Test-module scope finalization

### Decision

- Recorded D-18: the delivered artifact is the plug-and-play Astronomy Shop test module only. Copilot repository changes, documentation-index ingestion, native-tool registration, Hindsight behavior, and remediation remain separate work.

### Changed

- Added `make check` to the module task runner and documented its local quick-start workflow.
- Marked Copilot-native-tool and documentation-index material as future-work reference rather than a blocker for this deliverable.
- Updated the handoff, resume prompt, requirements, implementation plan, implementation notes, integration contract, inventory, verification matrix, runbook, and status to preserve the test-module boundary.
- Regenerated `docs-corpus/` after its source documentation changed; it now contains 34 documents / 176 chunks.

### Commands/checks

```bash
python3 /home/vinit/SRE\ Copilot/integration/astronomy-shop/tools/build_corpus.py
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop check
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop dry-run
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop adapter-help
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop report
python3 -m py_compile /home/vinit/SRE\ Copilot/integration/astronomy-shop/adapter/*.py /home/vinit/SRE\ Copilot/integration/astronomy-shop/tools/*.py /home/vinit/SRE\ Copilot/integration/astronomy-shop/tests/*.py
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop live-preflight
git -C /home/vinit/SRE\ Copilot/vendor/opentelemetry-demo status --short --branch
```

### Result

- An initial relative corpus command from the workspace root had no effect and failed because `tools/build_corpus.py` is module-relative; the absolute module command then generated the corpus successfully.
- `make check`: `Ran 102 tests in 1.588s` / `OK`; corpus current; local preflight `3 pass, 0 warn, 0 fail, 2 skip`.
- Corpus generation: 34 documents / 176 chunks; subsequent freshness checks pass.
- T-02 dry-run: exit 0, no request sent, no record written.
- Adapter help, scenario report (`0 of 10` live runs), Makefile help, and Python compilation: passed.
- Vendor state: clean (`## main...origin/main`).
- `make live-preflight`: Make returned exit 2 because the underlying preflight returned 1; it cleanly reported missing Docker/Compose and four refused live probes (`3 pass, 0 warn, 6 fail, 0 skip`) without a traceback.

### Open issues

- The module is complete for local, plug-and-play delivery. Optional live validation still requires Docker, a running Astronomy Shop stack, and a Copilot ingest endpoint/API key.
- Copilot-source work is intentionally out of scope for this deliverable.

### Status update

- Updated `integration/astronomy-shop/Makefile`, `integration/astronomy-shop/README.md`, the generated corpus, and the module delivery documentation.

## 2026-10-20 — Observability backend wiring audit

### Changed

- `adapter/__main__.py`: default `--jaeger-url` is now `http://localhost:8080/jaeger/ui` (was `http://localhost:16686`), and a new `--jaeger-api-prefix` / `JAEGER_API_PREFIX` flag (default `/api`) is passed to `JaegerClient`.
- `compose.overlay.yaml`: the LGTM service now publishes Tempo (`127.0.0.1:3200:3200`) and Loki (`127.0.0.1:3100:3100`) alongside Grafana (`127.0.0.1:3001:3000`).
- `tools/preflight.py`: probes extended from four to eight — added upstream Grafana (`/grafana/api/health`), Prometheus (`/-/healthy`), LGTM Tempo (`/ready`), and LGTM Loki (`/ready`).
- `adapter/jaeger.py`: docstring records that the query API lives under the deployment's base path.
- Tests: `test_clients.py` (base-path URL composition), `test_config.py` (overlay publishes Tempo/Loki), `test_preflight.py` (probe coverage), `test_presets.py` (default Jaeger URL and API prefix).
- Docs: module README, `adapter/README.md`, `docs/live-validation.md`, `docs/HANDOFF.md`, `docs/IMPLEMENTATION-NOTES.md`, `docs/VERIFICATION-MATRIX.md`, `docs/status.md`.

### Audit findings

- **The upstream stack ships no Tempo and no Loki.** `compose.yaml`, `compose.full.yaml`, and `compose.observability.yaml` define Jaeger, Prometheus, OpenSearch, and Grafana only. LGTM is the sole source for Tempo and Loki, so the adapter's three Loki presets and `--tempo-service` had no reachable endpoint in the module's own deployment. The overlay now publishes them.
- **The Jaeger query API path was wrong.** The upstream `src/jaeger/config.yml` sets `jaeger_query.base_path: /jaeger/ui`, and Jaeger v2 registers its REST API under that prefix, so the API is `/jaeger/ui/api/traces`, not `/api/traces`. The upstream Grafana Jaeger datasource uses the same base URL (`http://jaeger:16686/jaeger/ui`), which corroborates it. The adapter's default previously requested `/api/traces` and would have 404ed.
- **The preflight probes checked a Grafana that holds none of the module's assets.** The dashboard, LGTM datasources, and alert rules are mounted into the *upstream* Grafana; the probe set only covered LGTM's own Grafana, which has none of them.
- Confirmed correct and left unchanged: the collector merge (the extras layer replaces only `exporters` arrays, so upstream receivers and processors survive), the LGTM exporter endpoint (`http://lgtm:4318` is LGTM's OTLP/HTTP receiver), the Loki label scheme (Loki's OTLP endpoint maps `service.name` to `service_name`), and Tempo search (enabled by default; LGTM's config omits the `search` block).

### Commands/checks

```bash
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop check
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop dry-run
python3 -m adapter --list-presets
python3 tools/scenario_report.py --runs scenarios/runs
git -C /home/vinit/SRE\ Copilot/vendor/opentelemetry-demo status --short --branch
```

### Result

- `make check`: `Ran 106 tests` / `OK`; corpus current (34 documents / 176 chunks); local preflight `3 pass, 0 warn, 0 fail, 2 skip`.
- T-02 dry-run: exit 0, no request sent, no record written.
- Adapter preset listing, scenario report (`0 of 10` live runs), and vendor state (`## main...origin/main`): passed.
- Live validation remains blocked: no Docker in this worker, so the eight probes have not run against a live stack.

### Open issues

- The three Loki presets, `--tempo-service`, and the Jaeger query path are fixed but still unverified live.
- Loki timestamp units and `kafka_consumer_group_lag` remain unverified.
- Copilot-source work is out of scope for this deliverable.

### Status update

- Updated `docs/status.md` and the module delivery documentation.

## 2026-10-20 — Team documentation set

### Added

- `docs/CODE-STRUCTURE.md` — the code map. System-architecture and code-structure Mermaid diagrams, the directory tree, a file-by-file walkthrough of the adapter, the tools, the config and the data files, the test map, a "where to make changes" table, and the fourteen known traps.
- `docs/TEAM-GUIDE.md` — how to work in this repository: the first-hour checklist, the everyday commands, walkthroughs for the common tasks (add a preset, add an alert rule, add a scenario, change the ingest path, change a doc, run a fault scenario), debugging recipes (empty result sets, ingest rejections, duplicate events, `source_errors` key format), the code/test/documentation conventions, the list of things not to do, and how to get unstuck.
- `docs/TEAM-HANDOFF.md` — the full handoff for the integration and completion owner: the state of the workspace, what is built and verified, the Copilot's constraints, the evidence-shape table that decides which backends can trigger which breach, the remaining work in five phases with a "done when" column for each task, the inherited risks, the open questions needing a decision, and a first-day checklist.

### Changed

- `tools/build_corpus.py`: the three new documents are now indexed, so the Copilot can answer "how does the adapter work?" from the corpus. The corpus grew from 34 documents / 176 chunks to **37 documents / 233 chunks**.
- `docs/README.md`: the three new documents are listed in the document table.
- `docs/CODE-STRUCTURE.md`: the scenario table no longer lists flag names. The vendored `demo.flagd.json` is the single source of truth for flags, and duplicating them in a doc made the corpus retrieval test fail — `CODE-STRUCTURE` outranked the real `feature-flags` document for a `paymentFailure` query. The table now points at `scenarios/catalog.json` instead.
- `docs/HANDOFF.md`: the test count in the artifact listing was stale (102) and now reads 139.

### Commands/checks

```bash
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop check
```

### Result

- `make check`: `Ran 139 tests` / `OK`; corpus current (37 documents / 233 chunks); local preflight `3 pass, 0 warn, 0 fail, 2 skip`.

### Open issues

- The Mermaid diagrams were **not** render-validated. The sandbox classifier blocked downloading `@mermaid-js/mermaid-cli`, and no mermaid parser is installed locally. The syntax was reviewed by hand against the Mermaid flowchart grammar; a renderer has not confirmed it.

## 2026-10-20 — Copilot contract test, watch-service filter, resolved assumptions

### Changed

- `adapter/events.py`: `route` is truncated to 200 characters. The Copilot's `EventIn.route` carries `max_length=200` and *rejects* a longer route, which — because a batch is validated atomically — would take every other event in that batch down with it. The previous `_clean` truncation was a no-op for route.
- `adapter/events.py`: `exception` is truncated to 8192 characters, matching the Copilot's limit.
- `adapter/events.py`: an event with no explicit id now gets a deterministic one derived from a hash of the evidence's own fields (`_derived_id`) instead of a random `uuid4`. The Copilot deduplicates on the event id, so the random fallback broke the documented re-poll dedupe invariant for any caller that did not set an id. All four clients already set stable ids, so the production path was unaffected.
- `adapter/poller.py`: a batch is now split per service *and* per chunk. The previous grouping only made services contiguous, so one chunk could still contain two services and be rejected with `403`.
- `adapter/poller.py`: new `watch_service` option. The Copilot watches a single service (`APP_SERVICE_NAME`), so evidence for any other service can never raise a breach there; it is now dropped and counted in `source_errors` instead of being sent and refused.
- `adapter/__main__.py`: new `--watch-service` / `COPILOT_WATCH_SERVICE` flag, plus a startup warning naming every configured source that targets a service other than the watched one.
- `tests/copilot_contract.py` (new): a local stand-in for the Copilot's ingest endpoint with every rule transcribed from `Copilot/api/ingest.py` at `0ce98443db0b0b3c09c40a53849164c1f7160fe9` — auth, per-service token bucket, body size, batch size, atomic schema validation, service binding, dedupe.
- `tests/test_copilot_contract.py` (new): the adapter's *real* HTTP client against that contract, covering the per-service split, the 403 that a mixed batch would earn, the drop-and-report paths, dedupe on re-poll, field clamping, route truncation, batch chunking, and the 401 that must not be retried.
- Tests: `test_poller.py` (watched-service filter), `test_presets.py` (watch-service warning content and silence, `--configured-services` mapping).
- Docs: module README, `adapter/README.md`, `docs/FILE-INVENTORY.md`, `docs/HANDOFF.md`, `docs/RESUME-PROMPT.md`, `docs/VERIFICATION-MATRIX.md` (rows 28, 30, and new rows 37–41), `docs/status.md`, `docs/operations-runbook.md`, `docs/live-validation.md`.

### Integration findings

The two remaining "assumption" rows are now resolved against authoritative upstream source, not observed in a running deployment:

- **Loki `service_name` is correct.** Loki's `default_resource_attributes_as_index_labels` includes `service.name` as an index label by default, and its attribute-to-label normalization maps the dot to an underscore, giving `service_name`.
- **`kafka_consumer_group_lag` is correct.** The contrib `kafkametrics` receiver emits `kafka.consumer_group.lag`, and Prometheus's OTLP translator normalizes a dotted metric name by joining its tokens with `_`.

### Commands/checks

```bash
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop check
```

### Result

- `make check`: `Ran 139 tests` / `OK`; corpus current; local preflight `3 pass, 0 warn, 0 fail, 2 skip`.

### Open issues

- The contract test runs against a *transcription* of the Copilot's rules, not the real FastAPI endpoint: this worker has no pip and the auto-mode classifier blocks installing downloaded packages, so FastAPI and pydantic could not be installed. The transcription was checked line by line against the Copilot source, but a drift in the real endpoint would not be caught here.
- Live delivery to a running Copilot is still unverified: no Docker in this worker.

### Status update

- Updated `docs/status.md` and the module delivery documentation.

## 2026-10-20 — Copilot integration readiness

### Changed

- `adapter/poller.py`: events are grouped by service and sent one request per service, so a batch never names a service other than the one its key is bound to. New `parse_ingest_keys` parses `service:key,service:key` into `{service: key}`; `Poller` takes an optional `service_keys` map and picks the key from the batch's service.
- `adapter/poller.py`: a service with no key is dropped and reported in `source_errors` instead of being sent with a key bound to another service, which would spend that service's rate-limit token on a 403.
- `adapter/__main__.py`: new `--ingest-keys` / `COPILOT_INGEST_KEYS` flag. `--api-key` is now optional when it is set, and remains the fallback.
- `adapter/_common.py`: new `pick_attr` helper reads the same attribute under either spelling.
- `adapter/prometheus.py` / `adapter/jaeger.py`: status and route are read from the names the demo actually emits — `http_response_status_code` and `http_route` on Prometheus metrics, `http.response.status_code` and `http.route` on spans. A Jaeger route without a method is left alone; one with a method is prefixed `METHOD /path`.
- `adapter/events.py`: `status` is clamped to 100–599 and `duration_ms` to 0–3 600 000, and non-finite durations are dropped. The Copilot validates a batch atomically, so one out-of-range value would otherwise reject every other event in the batch.
- Tests: `test_poller.py` (per-service batching, per-service key selection, key parsing, services without a key), `test_clients.py` (semantic-convention and Prometheus label names, non-HTTP status labels), `test_events.py` (status/duration range clamping), `test_presets.py` (the `--ingest-keys` flag, `--api-key` optional).
- Docs: module README, `adapter/README.md`, `docs/FILE-INVENTORY.md`, `docs/HANDOFF.md`, `docs/RESUME-PROMPT.md`, `docs/VERIFICATION-MATRIX.md` (rows 33–36), `docs/status.md`.

### Integration findings

Checked against the Copilot repository (`github.com/satwikt/incident_response_agent`, `0ce98443db0b0b3c09c40a53849164c1f7160fe9`), read-only.

- **A mixed batch was a hard blocker.** `Copilot/api/ingest.py` binds every key to one service and rejects a batch naming any other with `403 service mismatch`, discarding the whole batch. The adapter previously sent every service in one request under one key. Now grouped per service.
- **The 403 happens after the rate limit is spent.** The token bucket is per service and is charged before the binding check, so a doomed batch burns the bucket of the service the key belongs to. Dropping unknown services avoids it.
- **The status attribute name was wrong.** The adapter looked for `http_status_code` / `http.status_code` / `status_code`; the demo emits `http_response_status_code` on Prometheus metrics and `http.response.status_code` on spans. Status was always `None`, so Jaeger evidence could never satisfy the Copilot's error-rate detection, which needs route and status. Fixed.
- **The Copilot watches one service.** `APP_SERVICE_NAME` selects a single watched service, so multi-service ingest works but multi-service detection does not. This is a Copilot-side design constraint, not a module defect, and is documented rather than worked around.
- **Only trace evidence drives detection.** Error-rate detection needs route and status; latency and slow-request detection need route and duration. Jaeger and Tempo carry all three; Prometheus presets carry route and status but no duration; Loki log lines carry neither, so they can only feed the keyword breach.
- **A route needs 20 requests in the window** before it can be judged healthy or unhealthy (`MIN_REQUESTS`).
- **Timestamps must be within 5 minutes ahead and 7 days behind.** The adapter's 300-second default window is well inside that.

### Commands/checks

```bash
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop check
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop dry-run
python3 -m adapter --list-presets
```

### Result

- `make check`: `Ran 121 tests` / `OK`; corpus current (34 documents / 176 chunks); local preflight `3 pass, 0 warn, 0 fail, 2 skip`.
- T-02 dry-run: exit 0, no request sent, no record written.
- Live delivery to a running Copilot is still unverified: no Docker in this worker.

### Open issues

- The per-service batching, per-service key routing, attribute names, and field clamping are verified against the Copilot's source and with mocks, not against a live endpoint.
- The Copilot's single watched service means the multi-service presets need `APP_SERVICE_NAME` set to the service under test; a run that expects a breach on a second service will not see one.
- Loki timestamp units and `kafka_consumer_group_lag` remain unverified.

### Status update

- Updated `docs/status.md` with the integration constraints and the module delivery documentation.

### Next entry template

```text
## YYYY-MM-DD — <chunk>
### Changed
- ...
### Commands/checks
- ...
### Result
- ...
### Open issues
- ...
### Status update
- Updated docs/status.md
```
