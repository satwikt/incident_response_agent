# Verification Matrix

Companion to `docs/FILE-INVENTORY.md`. This maps each requirement to the artifact that satisfies it, the check that proves it, and the current status. Use it when deciding whether a claim is safe to make.

Status vocabulary: **verified** (a reproducible check passed), **implemented** (artifact exists; live proof pending), **blocked** (a dependency prevents progress), **assumption** (used by the design but not observed).

## How to re-verify locally

```bash
cd integration/astronomy-shop
make check           # test + corpus-check + local preflight
make test            # 139 tests, stdlib unittest
make corpus-check    # deterministic corpus, exit 0 when current
make preflight       # local checks; Docker/Compose skipped on --local-only
```

`make test` runs `python3 -m unittest discover -s tests`. A passing suite proves parsing, contracts, and static configuration — **not** that a live stack works.

## Requirements → evidence

| # | Requirement | Artifact | Check | Status |
|---|---|---|---|---|
| 1 | Use a real, maintained microservice demo | `vendor/opentelemetry-demo/` pinned at `85c2cb253ae3519ef2b3a02984c32614106de5ea` | `check_vendor` in preflight; `test_corpus.py` pin assertion | verified (pin); live stack blocked |
| 2 | Keep upstream unmodified; isolate integration changes | `compose.overlay.yaml`, `integration/astronomy-shop/**` | preflight vendor cleanliness; static config tests | verified (clean locally) |
| 3 | Collect traces, metrics, logs from the demo | upstream collectors + `otelcol-config-extras.yml` | `tests/test_config.py` exporter/mount assertions | implemented; live blocked |
| 4 | Add an LGTM backend without replacing upstream | `compose.overlay.yaml` `lgtm` service | `tests/test_config.py` service/mount assertions | implemented; live blocked |
| 5 | Grafana can query Tempo and Loki | `grafana/provisioning/datasources/lgtm.yaml` | `tests/test_config.py` UID/pivot assertions | implemented; live blocked |
| 6 | Alert on curriculum signatures | `grafana/provisioning/alerting/astronomy-shop-alerts.yaml`, 7 rules | `tests/test_config.py` rule count, UIDs, metric names, labels | implemented; thresholds uncalibrated |
| 7 | Incident dashboard for the curriculum | `grafana/dashboards/astronomy-shop-investigation.json`, 8 panels | `tests/test_dashboard.py` structure, grid, UIDs, metrics, mount | implemented; never rendered |
| 8 | Read-only Prometheus/Loki/Tempo/Jaeger clients | `adapter/{prometheus,loki,tempo,jaeger}.py` | `tests/test_clients.py` with fixtures | verified with mocks |
| 9 | Normalize evidence into the ingest schema | `adapter/events.py` | `tests/test_events.py` schema, validation, IDs | verified |
| 10 | Deterministic event IDs so re-polls dedupe | `adapter/_common.py`, clients, `events.py` | `tests/test_events.py`, `test_clients.py` | verified locally; live dedupe blocked |
| 11 | Respect ingest limits (500 events, 1 MiB) | `adapter/poller.py` `chunk_events` | `tests/test_poller.py` size- and count-based chunking | verified |
| 12 | Bounded retries, no infinite loops | `adapter/poller.py` `RetryPolicy` | `tests/test_poller.py` 429/5xx/transport classification | verified |
| 13 | One failing source must not abort the cycle | `adapter/poller.py` per-source isolation | `tests/test_poller.py` | verified |
| 14 | Post events with API key | `adapter/client.py` | `tests/test_ingest_http.py` real localhost server | verified locally; real Copilot blocked |
| 15 | Runnable loop with no invented defaults | `adapter/__main__.py` | `tests/test_presets.py`, CLI smoke | verified |
| 16 | Dashboard-derived query presets | `adapter/presets.py`, 9 presets | `tests/test_presets.py` integrity + explicit service | verified as queries; results unverified |
| 17 | Documentation corpus with provenance | `docs-corpus/corpus.jsonl` (37 docs / 233 chunks), `manifest.json` | `tools/build_corpus.py --check`; `tests/test_corpus.py` | verified deterministic |
| 18 | Corpus into the Copilot index | — | retrieval of `paymentFailure`, `productCatalogLockContention` | outside this test-module deliverable; future Copilot work |
| 19 | Fault catalog valid against pinned flags | `scenarios/catalog.json` T-01…T-10 | `tests/test_scenarios.py` flags/variants/modes | verified |
| 20 | Safe fault injection with recovery | `tools/run_scenario.py`, `tools/flagd_ui.py` | `tests/test_run_scenario.py` snapshot/restore/interrupt | verified with mocks; live blocked |
| 21 | Record and report runs | `tools/record_scenario.py`, `tools/scenario_report.py` | `tests/test_scenarios.py`, `test_scenario_report.py` | verified |
| 22 | Readiness checks before live work | `tools/preflight.py` (15 required files) | `tests/test_preflight.py`; `--local-only` exit 0 | verified local; Docker/Compose blocked |
| 23 | Native tool contract, read-only | `docs/native-tools-spec.md`, `docs/native-tools.schema.json` | `tests/test_native_tools.py` names, params, limits, error codes | verified as a contract |
| 24 | Native tools implemented and registered | — | — | outside this test-module deliverable; future Copilot work |
| 25 | Compose stack resolves and starts | 5 Compose layers | `preflight --execute`; `docker compose config` | blocked: no Docker |
| 26 | End-to-end detection and incident | adapter + Copilot | T-01/T-02 live run + ingest query | blocked |
| 27 | Curriculum executed and calibrated | `tools/run_scenario.py`, alerts | T-01…T-10 live runs + threshold comparison | blocked |
| 28 | Loki label scheme `service_name` | `adapter/loki.py`, presets | upstream Loki `pkg/loghttp/push/otlplabels/config.go` default index labels; `labels.go` `LabelServiceName` | verified from the pinned upstream config |
| 29 | Jaeger v2 API prefix `/jaeger/ui/api` | `adapter/jaeger.py` (`api_prefix`), `--jaeger-api-prefix` | upstream `src/jaeger/config.yml` `base_path`; upstream Grafana Jaeger datasource | verified from the pinned upstream config |
| 30 | Kafka lag metric `kafka_consumer_group_lag` | alert rule, preset absent | contrib `kafkametricsreceiver` metric `kafka.consumer_group.lag` + Prometheus `otlptranslator` `normalizeName()` | verified from the pinned upstream source |
| 31 | LGTM Loki/Tempo reachable from adapter | `compose.overlay.yaml` loopback publishes | `tests/test_config.py`; adapter run | verified statically; live run pending |
| 32 | Alert thresholds match real traffic | alert rules | baseline vs fault windows | pending calibration |
| 33 | Ingest batches never mix services | `adapter/poller.py` `_by_service` | `tests/test_poller.py` per-service batching and key selection | verified against the Copilot contract |
| 34 | Per-service ingest keys | `adapter/poller.py` `parse_ingest_keys`, `--ingest-keys` / `COPILOT_INGEST_KEYS` | `tests/test_poller.py`, `tests/test_presets.py` | verified locally; real Copilot blocked |
| 35 | Evidence carries the fields detection needs | `adapter/_common.py` `pick_attr`; `adapter/{jaeger,prometheus}.py` | `tests/test_clients.py` semantic-convention and Prometheus label names | verified with fixtures |
| 36 | One out-of-range field cannot reject a batch | `adapter/events.py` `_status` / `_duration` | `tests/test_events.py` range clamping | verified against the ingest schema |
| 37 | An over-long route cannot reject a batch | `adapter/events.py` `_route` (200 chars) | `tests/test_events.py`, `tests/test_copilot_contract.py` | verified against the ingest schema |
| 38 | Evidence without an id still dedupes | `adapter/events.py` `_derived_id` | `tests/test_events.py` derived-id stability | verified |
| 39 | Only the watched service is sent | `adapter/poller.py` `watch_service`, `--watch-service` / `COPILOT_WATCH_SERVICE` | `tests/test_poller.py`, `tests/test_presets.py`, `tests/test_copilot_contract.py` | verified locally; real Copilot blocked |
| 40 | Mis-targeted sources are reported at startup | `adapter/__main__.py` `_warn_unwatched` | `tests/test_presets.py` warning content and silence | verified |
| 41 | The whole ingest contract, not a stub | `tests/copilot_contract.py` (transcribed from the Copilot at `0ce9844`) | `tests/test_copilot_contract.py` — the adapter's real HTTP client against a server enforcing auth, rate limit, body size, batch size, schema, and service binding | verified against the Copilot source; live Copilot blocked |

## What the 139 tests cover

| Test module | Contract it locks |
|---|---|
| `test_events.py` | Event schema, required fields, level and ID validation, deterministic IDs, status/duration range clamping |
| `test_clients.py` | Prometheus/Loki/Tempo/Jaeger payload parsing and evidence mapping, semantic-convention attribute names |
| `test_poller.py` | Chunking by count and bytes, retry classification, isolation, receipts, sources, per-service batching and key routing |
| `test_ingest_http.py` | Real localhost POST, header, JSON array body |
| `test_config.py` | Overlay mounts, exporter names, datasource UIDs, alert rules |
| `test_dashboard.py` | Dashboard shape, panel grid, UIDs, metric names, logs panel, mount path |
| `test_corpus.py` | Metadata, coverage, determinism, retrieval, pinned commit |
| `test_scenarios.py` | Catalog validity against `demo.flagd.json`, recorder timestamps |
| `test_run_scenario.py` | Toggle, restore, interruption, dry-run, flagd-ui HTTP round trip |
| `test_scenario_report.py` | Latest-run selection, missing runs, escaping, CLI |
| `test_presets.py` | Preset integrity, explicit service, CLI listing, per-service keys, watch-service warning |
| `test_copilot_contract.py` | The adapter's real HTTP client against a local server enforcing the Copilot's ingest rules: auth, rate limit, body size, batch size, schema, service binding, dedupe |
| `test_native_tools.py` | Tool names/params/limits/errors, read-only guarantee |
| `test_preflight.py` | Files, vendor pin, corpus freshness, Compose, Docker, probes, JSON/exit codes |

## Status change protocol

When a blocked row becomes verified:

1. Run the check and capture the exact command and observed output.
2. Update the row here and the corresponding entry in `docs/FILE-INVENTORY.md`.
3. Add the evidence to `docs/status.md` and a dated entry in `docs/change-log.md`.
4. Update `docs/HANDOFF.md` next action and remaining effort.

Do not change a row from **implemented** to **verified** on the basis of a unit test alone. If a check cannot run, leave it **blocked** and state the first useful error.
