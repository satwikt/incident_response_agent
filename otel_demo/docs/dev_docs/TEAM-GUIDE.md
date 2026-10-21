# Team Guide

Practical instructions and tips for working in this repository. For *what* the code is, see [CODE-STRUCTURE.md](CODE-STRUCTURE.md). For *what must not be broken*, see [IMPLEMENTATION-NOTES.md](IMPLEMENTATION-NOTES.md). For *what to do next*, see [TEAM-HANDOFF.md](TEAM-HANDOFF.md).

---

## 1. First hour

### 1.1 Get oriented

```bash
cd "/home/vinit/SRE Copilot"
cat docs/README.md                      # what this project is
cat docs/CODE-STRUCTURE.md              # the map
cat docs/IMPLEMENTATION-NOTES.md        # the invariants
```

Then run the checks. If they pass, you have a working baseline:

```bash
cd integration/astronomy-shop
make check
```

Expected output:

```
Ran 139 tests in 6.3s
OK
corpus is up to date
3 pass, 0 warn, 0 fail, 2 skip
```

The two `skip`s are Docker and Compose — they are skipped because `make check` runs `preflight --local-only`. That is expected in a worker without Docker.

### 1.2 Know what you cannot do here

| Not possible | Why | Workaround |
|---|---|---|
| Run Docker / Compose | no `docker` binary in this worker | Live validation happens on a Docker host; see `docs/live-validation.md` |
| Install packages | no `pip`/`ensurepip`; the sandbox classifier blocks downloaded packages | stdlib only — `unittest`, `http.server`, `urllib` |
| Run the real Copilot | no checkout, no endpoint, no key | `tests/copilot_contract.py` transcribes its rules |

**The whole module is stdlib-only on purpose.** Do not reintroduce pytest, PyYAML, requests, or pydantic. If you need something, write it against the standard library.

### 1.3 Read the invariants before you change code

`docs/IMPLEMENTATION-NOTES.md` §4 (ingest contract) and §7 (unverified assumptions) are the two sections that will bite you. The short version:

- One ingest request per service. Never mix services in a batch.
- Clamp every field before sending — the Copilot validates a batch atomically.
- The Copilot watches exactly one service.
- Never edit anything under `vendor/`.
- Never edit the vendored `demo.flagd.json`; use the flagd-ui client.

---

## 2. The commands you will use every day

All commands run from `integration/astronomy-shop/` unless stated otherwise.

| Command | What it does | Use it when |
|---|---|---|
| `make check` | tests + corpus freshness + local preflight | **Before every commit.** |
| `make test` | 139 stdlib unittest tests | While developing. |
| `python3 -m unittest tests.test_poller -v` | one test module | Narrowing down a failure. |
| `make corpus` | regenerate `docs-corpus/` | After editing any `docs/**` file. |
| `make corpus-check` | fail if the corpus is stale | In CI. |
| `make preflight` | local readiness checks | Checking the environment. |
| `make live-preflight` | Compose config + live backend probes | On a Docker host with the stack up. |
| `make report` | Markdown scenario coverage | See which scenarios have been run. |
| `make dry-run` | print the T-02 plan, send nothing | Before touching a live flag. |
| `make adapter-help` | the adapter's CLI help | Finding a flag. |
| `python3 -m adapter --list-presets` | the nine presets | Writing a query. |

Run a single test:

```bash
python3 -m unittest tests.test_events.EventContractTests.test_oversized_route_is_truncated
```

---

## 3. Common tasks

### 3.1 Add a Prometheus preset

Presets live in `adapter/presets.py`. Derive the query from a vendored dashboard so you never invent a signal.

```python
QueryPreset(
    name="cart-error-ratio",
    backend="prometheus",
    query=(
        'sum(rate(http_server_request_duration_seconds_count'
        '{service_name="cart",http_response_status_code=~"5.."}[5m])) '
        "/ clamp_min(sum(rate(http_server_request_duration_seconds_count"
        '{service_name="cart"}[5m])), 0.001)'
    ),
    service="cart",
),
```

Then add the test in `tests/test_presets.py` — the preset integrity test asserts that every preset mentions `service_name` and its own service, so a missing service override fails the suite. That is the intended behaviour, not an annoyance.

### 3.2 Add a Grafana alert rule

Add to `grafana/provisioning/alerting/astronomy-shop-alerts.yaml` and update `tests/test_config.py`, which counts the rules and checks their UIDs and metric names. Metric names must come from the vendored dashboards.

### 3.3 Add a scenario

Add to `scenarios/catalog.json`:

```json
{
  "id": "T-11",
  "name": "cart service down",
  "kind": "error",
  "mode": "minimal",
  "flag": "cartServiceFailure",
  "variant": "100%",
  "expected_services": ["cart"],
  "expected_cause": "cart requests fail",
  "evidence_hints": ["cart error spans and logs", "checkout 5xx rate rises"],
  "recovery": "flag off"
}
```

`tests/test_scenarios.py` validates every flag and variant against the vendored `demo.flagd.json` — if the flag does not exist there, the suite fails. That is the point: the catalog must not invent flags.

### 3.4 Change the ingest path

Read `docs/IMPLEMENTATION-NOTES.md` §4 first. Then extend `tests/copilot_contract.py` if you are adding a rule, and add a case to `tests/test_copilot_contract.py` if you are changing behaviour. Do not add ad-hoc mocks — the contract test exists because the mock-based tests missed real bugs.

### 3.5 Change a doc

Any edit under `docs/` makes the corpus stale. `make check` will fail with:

```
FAIL  corpus               corpus is stale; run tools/build_corpus.py
```

Fix it with:

```bash
python3 tools/build_corpus.py    # or: make corpus
```

This is the single most common "broken" build in this repo. It is not a real failure.

### 3.6 Run a fault scenario

```bash
make dry-run                                    # always first: shows the plan, sends nothing
python3 tools/run_scenario.py T-02 --duration 300
python3 tools/scenario_report.py --format md
```

The runner snapshots the whole flag state before flipping anything and restores it in a `finally` block, so an interrupted run still recovers. If a flag is stuck on:

```bash
# look at the snapshot the runner wrote
ls scenarios/runs/*.snapshot.json
```

---

## 4. Tips that save time

### 4.1 Check the vendor, not your memory

The upstream demo is pinned at `85c2cb253ae3` and must stay clean:

```bash
git -C vendor/opentelemetry-demo status --short --branch
git -C vendor/opentelemetry-demo rev-parse HEAD
```

`make check` fails if it drifts. If you *think* you know a metric name, check it:

```bash
grep -rn "http_server_request_duration_seconds" vendor/opentelemetry-demo/src/grafana/
```

### 4.2 The two "assumptions" are already resolved

Both were confirmed against upstream source — do not re-derive them:

- **Loki label is `service_name`** (Loki's default OTLP index labels include `service.name`, normalized to an underscore).
- **Kafka metric is `kafka_consumer_group_lag`** (the contrib receiver emits `kafka.consumer_group.lag`; Prometheus's OTLP translator joins tokens with `_`).

Both are still unobserved at runtime, so confirm them on the first live run.

### 4.3 Debug an empty result set

In order:

1. Is the backend reachable? `make live-preflight` probes it.
2. Is the **time unit** right? Loki/Tempo want nanoseconds, Jaeger wants microseconds.
3. Is the window empty? `--window` defaults to 300 s.
4. Is the **label** right? `service_name` on Prometheus and Loki.
5. Is the **path** right? Jaeger v2 is at `/jaeger/ui/api/...`.
6. Is the query right? Copy it from a vendored dashboard.

Empty result sets almost never mean "no data" — they mean unit, label, path, or window.

### 4.4 Debug an ingest rejection

| Status | Meaning | Fix |
|---|---|---|
| `401` | unknown key | `X-Api-Key` value; the key must exist in the Copilot's `INGEST_KEYS`. Not retried — a bad key never becomes good. |
| `403` | key bound to a different service | The batch mixed services, or the key is for another service. |
| `413` | body too large or too many events | `chunk_events` already handles both; check `MAX_BODY_BYTES` / `MAX_BATCH_SIZE`. |
| `422` | one invalid event rejected the batch | Find the offending field: `status` out of 100–599, `duration_ms` out of range or non-finite, `route` over 200 chars, `id` not matching `^[A-Za-z0-9._:-]{1,64}$`, `ts` more than 5 min ahead or 7 days behind. |
| `429` | rate limited | 20/s, burst 40, per service. Slow down or send fewer batches. |

### 4.5 Duplicate events on every poll

The Copilot dedupes on the event id. Duplicates mean the id is not stable.

- Each client sets a stable id via `_common.stable_id` — check that the evidence carries an explicit `id`.
- `events.to_event` derives a deterministic id when none is set, so the fallback is safe.
- If the *timestamp* changes between polls, the derived id changes with it. That is expected for genuinely new evidence and a bug for the same evidence.

### 4.6 Read the `source_errors` key format

`PollReport.source_errors` is keyed `<source>:<reason>`:

| Key | Meaning |
|---|---|
| `prom:ingest` | The send failed. The message carries the status. |
| `prom:ingest-keys` | Events were dropped — no key for that service, or it is not the watched service. |
| `prom` | The source itself failed (backend unreachable, bad response). |

A source error never aborts the cycle. One failing backend does not stop the others.

---

## 5. Conventions

### 5.1 Code style

- stdlib only, Python 3.11+ syntax (`X | None` in annotations is fine).
- Type hints on public functions.
- Docstrings say *why*, not *what*. One line where a line will do.
- No new dependencies. Ever.

### 5.2 Test style

- One `unittest.TestCase` per concern, named `*Tests`.
- Test names state the behaviour: `test_oversized_route_is_truncated`, not `test_route_2`.
- Prefer a real local HTTP server over a mock when the code path is network-shaped — that is why `test_ingest_http.py` and `test_copilot_contract.py` exist.
- When a test is asserting a contract, say in a comment which contract and where it comes from.

### 5.3 Documentation conventions

- "verified" means a reproducible check passed, and the command and output are recorded.
- "implemented" means the artifact exists but has not been observed live.
- "blocked" means a specific dependency prevents progress — name it.
- Never upgrade a claim from "implemented" to "verified" on the basis of a unit test alone.

### 5.4 The change-log

`docs/change-log.md` gets a dated entry per chunk:

```markdown
## YYYY-MM-DD — <what changed>

### Changed
- file: what changed and why.

### Commands/checks
```bash
make -C /home/vinit/SRE\ Copilot/integration/astronomy-shop check
```

### Result
- `make check`: `Ran N tests` / `OK`; ...
```

### 5.5 The verification matrix

`docs/VERIFICATION-MATRIX.md` maps each requirement to the artifact and the check that proves it. When you close a gap, update the row, then update `docs/FILE-INVENTORY.md` and `docs/status.md` in the same change.

---

## 6. Things not to do

| Don't | Why |
|---|---|
| Modify anything under `vendor/` | The pin is enforced; upstream must stay reproducible. |
| Edit the vendored `demo.flagd.json` | Use `flagd_ui.py`. The runner's snapshot/restore is the only supported path. |
| Mix services in one ingest batch | The Copilot rejects the whole batch with `403`. |
| Send an unclamped field | The batch is validated atomically; one bad event loses all of them. |
| Add a default query to the adapter | No invented defaults. A backend is used only if asked for. |
| Add a dependency | The module must run on stdlib alone. |
| Hand-edit `docs-corpus/*` | It is generated. Rebuild it. |
| Edit a doc and forget `make corpus` | `make check` will fail on a stale corpus. |
| Reintroduce pytest | The worker has no pytest; the suite is stdlib. |
| Report the stack as "working" without a live run | Docker has never been available here. Say "locally verified", not "working". |

---

## 7. Getting unstuck

| Situation | Do this |
|---|---|
| `make check` fails on the corpus | `python3 tools/build_corpus.py` |
| `make check` fails on the vendor pin | `git -C vendor/opentelemetry-demo status` — something changed upstream; restore it. |
| A test fails after a doc edit | The corpus test compares the committed corpus to a fresh build. Rebuild. |
| The suite passes but the adapter sends nothing | Read `source_errors` in the run output. It names the source and the reason. |
| You need the Copilot's behaviour | `tests/copilot_contract.py` has it transcribed, with the source commit noted. |
| You need to know what the demo really emits | `grep` the vendored dashboards and the demo's own source. |
| You need to know what the Copilot really does | Read the Copilot repository read-only at `github.com/satwikt/incident_response_agent`, commit `0ce98443db0b0b3c09c40a53849164c1f7160fe9`. |

When you are genuinely stuck, record the question in `docs/status.md` under "External validation not performed" rather than guessing. A recorded open question is worth more than a plausible assumption.
