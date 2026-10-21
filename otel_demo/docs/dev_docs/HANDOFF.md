# AI SRE Copilot Testbed Handoff

Last updated: 2026-10-20

## Deliverable

Provide a plug-and-play **OpenTelemetry Astronomy Shop** test module for an AI SRE Copilot integration. The deliverable is limited to the isolated module under `integration/astronomy-shop/` and its delivery documentation.

It provides:

- an unmodified, pinned Astronomy Shop dependency;
- Compose/Collector/Grafana overlay assets;
- a read-only telemetry adapter compatible with `POST /ingest/events`;
- a deterministic documentation corpus and native fault-scenario harness;
- local checks, dry runs, and a reproducible Makefile workflow.

The broader Copilot application goals—documentation-index ingestion, native tool registration, Hindsight behavior, and remediation—remain separate work and are not delivered or modified here.

## Companion documents

Read these before making changes:

- `docs/RESUME-PROMPT.md` — copy-paste prompt for a fresh AI agent.
- `docs/IMPLEMENTATION-NOTES.md` — architecture, module responsibilities, invariants, failure modes.
- `docs/FILE-INVENTORY.md` — every file and its verification state.
- `docs/VERIFICATION-MATRIX.md` — requirement → artifact → check → status, plus the test-coverage map.
- `docs/live-validation.md` — first-run command checklist with expected outputs.
- `docs/native-tools-spec.md` and `docs/native-tools.schema.json` — future Copilot-work reference; outside this test-module deliverable.

## Workspace and repository state

Working directory at handoff:

```text
/home/vinit/SRE Copilot
```

The workspace root **is a Git repository** (this is where the work is delivered). The vendored Astronomy Shop tree is a snapshot of upstream, committed into this repository:

```text
vendor/opentelemetry-demo/
upstream: https://github.com/open-telemetry/opentelemetry-demo.git
commit: 85c2cb253ae3519ef2b3a02984c32614106de5ea
recorded in: vendor/opentelemetry-demo/.pinned-commit
```

The vendored snapshot was clean at handoff. Do not edit files under `vendor/opentelemetry-demo`. Copilot-owned changes live under `integration/astronomy-shop/` and `docs/`.

The Copilot project remote is now identified as `https://github.com/satwikt/incident_response_agent` (main at `0ce98443db0b0b3c09c40a53849164c1f7160fe9` when checked read-only), but its runtime checkout is not present in this workspace.

Runtime flag changes go through the flagd-ui API, which rewrites the vendored `src/flagd/demo.flagd.json`. That is the supported demo mechanism, but it can leave the vendored copy dirty because the UI re-encodes JSON. Never commit vendor changes; turn every flag to `off` after a test and restore the file with Git if needed.

## Current state at a glance

| Chunk | State | Evidence |
|---|---|---|
| 0. Docs and baseline | **Verified** | Design docs, decisions, requirements, runbook, pinned commit |
| 1. Astronomy Shop deployment | **Implemented config; live blocked** | Vendored snapshot present; Compose smoke not run |
| 2. LGTM overlay + Grafana | **Implemented; not live-validated** | LGTM service, collector extras mount, Tempo/Loki datasources, trace→log pivot, incident dashboard |
| 2b. Curriculum alerts | **Implemented; not live-validated** | Seven Grafana alert rules from vendored metric names |
| 3. Documentation corpus | **Verified locally** | 37 documents / 233 chunks, deterministic `--check` |
| 4. Copilot adapter | **Implemented; unit/HTTP-tested; live blocked** | Four clients, poller, CLI, query presets, dedupe, byte-aware batching |
| 5. Fault curriculum | **Scaffolding verified; no live runs** | T-01…T-10 catalog, recorder, runner, report |
| 6. Copilot-side native observability tools | **Outside this deliverable** | `docs/native-tools-spec.md` + `docs/native-tools.schema.json` retained as future reference; no Copilot files modified |
| 7. External chaos/hardening | **Not started** | Deliberately last |

Full history and exact results: `docs/status.md` and `docs/change-log.md`.

## Implemented artifacts

```text
docs/                                  design record, status, changelog, live checklist, native-tool spec, this handoff
integration/astronomy-shop/
  Makefile                             task runner (check, test, corpus, preflight, report, dry-run)
  compose.overlay.yaml                 LGTM service, Grafana mounts, collector extras mount
  otelcol-config-extras.yml            OTLP/HTTP exporter to lgtm:4318, preserves upstream exporters
  grafana/provisioning/datasources/lgtm.yaml          Tempo + Loki datasources, trace→log pivot
  grafana/provisioning/alerting/astronomy-shop-alerts.yaml   seven unified alert rules
  grafana/dashboards/astronomy-shop-investigation.json      incident dashboard (8 panels)
  adapter/events.py                    Evidence model, ingest schema, deterministic IDs
  adapter/client.py                    event building + POST /ingest/events sender
  adapter/_http.py, _common.py         JSON GET helper, level/service/id helpers
  adapter/prometheus.py                query / query_range + evidence mapping
  adapter/loki.py                      query_range + structured-level extraction
  adapter/tempo.py                     search / get_trace + evidence mapping
  adapter/jaeger.py                    find_traces + root-span evidence mapping
  adapter/poller.py                    batching (500 events / 1 MiB), per-service grouping and keys, retries, drop accounting
  adapter/sources.py                   trailing-window source factories
  adapter/presets.py                   dashboard-derived query presets
  adapter/__main__.py                  runnable polling loop with --preset/--list-presets
  tools/build_corpus.py                deterministic corpus builder + --check
  tools/record_scenario.py             manual run recorder
  tools/flagd_ui.py                    flagd-ui read/write client
  tools/run_scenario.py                snapshot → toggle → hold → restore → record
  tools/scenario_report.py             run coverage report (Markdown/JSON)
  tools/preflight.py                   read-only live-stack readiness checker
  docs-corpus/corpus.jsonl             233 chunks
  docs-corpus/manifest.json            37-document inventory
  scenarios/catalog.json               T-01…T-10 validated against flagd
  tests/                               139 stdlib unittest tests
```

## Verification performed

All of these passed in the current environment:

```bash
cd integration/astronomy-shop
make check                                      # 139 tests, corpus current, local preflight passes
python3 -m py_compile adapter/*.py tools/*.py tests/*.py
python3 -m unittest discover -s tests          # 139 tests, OK
python3 tools/build_corpus.py --check          # corpus is up to date
python3 tools/run_scenario.py T-02 --dry-run   # exit 0, no request sent
python3 tools/scenario_report.py --runs scenarios/runs
python3 -m adapter --help                      # exit 0
python3 -m adapter --list-presets              # exit 0
python3 tools/preflight.py --local-only        # exit 0; default run fails only on missing Docker
make help                                      # task runner loads
make -n test                                   # prints the unittest command
```

Test coverage includes:

- ingest schema, deterministic IDs, required-field and level validation;
- Prometheus vector/matrix parsing, Loki streams, Tempo search, Jaeger root-span mapping;
- poller retry classification, backoff, byte-aware chunking, source isolation, receipts;
- real localhost HTTP ingest with `X-Api-Key` and JSON array body;
- overlay mounts, exporter-name consistency, Grafana datasource UIDs, alert contracts;
- corpus metadata, coverage, determinism, retrieval, pinned-commit drift;
- scenario flag/variant validation, recorder fields, runner restore semantics, report output.

## Not verified / blocked in this environment

- `docker compose config` and any live Compose startup: **Docker is not installed** (`docker: command not found`).
- Grafana has not loaded the datasources or alert rules live.
- Prometheus/Loki/Tempo/Jaeger response shapes have only been tested with mocks and local HTTP fixtures; Loki timestamp units remain unverified. The Jaeger API prefix, the Loki `service_name` label, and the Kafka `kafka_consumer_group_lag` metric name are resolved from the pinned upstream source.
- No metric/log/trace has been ingested into a running Copilot. The ingest path is verified against a transcription of the Copilot's contract (`tests/copilot_contract.py`), not the live FastAPI endpoint.
- No fault scenario has been executed against a live stack.
- The generated corpus is not ingested into a Copilot documentation index; that integration is outside this test-module deliverable.
- Alert thresholds are uncalibrated against baseline traffic.
- Native tools are retained as a reference specification only; Copilot implementation and registration are outside this test-module deliverable.

## Environment constraints and gotchas

- **No Docker, pytest, PyYAML, or pip** in this worker. Tests use stdlib `unittest`; do not reintroduce a pytest dependency.
- The root workspace is **not** a Git repository. Check `git -C vendor/opentelemetry-demo status --short --branch` before relying on vendor state.
- The overlay **requires** `compose.observability.yaml`; its extras pipeline reuses `otlp_grpc/jaeger` and `otlp_http/prometheus` from that layer.
- Compose merges volumes by container target. The overlay replaces the upstream extras mount at `/etc/otelcol-config-extras.yml`; without that mount the LGTM exporter is never loaded.
- LGTM is for development/demo only. Its Grafana is mapped to `127.0.0.1:3001`; the upstream Grafana remains separate.
- The upstream stack ships **no Tempo and no Loki**; LGTM is the only source for both. The overlay publishes them on `127.0.0.1:3200` and `127.0.0.1:3100` so the adapter's default URLs resolve.
- The upstream stack runs **Jaeger v2** with `base_path: /jaeger/ui`, which registers the REST API under that prefix. The query API is `/jaeger/ui/api/traces`, reachable through the frontend proxy at `http://localhost:8080/jaeger/ui/api/traces`; the adapter's default `--jaeger-url` already carries that base path.
- The adapter's deterministic IDs are required for dedupe; do not revert to random UUIDs per poll.
- Ingest caps: array of ≤500 events, body ≤1 MiB, `X-Api-Key`, event `id` 1–64 chars of `A–Z a–z 0–9 . _ : -`. Duplicate IDs are ignored.
- flagd-ui `POST /feature/api/write` replaces the whole flag document. The runner snapshots first and restores in `finally`; do not run concurrent writers.
- The runner changes `defaultVariant` only; targeted flags are not handled (none in the current curriculum).
- T-09 (stop Loki/Tempo) and T-10 (inject malicious log) still require manual operator action during the recorded window.
- Corpus `--check` is part of the test suite. Regenerate after changing any indexed doc.
- The retrieval helper in `tools/build_corpus.py` is term-overlap validation, not production RAG.
- Adapter presets are starting points derived from the dashboards; they are not tuned thresholds, and aggregate queries pass an explicit service override so evidence conversion never guesses a service.

## Optional live-validation next action

If live validation is requested on a host with Docker, first confirm the workspace is internally consistent:

```bash
cd integration/astronomy-shop
python3 tools/preflight.py --local-only
```

Expected: 3 pass, 0 fail, 2 skip. Then run Compose validation:

```bash
cd ../../vendor/opentelemetry-demo
docker compose config \
  -f compose.yaml \
  -f compose.full.yaml \
  -f compose.observability.yaml \
  -f compose.extras.yaml \
  -f ../../integration/astronomy-shop/compose.overlay.yaml
```

If that passes, start minimal mode plus observability and the overlay:

```bash
docker compose -f compose.yaml -f compose.observability.yaml -f compose.extras.yaml \
  -f ../../integration/astronomy-shop/compose.overlay.yaml up -d
```

Then:

1. Confirm the web store, load generator, Collector, upstream Grafana, Prometheus, Jaeger, LGTM Grafana (`127.0.0.1:3001`), the Tempo/Loki datasources, and the `Astronomy Shop Incident Investigation` dashboard:

```bash
python3 ../../integration/astronomy-shop/tools/preflight.py --execute --probe
```
2. Run the adapter once against the stack with explicit queries (there are no invented defaults):

```bash
cd ../../integration/astronomy-shop
COPILOT_API_KEY=... python3 -m adapter \
  --ingest-url http://localhost:8000/ingest/events \
  --preset checkout-error-ratio \
  --jaeger-service checkout \
  --once
```

The Copilot binds each ingest key to one service and rejects a batch naming
another (`403 service mismatch`). When the endpoint is configured with
`INGEST_KEYS=service:key,...`, pass the same pairs to the adapter so each
service is sent with its own key:

```bash
COPILOT_INGEST_KEYS=checkout:key-for-checkout,payment:key-for-payment \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --jaeger-service checkout --once
```

3. Confirm one metric and one trace reach ingest, and that a second `--once` run dedupes instead of duplicating. The overlay publishes LGTM's Tempo and Loki on loopback, so `--preset checkout-error-logs` and `--tempo-service` work with the default URLs.
4. Record T-01 baseline, then T-02:

```bash
python3 tools/run_scenario.py T-01 --duration 300
python3 tools/run_scenario.py T-02 --duration 300
python3 tools/scenario_report.py --format md
```

5. Calibrate the Grafana alert thresholds against the observed baseline and fault windows.

Use `docs/live-validation.md` for the full command-by-command checklist, expected outputs, and the results table.

6. Do not check out or modify the Copilot repository for this deliverable. Copilot-side native tools and documentation-index wiring are explicitly out of scope.

If the overlay command fails, fix only `integration/astronomy-shop/` config paths and record the result in `docs/status.md`.

## Remaining work estimate

The remaining items below are optional external validation or future Copilot work; they are not required to deliver this test module.

| Work | Estimate |
|---|---|
| Compose validation, minimal stack, LGTM/Grafana live checks | 0.5–1 day |
| Backend API compatibility + adapter live smoke + dedupe check | 0.5–1 day |
| Faults T-01…T-07, evidence capture, alert calibration | 1–2 days |
| Full-mode Kafka T-08 | 0.5–1 day |
| Repeat/changed-context evaluation + Hindsight recall | 1–2 days |
| Corpus ingestion into the Copilot docs index | Outside this deliverable |
| Native tools (Prometheus/Loki/Tempo/Jaeger/flagd/docs) | Outside this deliverable |
| Dashboard live check, exemplars, trace→log polish | 0.5 day |
| Kubernetes/chaos hardening (Chunk 7, optional/later) | 1–2 weeks |

The local plug-and-play module is complete. Optional live validation remains dependent on Docker and a running Copilot endpoint; Copilot-side implementation is outside scope. External chaos/hardening remains optional and later.

## Living documentation rule

After every chunk:

1. Update `docs/status.md` with implemented / verified / blocked state.
2. Append a dated entry to `docs/change-log.md`.
3. Record exact commands and actual results.
4. Keep verified, planned, inferred, and blocked states distinct.
5. Never claim a live result that was not observed.

## Resume checklist for the next agent

1. Read `docs/HANDOFF.md`, `docs/RESUME-PROMPT.md`, `docs/IMPLEMENTATION-NOTES.md`, `docs/FILE-INVENTORY.md`, `docs/VERIFICATION-MATRIX.md`, `docs/live-validation.md`, `docs/status.md`, and the tail of `docs/change-log.md`.
2. Treat `docs/native-tools-spec.md` and `docs/native-tools.schema.json` as future-work reference only; do not write Copilot code for this deliverable.
3. Inspect `git -C vendor/opentelemetry-demo status --short --branch`; restore unexpected vendor edits before proceeding.
4. From `integration/astronomy-shop`, run `make check`, `make dry-run`, `make adapter-help`, and `make report`.
5. Check whether Docker is available only when optional live validation is requested; otherwise do not start live work.
6. If optional live validation is performed, execute the documented path above and update the living docs with observed results.
