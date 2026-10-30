# Team Handoff — Integration and Completion Owner

**Date:** 2026-10-20
**For:** the person who owns integrating this test module with a running Copilot and completing the remaining project work.
**Status:** the plug-and-play module is complete and locally verified. Everything that remains needs a Docker host and/or a running Copilot.

This is the single document to start from. It is self-contained: it states what exists, what does not, what the Copilot requires, what is left to do, and how to know each item is done. Where a companion document has the depth, it is named and linked rather than repeated in full.

---

## 1. The one-paragraph summary

We built a plug-and-play **OpenTelemetry Astronomy Shop** test module: an unmodified pinned demo, a Compose overlay that adds Tempo and Loki (LGTM) plus Grafana datasources, alerts and an incident dashboard, a read-only telemetry **adapter** that normalizes Prometheus/Loki/Tempo/Jaeger output into the Copilot's `POST /ingest/events` contract, a deterministic **documentation corpus**, a **fault-scenario harness** (T-01…T-10) driven by native flagd flags, and 139 stdlib tests plus a `make check` workflow. It was verified against the Copilot's real source code and against a local transcription of its ingest contract. It has **never been run against a live stack** — Docker was not available in the worker that built it. That live validation, plus the alert calibration and the Copilot-side work, is what you are picking up.

---

## 2. Read these, in this order

| # | Document | Why you need it |
|---|---|---|
| 1 | **[CODE-STRUCTURE.md](CODE-STRUCTURE.md)** | The map: architecture diagram, module layout, every file, where the traps are. |
| 2 | **[TEAM-GUIDE.md](TEAM-GUIDE.md)** | How to work here: commands, conventions, debugging recipes, what not to do. |
| 3 | [IMPLEMENTATION-NOTES.md](IMPLEMENTATION-NOTES.md) | The invariants. Read before changing the adapter. |
| 4 | [VERIFICATION-MATRIX.md](VERIFICATION-MATRIX.md) | Requirement → artifact → check → status. Your acceptance criteria live here. |
| 5 | [status.md](status.md) | Current state in one page, and what is explicitly *not* verified. |
| 6 | [live-validation.md](live-validation.md) | The command-by-command first live run, with expected outputs and a results table. |
| 7 | [HANDOFF.md](HANDOFF.md) | The shorter resume brief for any agent picking this up. |
| 8 | [FILE-INVENTORY.md](FILE-INVENTORY.md) | File-by-file purpose and verification state. |
| 9 | [change-log.md](change-log.md) | The dated history. The tail explains the last two rounds. |
| 10 | [operations-runbook.md](operations-runbook.md) | Start/stop/inject/observe/recover procedures. |

Reference material: [architecture.md](architecture.md), [requirements.md](requirements.md), [decisions.md](decisions.md) (D-01…D-18), [integration-contract.md](integration-contract.md), [test-scenarios.md](test-scenarios.md), [native-tools-spec.md](native-tools-spec.md) (future Copilot work, reference only).

---

## 3. The workspace

```text
/home/vinit/SRE Copilot/
├── docs/                          # the design + delivery record — you own edits here
├── integration/astronomy-shop/    # the deliverable
└── vendor/opentelemetry-demo/     # upstream demo — NEVER modify
```

### Repository state

- The **workspace root is not a Git repository.** Only `vendor/opentelemetry-demo` is a checkout (an independent shallow clone, `main`, pinned at `85c2cb253ae3519ef2b3a02984c32614106de5ea`).
- The vendor checkout was **clean** at handoff. `make check` fails if it drifts.
- The Copilot project is `github.com/satwikt/incident_response_agent`. It was read read-only at commit `0ce98443db0b0b3c09c40a53849164c1f7160fe9`. **There is no Copilot checkout in this workspace.**
- flagd-ui rewrites the vendored `src/flagd/demo.flagd.json`, which leaves the vendor checkout dirty (it re-encodes the JSON). That is expected after a scenario run. Never commit it; restore with Git.

### Environment constraints of the worker that built this

| Constraint | Consequence |
|---|---|
| No `docker` binary | Nothing was ever started. No Compose validation, no live stack, no Grafana render. |
| No `pip`/`ensurepip`; the sandbox classifier blocks downloaded packages | The real Copilot endpoint (FastAPI + pydantic) could not be run. The module is stdlib-only by necessity. |
| Not root | No system package installs. |

**Your environment probably has Docker.** That changes what is possible — §8 is the work that unblocks.

---

## 4. What is built and verified

`make check` from `integration/astronomy-shop`:

```
Ran 139 tests in 6.3s
OK
corpus is up to date
3 pass, 0 warn, 0 fail, 2 skip
```

The two skips are Docker and Compose, skipped by `--local-only`.

### 4.1 The module

| Area | What exists | Verification |
|---|---|---|
| **Upstream** | Pinned, unmodified Astronomy Shop checkout | `check_vendor` in preflight; `test_corpus.py` pin assertion |
| **Compose overlay** | `compose.overlay.yaml`: the `lgtm` service, Grafana provisioning mounts, the collector-extras mount | `test_config.py` |
| **Collector layer** | `otelcol-config-extras.yml`: the `otlphttp/lgtm` exporter on all three pipelines, preserving upstream exporters | `test_config.py` |
| **Grafana** | 2 datasources (`lgtm-traces`, `lgtm-logs`) with a trace→log pivot; 7 alert rules; 1 dashboard with 8 panels | `test_config.py`, `test_dashboard.py` |
| **Adapter** | 4 read-only clients, the `Evidence` model, the poller, 9 presets, the CLI | 139 tests, incl. `test_copilot_contract.py` |
| **Corpus** | `docs-corpus/corpus.jsonl` — 37 documents / 233 chunks, with a pinned-commit manifest | `test_corpus.py`; `make corpus-check` |
| **Scenarios** | T-01…T-10 catalog validated against the vendored flags; runner with snapshot/restore; recorder; report | `test_scenarios.py`, `test_run_scenario.py`, `test_scenario_report.py` |
| **Checks** | `make check` / `test` / `corpus` / `corpus-check` / `preflight` / `live-preflight` / `report` / `dry-run` | `test_preflight.py` |

### 4.2 What the adapter actually guarantees

These were established by reading the Copilot's source and are locked by `tests/copilot_contract.py` + `tests/test_copilot_contract.py`:

1. **One request per service.** The Copilot binds each ingest key to exactly one service and rejects a batch naming another with `403 service mismatch`, discarding the whole batch.
2. **Clamped fields.** `status` 100–599, `duration_ms` 0–3 600 000 and finite, `route` ≤ 200 chars, `exception` ≤ 8192 chars. The Copilot validates a batch **atomically** — one invalid event loses all of them.
3. **Stable ids.** Evidence without an explicit id gets a deterministic one derived from its own fields, so re-polls dedupe instead of accumulating.
4. **Bounded batching.** ≤ 500 events and ≤ 1 MiB per request, split on both.
5. **Bounded retries.** 429/5xx/transport retry with exponential backoff; 401 does not.
6. **Source isolation.** One failing backend never aborts the cycle.
7. **Watched-service filter.** `--watch-service`/`COPILOT_WATCH_SERVICE` drops evidence for any service the Copilot does not watch, and warns at startup about any source aimed elsewhere.

---

## 5. The Copilot's constraints (the ones that shape everything)

Read `Copilot/api/ingest.py` and `Copilot/agent/config.py` in the Copilot repo before touching the adapter. Summary:

| Constraint | Detail | How the module handles it |
|---|---|---|
| Key → one service | `INGEST_KEYS=service:key,...`; a foreign service in the batch → `403` | One request per service, each with its own key |
| Rate limit charged first | Token bucket per service, 20/s burst 40 — spent even on a doomed `403` | Services with no key are dropped, not sent |
| Atomic batch validation | One bad event → `422` for the whole batch | Clamp every field before sending |
| `route` is rejected, not truncated | `max_length=200`; `message`/`exception` *are* truncated by `_clean` | Adapter truncates route to 200 |
| Event id charset | `^[A-Za-z0-9._:-]{1,64}$`; the store dedupes on it | Stable ids from the clients, derived fallback |
| Timestamp window | ≤ 5 min future, ≤ 7 days past | The 300 s default window is well inside it |
| One watched service | `APP_SERVICE_NAME` → `config.SERVICE_NAME` | `--watch-service` filter + startup warning |
| `MIN_REQUESTS=20` | A route with fewer than 20 requests in the window is "not enough data" | Nothing to do — but it means breaches need traffic |
| Detection needs fields | error-rate needs route+status; latency/slow needs route+duration | See §6 |

---

## 6. Evidence shape — what can actually trigger a breach

| Backend | route | status | duration | Can drive |
|---|---|---|---|---|
| Jaeger / Tempo (root span) | ✅ | ✅ | ✅ | error-rate, latency, slow |
| Prometheus (preset queries) | ✅ | ✅ | ❌ | error-rate, keyword |
| Loki (log lines) | ❌ | ❌ | ❌ | keyword only |

**This is the single most important fact for planning your validation runs.** If you want a latency breach, the evidence must come from Jaeger or Tempo, not from Prometheus. If you want an error-rate breach, you need route *and* status — Prometheus and traces both work.

---

## 7. The four traps (all of these have already cost time)

1. **The Collector replaces arrays when merging config files.** An extras layer must repeat the upstream exporter names or it silently removes them. `otelcol-config-extras.yml` lists all of `debug`, `otlp_grpc/jaeger`, `span_metrics`, `otlp_http/prometheus`, `opensearch`.
2. **The overlay must mount the extras at `/etc/otelcol-config-extras.yml`.** Compose merges volumes by container target, so the mount *replaces* the upstream one. Without it, the LGTM exporter is never loaded and Tempo/Loki stay empty — with no error anywhere.
3. **Jaeger v2 serves its API under `base_path: /jaeger/ui`.** The query API is `/jaeger/ui/api/traces`. The adapter's default `--jaeger-url` is `http://localhost:8080/jaeger/ui` with prefix `/api`.
4. **Loki and Tempo take nanoseconds; Jaeger takes microseconds.** A wrong unit returns an empty result set with no error at all.

More in [CODE-STRUCTURE.md](CODE-STRUCTURE.md) §9.

---

## 8. The remaining work

This is your list. Nothing below has been done.

### Phase A — Bring the stack up (needs Docker)

| # | Task | Done when |
|---|---|---|
| A1 | Validate the Compose layers resolve | `docker compose config` with the five layers exits 0 |
| A2 | Start minimal + observability + overlay | All containers healthy; `make live-preflight` reports the probes passing |
| A3 | Confirm telemetry reaches Prometheus | `curl 'http://localhost:9090/api/v1/query?query=up'` returns the demo's `up` series |
| A4 | Confirm Tempo and Loki have data | A trace search on `checkout` returns results; `{service_name="checkout"}` returns log lines |
| A5 | Confirm Grafana loads the datasources, the 7 rules and the dashboard | The dashboard renders with populated panels; rules appear in Alerting |
| A6 | Confirm the trace→log pivot works | Selecting a span in the dashboard shows the correlated logs |

**Watch for:** the upstream stack ships **no Tempo and no Loki** — LGTM is the only source for both. And `compose.observability.yaml` is **required**, because the extras pipeline reuses its exporters.

Command and expected output for each: `docs/live-validation.md`.

### Phase B — Deliver to a real Copilot (needs a Copilot endpoint + key)

| # | Task | Done when |
|---|---|---|
| B1 | Obtain/stand up a Copilot and note its `INGEST_KEYS` and `APP_SERVICE_NAME` | You know the key for the service under test |
| B2 | First adapter run | One metric and one trace are accepted (`accepted > 0` in the receipt) |
| B3 | **Dedupe check** | A second identical `--once` run reports `duplicates > 0` and `accepted == 0` |
| B4 | **Service-binding check** | A batch naming a second service is either refused with `403` or dropped by the adapter — you know which, and it is the expected one |
| B5 | **Watched-service check** | With `--watch-service` set, only that service's evidence is sent; the startup warning names the mis-targeted sources |
| B6 | Confirm the Copilot's detection fires | With `MIN_REQUESTS=20` met, a fault produces an incident in the Copilot |

**The risk to know about:** the contract test proves the adapter is consistent with a *transcription* of the Copilot's rules, not with the running service. B2 is where any transcription drift surfaces. Watch for a `422` on a batch the adapter believed was clean — that is a transcription bug, not a stack bug.

### Phase C — Run the fault curriculum and calibrate

| # | Task | Done when |
|---|---|---|
| C1 | Record T-01 baseline | `scenarios/runs/` has a T-01 record; the report shows it |
| C2 | Run T-02 … T-07 (minimal mode) | Each has a record with the expected evidence and the expected cause |
| C3 | Run T-08 (Kafka, **full mode**) | Record exists; `kafka_consumer_group_lag` is non-zero and the alert fires |
| C4 | T-09 and T-10 | These need manual operator action inside the recorded window (stop Loki/Tempo; inject a malicious log) |
| C5 | **Calibrate the alert thresholds** against the observed baseline and fault windows | No false positives at baseline; every curriculum fault fires its rule |
| C6 | T-03 repeat + T-04 changed-context | The Copilot's diagnosis differs appropriately; Hindsight recall is evaluated |

T-08 needs `compose.full.yaml` — the Kafka stack is expensive and unnecessary for the other scenarios (decision D-10).

### Phase D — Copilot-side work (explicitly outside this deliverable)

| # | Task | Note |
|---|---|---|
| D1 | Ingest `docs-corpus/corpus.jsonl` into the Copilot's documentation index | The corpus is built and deterministic; only the wiring is missing |
| D2 | Implement and register the native read-only tools | `docs/native-tools-spec.md` + `docs/native-tools.schema.json` are the contract; 7 tools, read-only, separate from ingest (D-16) |
| D3 | Verify Hindsight recall behaviour | Needed for T-03/T-04 |

Decisions D-07, D-16 and D-18 deliberately deferred this work until after adapter validation. Do D1–D3 only after Phase B passes.

### Phase E — Optional

| # | Task | Note |
|---|---|---|
| E1 | Dashboard polish, exemplars, trace→log refinement | ~0.5 day |
| E2 | Kubernetes / chaos hardening (original Chunk 7) | Deliberately last; 1–2 weeks |

### Estimates

| Phase | Estimate |
|---|---|
| A — stack up and verified | 0.5–1 day |
| B — real Copilot delivery | 0.5–1 day |
| C — curriculum + calibration | 1–2 days (T-08 adds 0.5–1) |
| D — Copilot-side | depends on the Copilot's own roadmap |
| E — optional | 0.5 day / 1–2 weeks |

---

## 9. How to run the adapter

```bash
cd integration/astronomy-shop

# single key, one service
COPILOT_API_KEY=... \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --jaeger-service checkout --once

# per-service keys — one request per service, each with its own key
COPILOT_INGEST_KEYS=checkout:key-for-checkout,payment:key-for-payment \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --jaeger-service checkout --once

# scoped to the service the Copilot watches
COPILOT_WATCH_SERVICE=checkout \
COPILOT_INGEST_KEYS=checkout:key-for-checkout \
COPILOT_INGEST_URL=http://localhost:8000/ingest/events \
python3 -m adapter --preset checkout-error-ratio --jaeger-service checkout --once
```

`--watch-service` is the one to use for a first live run: it drops everything the Copilot cannot use and tells you at startup which sources you mis-targeted.

The nine presets: `python3 -m adapter --list-presets`.

---

## 10. How to run the scenarios

```bash
cd integration/astronomy-shop

make dry-run                                  # always first: prints the plan, sends nothing
python3 tools/run_scenario.py T-02 --duration 300
python3 tools/scenario_report.py --format md
```

The runner snapshots the whole flag state, flips one `defaultVariant`, holds for the duration, and restores in a `finally` block — so an interrupted run still recovers. T-09 and T-10 need manual operator action inside the window.

---

## 11. The rules for the rest of the work

### The invariants — never break these

1. **Never modify `vendor/`.** The pin is enforced by `make check`.
2. **Never edit the vendored `demo.flagd.json` directly.** Use `flagd_ui.py`; the snapshot/restore is the only supported path.
3. **Never mix services in one ingest batch.**
4. **Never send an unclamped field.**
5. **Never revert deterministic event ids to random UUIDs.**
6. **Never invent a default query.** A backend is used only if asked for explicitly.
7. **Never add a dependency.** The module is stdlib-only.
8. **Never hand-edit `docs-corpus/*`.** Rebuild it.
9. **Never edit a doc and skip `make corpus`.** `make check` will fail on a stale corpus — that is the intended guard, not a real breakage.

### The documentation rule

- **verified** — a reproducible check passed, and the command and output are recorded.
- **implemented** — the artifact exists; live proof pending.
- **blocked** — a specific dependency prevents progress; name it.
- **assumption** — used by the design but not observed.

Never upgrade a claim from *implemented* to *verified* on the basis of a unit test alone. Never report a live result that was not observed.

### After every chunk

1. `docs/status.md` — what is done, what is not, what is blocked.
2. `docs/change-log.md` — a dated entry with the exact commands and their actual output.
3. `docs/VERIFICATION-MATRIX.md` — update the row that changed status.
4. `docs/FILE-INVENTORY.md` — update the state of any file you touched.
5. `docs/live-validation.md` — fill in the results table as you go.

---

## 12. Risks and open questions you inherit

| Risk | Severity | What to do |
|---|---|---|
| The contract test runs against a **transcription**, not the real Copilot | **High** | Phase B is where drift surfaces. If you see a `422` on a batch the adapter thought was clean, suspect the transcription — compare against `Copilot/api/ingest.py` at the Copilot's current commit. |
| The Copilot's commit may have moved since `0ce9844` | **High** | Re-read `Copilot/api/ingest.py` and `Copilot/agent/config.py` before Phase B. The rules could have changed. |
| Nothing has ever run live | **High** | Assume the first live run finds problems. That is the normal state of a module built without Docker. |
| Alert thresholds are uncalibrated | Medium | Phase C5. Expect false positives at baseline until calibrated. |
| Loki/Kafka names are source-confirmed but unobserved | Low | Confirm both in Phase A/C. Both are expected to be correct. |
| The stack is heavy | Low | Minimal mode first (D-02). Full mode only for T-08. |
| flagd-ui leaves the vendor checkout dirty | Low | Expected. Restore with Git; never commit it. |
| The `docs-corpus` retrieval helper is term-overlap, not RAG | Low | D1 is a real integration task, not a config flip. |

### Open questions that need a decision

- Which service does the curriculum target? That fixes `APP_SERVICE_NAME` and the adapter's `--watch-service`. The presets span five services (checkout, payment, shipping, recommendation, product-catalog).
- Does the Copilot's `INGEST_KEYS` hold one key or one key per service? The module supports both; per-service is the recommended shape.
- Is Phase D in scope for you, or owned by the Copilot team?

---

## 13. A checklist for your first day

- [ ] Read [CODE-STRUCTURE.md](CODE-STRUCTURE.md) and [TEAM-GUIDE.md](TEAM-GUIDE.md).
- [ ] Read `docs/IMPLEMENTATION-NOTES.md` §4 (ingest contract) and §7 (unverified assumptions).
- [ ] `cd integration/astronomy-shop && make check` — 139 tests, corpus current, preflight 3 pass / 2 skip.
- [ ] `git -C vendor/opentelemetry-demo status --short --branch` — clean, at `85c2cb253ae3`.
- [ ] `make dry-run` — prints the T-02 plan, sends nothing, writes no record.
- [ ] `python3 -m adapter --list-presets` — nine presets.
- [ ] Confirm Docker is available: `docker --version`.
- [ ] Start Phase A1: `docker compose config` with the five layers.

---

## 14. The one-paragraph answer to "is it done?"

The module is done and locally verified: 139 tests, a deterministic corpus, a working Makefile, an adapter whose ingest behaviour is proven against the Copilot's actual source and a local transcription of its contract, and a complete fault curriculum with tooling. What is not done is anything that requires a running system — the Compose stack has never been started, Grafana has never rendered, no event has ever reached a real Copilot, no fault has ever been injected, and the alert thresholds are placeholders. That is roughly four to six days of work on a Docker host, and it is the natural next chunk.
