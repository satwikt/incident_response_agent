# Resume Prompt

Copy everything below the line into the next AI agent's first message. It is written to be self-contained: it names the workspace, the artifacts, the current verified state, the exact commands, the contracts, and the failure modes. The companion documents it references are part of the brief and must be read before editing anything.

---

You are continuing the **AI SRE Copilot × OpenTelemetry Astronomy Shop** testbed integration. This is not a greenfield project. The deliverable is only the plug-and-play test module under `integration/astronomy-shop/`: a read-only telemetry adapter, Compose/Grafana overlay, documentation corpus, fault-scenario harness, preflight checks, and local tests. The module is implemented and locally verified.

The single most important rule: **do not expand this deliverable into Copilot implementation, and do not claim live results you did not observe.** The Copilot repository must not be checked out or modified for this module. Live stack validation is an optional external step requiring Docker and runtime endpoints.

## 1. Read these files first, in this order

Do not skip this. Each file answers a different question, and together they are the handoff.

| Order | File | What it gives you |
|---|---|---|
| 1 | `docs/HANDOFF.md` | Authoritative current state, artifact list, exact next action, effort estimate |
| 2 | `docs/IMPLEMENTATION-NOTES.md` | How the system works, module contracts, invariants, unverified assumptions, failure modes |
| 3 | `docs/FILE-INVENTORY.md` | Every file, its purpose, and whether it is verified, implemented, blocked, or generated |
| 4 | `docs/VERIFICATION-MATRIX.md` | Requirement → artifact → check → status, so you know what "done" means |
| 5 | `docs/status.md` | Short implemented / not-started / blocked state |
| 6 | `docs/live-validation.md` | Command-by-command first live run with expected outputs and a results table |
| 7 | `docs/native-tools-spec.md` + `docs/native-tools.schema.json` | Future Copilot-work reference; do not implement it in this module |
| 8 | `docs/requirements.md`, `docs/architecture.md`, `docs/integration-contract.md` | Why the boundary is push-based and what the acceptance criteria are |
| 9 | `docs/decisions.md` | Accepted decisions D-01…D-18 and rejected alternatives; do not relitigate |
| 10 | `docs/test-scenarios.md`, `docs/operations-runbook.md` | Fault curriculum and operating procedure |
| 11 | Tail of `docs/change-log.md` | Exact commands and observed results so far; append, do not rewrite |
| 12 | `integration/astronomy-shop/README.md`, `scenarios/README.md`, `adapter/README.md`, `docs-corpus/README.md` | Tooling usage and safety behavior |

These companion documents are part of the brief, not optional background: the prompt states current status and intent, while they carry the exact contracts and evidence. If you were given only this text, request the files before starting. If a file is missing, stop and report it. `docs/HANDOFF.md` is the source of truth if these ever disagree.

## 2. Mission, in execution order

1. **Run the local module checks** with `make check`, the scenario dry-run, adapter help, and the report command.
2. **Keep the module portable and read-only**: changes belong under `integration/astronomy-shop/` and its delivery docs; never modify vendor source or the Copilot repository.
3. **Optionally validate the Compose stack** on a host with Docker, using the documented five-layer configuration.
4. **Optionally prove the adapter and scenario paths live** when a running Astronomy Shop stack and Copilot ingest endpoint are supplied; record actual results and keep blocked checks labeled.
5. **Stop at the test-module boundary.** Copilot corpus ingestion, native tool implementation/registration, Hindsight changes, and Copilot runtime changes are outside this deliverable.

The local module must remain usable without Docker or a Copilot checkout. Live validation is external evidence, not a reason to weaken local checks.

## 3. Ground truth: what already exists

### Implemented and locally verified

- **Adapter** (`integration/astronomy-shop/adapter/`): read-only Prometheus, Loki, Tempo, and Jaeger clients; a poller with 500-event and 1 MiB batching; bounded exponential backoff for 429/5xx/transport errors; per-source failure isolation; deterministic event IDs; accepted/duplicate/dropped accounting; a CLI with nine presets.
- **Overlay** (`compose.overlay.yaml`, `otelcol-config-extras.yml`): an `otel-lgtm` service, a collector extras mount at `/etc/otelcol-config-extras.yml`, and OTLP/HTTP export to LGTM while preserving upstream exporters.
- **Grafana**: Tempo and Loki datasources with a trace→log pivot; seven unified alert rules; an eight-panel incident dashboard mounted into the upstream demo dashboard provider.
- **Corpus** (`docs-corpus/`): 37 documents / 233 chunks, deterministic IDs, provenance to the pinned commit, `--check` staleness guard, term-overlap search for validation.
- **Scenarios** (`scenarios/`): T-01…T-10 catalog validated against the vendored flagd definitions; manual recorder; snapshot/restore runner; coverage report.
- **Tooling**: `tools/preflight.py`, a `Makefile`, and the live-validation checklist.

### Verified by the previous run

```text
python3 -m unittest discover -s tests   → 139 tests, OK (exit 0)
python3 tools/preflight.py --local-only → exit 0 (3 pass, 0 fail, 2 skip; 15 required files)
python3 tools/build_corpus.py --check   → up to date (exit 0)
python3 -m adapter --list-presets       → exit 0, nine presets
python3 tools/run_scenario.py T-02 --dry-run → exit 0, no request sent
```

### Not verified, and must not be described as working

- Compose resolution with all five layers; no `docker` existed in the previous worker.
- Grafana loading datasources, alerts, or the dashboard; no dashboard has ever rendered.
- Real backend response shapes for Prometheus, Loki, Tempo, Jaeger behind the proxies.
- Ingest into a real Copilot, dedupe against its store, or key-to-service authorization.
- Any fault ever being injected and observed end to end.
- The Loki `service_name` label and the Kafka `kafka_consumer_group_lag` metric name are resolved from the pinned upstream source, but neither has been observed in a running deployment.
- Native tool registration: the Copilot repository checkout is absent from this workspace; remote is `https://github.com/satwikt/incident_response_agent`.

## 4. Repository map and ownership

```text
<workspace root>                        # not a Git repository
├── docs/                               # design + delivery record (you own edits)
│   ├── HANDOFF.md                      # start here
│   ├── IMPLEMENTATION-NOTES.md
│   ├── FILE-INVENTORY.md
│   ├── VERIFICATION-MATRIX.md
│   ├── RESUME-PROMPT.md                # this file
│   ├── live-validation.md
│   ├── native-tools-spec.md
│   ├── native-tools.schema.json
│   ├── requirements.md
│   ├── architecture.md
│   ├── integration-contract.md
│   ├── implementation-plan.md
│   ├── decisions.md
│   ├── test-scenarios.md
│   ├── operations-runbook.md
│   ├── status.md
│   ├── change-log.md
│   └── README.md
├── integration/astronomy-shop/         # all Copilot-owned integration code
│   ├── Makefile
│   ├── compose.overlay.yaml
│   ├── otelcol-config-extras.yml
│   ├── adapter/                        # read-only polling + ingest
│   ├── grafana/                        # datasources, alerting, dashboards
│   ├── tools/                          # corpus, scenarios, preflight, reports
│   ├── tests/                          # stdlib unittest
│   ├── scenarios/                      # catalog, runs, README
│   └── docs-corpus/                    # generated JSONL + manifest
└── vendor/opentelemetry-demo/          # pinned clone, never edit as source
```

Rules of ownership:

- `vendor/opentelemetry-demo/` is an independent clone at commit `85c2cb253ae3519ef2b3a02984c32614106de5ea`. Never commit or hand-edit changes there.
- Copilot-owned code and configuration go under `integration/astronomy-shop/`.
- Design decisions and delivery records go under `docs/`.
- The workspace root is not a Git repository. Use `git -C vendor/opentelemetry-demo ...` only to inspect vendor state.

## 5. Hard constraints

- **No vendor source edits.** The flagd-ui write path rewrites `vendor/opentelemetry-demo/src/flagd/demo.flagd.json` at runtime; that is expected. Never treat that rewritten file as a source change, and never "fix" it by editing upstream code.
- **Detection stays push-based** through `POST /ingest/events`. Native tools are read-only investigation helpers. Do not move detection into tools.
- **No write, toggle, shell, SQL, or arbitrary-HTTP capability** may be added to the Copilot tool surface. There is deliberately no flag-toggle tool; scenario control is an operator action.
- **No weakened checks.** Do not delete tests, loosen assertions, skip a failing check, or mark blocked work as verified to make a report look complete.
- **Respect environment limits.** The previous worker had no Docker, pytest, PyYAML, or pip. Tests use stdlib `unittest`. Confirm any tool exists before depending on it; prefer no new dependencies.
- **Do not invent service identity.** Aggregate PromQL drops labels; presets carry explicit service overrides. Evidence conversion must never guess a `service_name`.
- **Preserve deterministic IDs.** Re-polling the same sample, log line, or trace must produce the same event ID so the ingest store dedupes. Per-poll random UUIDs are a regression.

## 6. Data and interface contracts you must preserve

### Ingest event

```http
POST /ingest/events
X-Api-Key: <key>
Content-Type: application/json
```

Body is a JSON array. Limits: **500 events** and **1 MiB serialized body** per request.

Fields per event: `id`, `ts`, `service`, `level`, `route`, `status`, `duration_ms`, `message`, `exception`.

- `id` must match `[A-Za-z0-9._:-]{1,64}`.
- `level` is one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.
- `service` and `message` are required.
- The adapter embeds `source=`, `trace_id=`, and `span_id=` inside `message` until native tools replace that convention.
- Ingestion is idempotent on `id`; duplicates are counted, not errors.

### Adapter CLI

```bash
python3 -m adapter \
  --ingest-url http://localhost:8000/ingest/events \
  --api-key "$COPILOT_API_KEY" \
  --preset checkout-error-ratio \
  --jaeger-service checkout \
  --interval 30 --window 300 --batch-size 500 --timeout 5 --attempts 3 \
  --once
```

Environment fallbacks: `COPILOT_INGEST_URL`, `COPILOT_API_KEY`, `COPILOT_INGEST_KEYS`, `PROMETHEUS_URL`, `LOKI_URL`, `TEMPO_URL`, `JAEGER_URL`.

`--ingest-keys` / `COPILOT_INGEST_KEYS` takes `service:key,service:key` pairs —
the same form the Copilot's own `INGEST_KEYS` uses, but mapped service→key
instead of key→service. The Copilot rejects a batch naming a service other
than the one its key is bound to, so the adapter sends one request per service
and uses that service's key. Without it the adapter still sends one request
per service, all with the single `--api-key`.

### Nine query presets

| Preset | Backend | Signal |
|---|---|---|
| `checkout-error-ratio` | Prometheus | checkout 5xx ratio, 5 m |
| `checkout-p95` | Prometheus | checkout HTTP p95 seconds |
| `payment-error-spans` | Prometheus | payment error spans/sec |
| `shipping-p95` | Prometheus | shipping HTTP p95 seconds |
| `recommendation-p95` | Prometheus | recommendation span p95 ms |
| `product-catalog-p95` | Prometheus | product catalog HTTP p95 seconds |
| `checkout-error-logs` | Loki | checkout lines containing `error` |
| `payment-error-logs` | Loki | payment lines containing `error` |
| `recommendation-error-logs` | Loki | recommendation lines containing `error` |

These are starting points, not tuned thresholds. Calibrate them against observed baseline traffic and record the change.

### Grafana contracts

- Datasource UIDs: `webstore-metrics` (Prometheus, upstream), `webstore-traces` (Jaeger, upstream), `lgtm-traces` (Tempo), `lgtm-logs` (Loki).
- Seven alert rules: `AstronomyShopCheckoutErrorRate`, `AstronomyShopPaymentErrorSpans`, `AstronomyShopShippingP95Latency`, `AstronomyShopRecommendationP95Latency`, `AstronomyShopProductCatalogP95Latency`, `AstronomyShopPostgresDeadlocks`, `AstronomyShopKafkaConsumerLag`.
- Eight dashboard panels: checkout 5xx ratio, payment error spans, shipping p95, recommendation p95, product-catalog p95, Postgres deadlocks, Kafka consumer lag, Loki logs.
- Kafka rule is full-mode only and its metric name is unverified. Verify before trusting it.

### Native tool envelope

Every tool returns JSON and never raises into the model:

```json
{
  "ok": true,
  "source": "prometheus",
  "window": {"start": "2026-10-20T10:00:00Z", "end": "2026-10-20T10:05:00Z"},
  "data": [],
  "truncated": false,
  "error": null
}
```

Errors: `invalid_argument`, `backend_unavailable`, `timeout`, `not_found`, `too_large`, `internal`. Limits: 5 s per call, 24 h max window, 2000-char max query, 200 rows, 256 KiB response. The seven tools are `query_prometheus`, `query_loki`, `query_tempo`, `query_jaeger`, `get_trace`, `get_flag_state`, `search_application_docs`.

## 7. Phase 0 — orientation (no Docker required)

From the workspace root:

```bash
git -C vendor/opentelemetry-demo status --short --branch
cd integration/astronomy-shop
make check
make help
make dry-run
make adapter-help
make report
```

Expected: vendor clean at the pinned commit, **139 tests OK**, corpus up to date, local preflight 3 pass / 0 fail / 2 skip (Docker and Compose are skipped by `--local-only`, which `make check` invokes through `make preflight`).

If the vendor is dirty, inspect `git -C vendor/opentelemetry-demo diff --stat` before doing anything. A dirty `src/flagd/demo.flagd.json` usually means a previous scenario did not restore; recover from the snapshot rather than reverting blindly.

If tests fail, fix the cause. Do not proceed to live validation with a red suite.

## 8. Optional Phase 1 — live stack validation

Follow `docs/live-validation.md`. The condensed path:

```bash
cd integration/astronomy-shop && make preflight     # local checks; Docker is skipped here
cd ../../vendor/opentelemetry-demo

docker compose config \
  -f compose.yaml \
  -f compose.full.yaml \
  -f compose.observability.yaml \
  -f compose.extras.yaml \
  -f ../../integration/astronomy-shop/compose.overlay.yaml
```

If `config` fails, the error is usually a path, a missing service reference, or an exporter referenced by the overlay but not defined by the chosen layers. Fix only `integration/astronomy-shop/`; never patch vendor files. Record the exact error in `docs/change-log.md`.

Start minimal mode (frontend proxy, core services, observability, overlay):

```bash
docker compose \
  -f compose.yaml \
  -f compose.observability.yaml \
  -f compose.extras.yaml \
  -f ../../integration/astronomy-shop/compose.overlay.yaml \
  up -d
```

Then confirm, with expected locations:

| Surface | URL | Notes |
|---|---|---|
| Store frontend | `http://localhost:8080/` | through the frontend proxy |
| flagd UI | `http://localhost:8080/feature` | REST at `/feature/api/read`, `/feature/api/write` |
| Jaeger UI | `http://localhost:8080/jaeger/ui` | behind the proxy |
| Upstream Grafana | `http://localhost:8080/grafana` | demo dashboards |
| LGTM Grafana | `http://127.0.0.1:3001` | provisioned Tempo/Loki datasources |
| Loki | `http://127.0.0.1:3100` | published by the overlay |
| Tempo | `http://127.0.0.1:3200` | published by the overlay |

Run the executable checks:

```bash
python3 ../../integration/astronomy-shop/tools/preflight.py --execute --probe
```

Pass criteria: required files present, vendor pinned, corpus current, `docker compose version` works, `compose config` resolves, and all eight HTTP probes pass (store, flagd read with a `flags` map, LGTM Grafana health, Jaeger UI, upstream Grafana health, Prometheus health, LGTM Tempo ready, LGTM Loki ready).

Record pass/fail per check in `docs/status.md`. A container being `Up` is not proof the collector is exporting; check the collector logs for export errors.

## 9. Optional Phase 2 — adapter smoke

```bash
cd integration/astronomy-shop
COPILOT_API_KEY=... python3 -m adapter \
  --ingest-url http://localhost:8000/ingest/events \
  --preset checkout-error-ratio \
  --jaeger-service checkout \
  --once
```

Every backend the adapter uses is host-reachable with the default URLs: Prometheus and Jaeger through the frontend proxy, and LGTM's Tempo and Loki on the loopback ports the overlay publishes.

Pass criteria for one run:

- `fetched > 0`
- `accepted > 0`
- `source_errors` empty
- `failed_batches == 0`
- The Copilot store contains the events, with a queryable `trace_id`.

Then run the same command a second time. It must report duplicates/dedupe rather than doubling the store. If it does not, the deterministic ID path has regressed.

If a source fails, isolate it in this order: URL/port reachability → HTTP status → response shape → label scheme. Record the actual error. Do not guess a fix and do not widen the adapter to "make it work".

## 10. Optional Phase 3 — baseline and first fault

```bash
python3 tools/run_scenario.py T-01 --duration 300
python3 tools/run_scenario.py T-02 --duration 300
python3 tools/scenario_report.py --format md
```

The runner snapshots the full flag document to `scenarios/runs/<stamp>-<scenario>.snapshot.json` before mutating, toggles `paymentFailure` to `100%`, holds for the window, restores in a `finally` block, and records the run.

Pass criteria:

- T-01 produces no incident; healthy RED metrics.
- T-02 produces the checkout error-rate alert and payment error-span evidence, and the Copilot incident points at payment rather than checkout.
- `paymentFailure` is back to `off`.
- The snapshot was restored; if restore failed, the snapshot path is printed for manual recovery.
- A run record exists and appears in the report.

Use `--observed`, `--copilot-result`, and `--hindsight-path` to record what you actually saw. Do not invent these.

## 11. Optional Phase 4 — curriculum and calibration

| ID | Fault | Mode | Flag / variant | Expected signature |
|---|---|---|---|---|
| T-01 | baseline | minimal | none | no incident |
| T-02 | payment failure | minimal | `paymentFailure=100%` | payment error spans, checkout 5xx |
| T-03 | repeat payment failure | minimal | `paymentFailure=100%` | same fingerprint, Hindsight recall |
| T-04 | payment unreachable | minimal | `paymentUnreachable=on` | checkout connection failures, no payment spans |
| T-05 | international shipping slowdown | minimal | `intlShippingSlowdown=10sec` | international latency only |
| T-06 | recommendation cache failure | minimal | `recommendationCacheFailure=on` | memory/CPU trend, frontend latency |
| T-07 | product catalog lock contention | minimal | `productCatalogLockContention=on` | DB deadlocks, multi-hop trace |
| T-08 | Kafka queue problems | **full** | `kafkaQueueProblems=3sec` | consumer lag, async backpressure |
| T-09 | degraded telemetry | minimal | none | metrics up, logs/traces unavailable |
| T-10 | malicious log content | minimal | none | instruction-like text treated as data |

Run T-04…T-07 in minimal mode, then switch to full mode for T-08:

```bash
docker compose -f compose.yaml -f compose.full.yaml -f compose.observability.yaml \
  -f compose.extras.yaml -f ../../integration/astronomy-shop/compose.overlay.yaml up -d
```

For T-09, stop Loki/Tempo and confirm the adapter reports partial evidence explicitly instead of silently returning empty. For T-10, inject an instruction-like log line and confirm no tool selection or policy changes; the text is data.

Calibrate the seven alerts against the baseline and fault windows. Record the old threshold, the observed value, the new threshold, and why. Do not tune until everything alerts; a quiet baseline is a requirement.

## 12. Phase 5 — outside this deliverable

The following are intentionally not part of the plug-and-play test module and must not be implemented here:

- ingesting `docs-corpus/corpus.jsonl` into the Copilot documentation index;
- implementing or registering the seven Copilot-native tools;
- changing Hindsight, detection, agent registration, or Copilot runtime behavior;
- running shadow comparisons inside the Copilot repository.

`docs/native-tools-spec.md` and `docs/native-tools.schema.json` remain reference artifacts for a separate, explicitly authorized Copilot-repository task.

## 13. Invariants that must survive your changes

- Deterministic evidence IDs for samples, log records, and traces.
- Batch before send: ≤500 events, ≤1 MiB serialized body.
- Retry policy lives in the poller only: 429/5xx/transport, bounded exponential backoff. Clients do not retry.
- Per-source isolation: one failing backend must not abort the cycle.
- Explicit service overrides on aggregate PromQL presets.
- Scenario mutation only through flagd-ui, with snapshot-before-write and restore-in-`finally`.
- Tool surface is read-only; `get_flag_state` reads and must never call the write endpoint.
- Compose overlay mounts must stay at exact container paths; nested mounts inside a directory mount are easy to break.

## 14. Failure playbook

| Symptom | Check first | Then |
|---|---|---|
| Collector container exits | `--config` file list; extras mount at `/etc/otelcol-config-extras.yml` | exporter names must match; arrays replace, not merge |
| No data in LGTM | collector `otlphttp/lgtm` exporter | `lgtm` service health, network alias |
| Adapter `source_errors` | backend URL/port | response shape, then label spelling |
| Ingest 401/403 | `X-Api-Key` | key-to-service binding |
| Ingest 413/422 | batch bytes/count | required fields, id charset |
| Duplicates every poll | deterministic ID path | timestamps or IDs being regenerated per batch |
| Flags stuck active | `scenarios/runs/*.snapshot.json` | restore manually, confirm every flag `off` |
| Grafana panel empty | datasource UID | metric name, `service_name` label |
| Alert never fires | query returns data at all | `for` duration, threshold, no-data state |
| Scenario runner exits 2 | flagd-ui base URL | `/feature` prefix, write endpoint availability |

## 15. Documentation and verification protocol

After each phase, update:

- `docs/status.md` — move items between implemented / verified / blocked; add exact commands.
- `docs/change-log.md` — append a dated entry with `### Changed`, `### Commands/checks`, `### Result`, `### Open issues`, `### Status update`. Do not rewrite earlier entries.
- `docs/HANDOFF.md` — update the next action, remaining-effort line, and any artifact list changes.
- `docs/FILE-INVENTORY.md` and `docs/VERIFICATION-MATRIX.md` — if you add a file or change a status.
- `docs/live-validation.md` — if a command or expected output changed.

Verification commands to run before declaring the test module done:

```bash
cd integration/astronomy-shop
make check
make dry-run
make adapter-help
make report
```

Run optional live checks only when their external prerequisites exist. Paste observed output, not a summary. If a check cannot run, write **blocked** and the first useful error.

## 16. Definition of done for this deliverable

- The plug-and-play module contains the overlay, adapter, corpus, scenarios, preflight checks, and local tests.
- `make check`, the scenario dry-run, adapter help, report generation, and Python compilation pass.
- Every local claim is backed by a reproducible check; live claims remain explicitly blocked or optional until observed.
- No vendor source edits, no Copilot repository changes, no unapproved write capability, and no weakened tests.
- `docs/HANDOFF.md` and this prompt describe the module boundary accurately.

## 17. Explicitly forbidden

- Editing `vendor/opentelemetry-demo` as source, or committing vendor changes.
- Adding a flag-write, remediation, shell, SQL, or arbitrary-HTTP tool.
- Replacing deterministic IDs with per-poll random UUIDs.
- Raising batch limits above 500 events or 1 MiB.
- Adding retries inside backend clients.
- Reporting unit tests as live validation, or reporting a blocked check as a pass.
- Deleting or weakening a test to make a suite green.
- Implementing Copilot-side native tools or documentation-index wiring as part of this module.

## 18. Final report format

Report, in this order:

1. What you ran, with exact commands.
2. What passed, with the observed output that proves it.
3. What failed, with the first useful error and what you tried.
4. What remains blocked, and on what.
5. The exact next command for the next agent.
6. Any doc or artifact you changed.

Keep verified, inferred, and blocked strictly separate. A clean unit-test run is not a live result.
