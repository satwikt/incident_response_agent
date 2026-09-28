# Test Log

Open defects and gates are tracked in [OPEN_DEFECTS.md](OPEN_DEFECTS.md).

One entry per milestone gate (see `DESIGN.md` section 7). Each entry states what was **verified live**, what is **inferred**, and what is **out of scope**, plus a confidence statement. Findings from Tester rounds are never accepted on the Tester's word alone; the Developer re-verifies against real bytes, DB rows and responses.

## M0: hygiene, demo scenarios, Hindsight spike (2026-09-28)

**Verifier:** Developer only. No independent Tester round (the framework schedules rounds for M1, M3 and M4).

### Verified live (real container, real HTTP, real Hindsight)
- Demo app built as an image and run non-root with one worker. `/docs`, `/openapi.json` and the old `/admin/faults` return 404. Chaos endpoints without or with a wrong key return 401; the ops key and chaos key are not interchangeable.
- Five causes behave as designed: decoy pair (`bad_config` / `bad_deploy`) gives about 30% failures, wrong fix leaves them (12/40), right fix clears them (0/40); pool exhaustion (10 OK then 503 on every route, `restart_worker` only mitigates, `flush_pool` fixes); memory leak (149 OK then 500s, `flush_pool` no effect, `restart_worker` fixes); slow downstream (3.3 s to 0.007 s after `enable_fallback`).
- Fault log lines carry `release`, `config_rev`, pool and leak state (the evidence that separates the decoy pair).
- Todo UI stored XSS: old page injected 2 elements from a hostile title, fixed page 0 (checked in jsdom).
- Repo hygiene: history scrubbed and force-pushed to `origin/main`; tracked tree has no `.env`, databases, chroma store, bytecode; local run of the CI hygiene greps is clean.
- Hindsight (local Docker, Groq): retain 3 to 6 s, recall 0.2 to 0.4 s, tag filter isolates exactly, `document_id` + `update_mode="replace"` upserts without duplicates, `delete_document` removes memory units, async retain completes in 3.1 s (`gpt-oss-20b`), recall exposes `scores.final` that separates relevant from irrelevant.

### Automated
- `Demo/tests`: 87 tests pass (Python 3.13 container).

### Inferred, not verified
- CI workflow (`.github/workflows/ci.yml`) parses as YAML but has **never run on GitHub**.
- Behaviour under real traffic and real users; everything above was scripted.
- Groq free-tier limits may change; Hindsight cooldown behaviour was observed, not documented.

### Confidence
About **85%** that the M0 deliverables do what they claim in the environments tested. Lower confidence (about 60%) on anything that depends on the free LLM tiers staying available during a live demo.

### Open items and whether they block M1

| # | Item | Blocks M1? | Blocks | Plan |
|---|---|---|---|---|
| O1 | M0 work is **uncommitted and unpushed**; CI never ran | No (checkpoint only) | M1 gate (CI green) | Commit and push at M1 start or end; read the Actions result |
| O2 | **Agent LLM on Groq via ADK/LiteLLM untested** (function-calling reliability) | No (M1 has no LLM calls) | **M2 and M3** | Gate before M2: 20-run tool-call test; fall back to paid Gemini/Groq tier or another model if flaky |
| O3 | Hindsight **reflect** unusable on free tier | No | Nothing critical (runbook cards built from recall plus our own LLM call) | Optional re-test with a paid tier; consider `enable_observations=false` |
| O4 | Directives effect on reflect unverified | No | Operator-preference feature only | Test if reflect becomes available; else store preferences as tagged memories |
| O5 | XSS checked in jsdom, not a real browser (browser tool blocks 127.0.0.1) | No | M4-lite tester round | Re-check in a real browser at M4 |
| O6 | Old git history blobs still contain pre-rename wording | No | Submission | Squash-rewrite before submission if history must be clean |
| O7 | `PROJECT_OVERVIEW.md` stale after CNCF removal | No | Submission | Rewrite in M5 |
| O8 | Spike Hindsight container still running locally | No | Nothing | Stop it when not needed (`docker compose -f spike/docker-compose.yml down`) |
| O9 | `spike/.env` has `HINDSIGHT_LLM_MODEL=openai/gpt-oss-120b` (cooldown-prone) | No | Retain reliability | Change to `openai/gpt-oss-20b` (done by the user) |


## M1: push-based ingest, SQL tools, two-service stack (2026-09-28)

**Verifiers:** Developer, plus an independent Tester (fresh Sonnet instance, black box, no source access) on the security-style scenarios. An Opus tester was blocked by a provider safeguard, so per the working rule Opus only takes the functional scenarios (round pending, see below).

### Automated
- Copilot: 105 tests. Demo: 115 tests. All pass in clean Python 3.13 containers.
- Mutation check (7 injected bugs in ingest and the event store): all 7 caught by the suite.

### Verified live on the real stack (`docker compose`, two services)
- Events flow demo to Copilot to SQLite; tools return correct error rate, p95, log lines and health; the watcher detected a breach and handed off to the agent (which failed only because no LLM key is configured yet).
- Compose lists exactly `copilot` and `demo`; none of prometheus, loki, tempo, grafana, otel-collector, redis.

### Independent tester round (Sonnet), and what I did about each finding
Each finding was re-verified by me against the live stack before acting.

| Scenario | Tester result | My re-verification and action |
|---|---|---|
| 1 auth uniformity, timing | PASS (identical 401 body; medians within 0.1 ms over 100 tries per case) | not repeated |
| 2 service binding | PASS (403, no rows stored, ids namespaced) | covered by unit tests |
| 3 size limits (incl. chunked, 50 MB stream) | PASS | added exact-boundary, route 200/201 and exception-truncation tests (tester could not test these) |
| 4 concurrent duplicates | PASS (50 rows from 10 parallel identical batches; first version wins) | covered by unit test |
| 5 malformed fields | **FAIL (Med)**: lone surrogate (`�`) in `message`/`exception` gave HTTP 500 and dropped the batch | **Reproduced (500, 6 tracebacks).** Fixed: lone surrogates replaced with U+FFFD; encoding errors in the store map to 422; non-string `message`/`exception` now 422 instead of silently NULL. Regression tests added. **Retested live: 200, stored as `61 EFBFBD 62`, 0 tracebacks; real emoji pair preserved.** |
| 6 rate limit (400 req, concurrency 40) | PASS (266 x 200, 134 x 429, container stayed up, recovers in 3 s) | not repeated |
| 7 SQL/LIKE quoting | PASS (stored literally, table intact) | covered by unit tests |
| 8 demo control plane | PASS (401 for none/wrong/swapped keys; many path and header tricks all 401/404) | covered by unit tests |
| 9 information disclosure | **FAIL (Low/Med)**: (a) `PUT/DELETE /todos/<huge int>` gave 500 leaking a Python/SQLite message; (b) trailing-slash 307 redirect followed the attacker-supplied Host header; (c) demo 422 bodies echo input | (a) and (b) **reproduced and fixed**: ids bounded to 1..2^31-1 (422), unexpected errors return a generic body (injected faults still show their message on purpose), `redirect_slashes=False` on both apps. **Retested live: 422 with no internals; 404 with no Location on all three paths.** (c) is FastAPI's default; accepted, listed below. |
| 10 emitter resilience (Copilot stopped) | PASS on latency (median 8.2 ms with the Copilot down, no errors, no tracebacks); about 88 of about 200 events emitted during a 30 s outage were delivered afterwards | behaviour is by design (batches dropped after 3 attempts); documented as a limitation |
| 11 stored-content check | PASS (hostile todo titles never appear in event messages) | n/a |
| 12 own tests (methods, duplicate headers, big headers, slow body) | PASS, except no read timeout on a slow body (Low) | documented as a limitation |

Also confirmed: chaos state was **left injected from my own earlier smoke test**, so the tester's baseline was not clean (their 500s from `POST /todos` were chaos, not defects). Containers were recreated afterwards and the state is empty.
The tester printed the ingest key in its own tool transcript, so **the ingest key was rotated** and the old key now returns 401 (verified live).

### Not tested by the tester (and coverage)
Second ingest key (cross-service claims) covered by unit tests; row cap and retention covered by unit tests; 2,000 req/s flood not run (400-request burst only); access from outside the host not tested (ports are bound to 127.0.0.1); 1 MB header value.

### Functional QA round (Opus, functional scope only)
The first attempt ran in a read-only agent type and executed only the health and agent-tool checks (both PASS). It was re-run as a write-capable agent with the state changes explicitly authorised (test rows under `qa-*`, demo traffic, chaos/ops calls, container restarts). The events table was emptied first so the baseline was clean. Every finding was re-verified by me before acting.

| Scenario | Result | Notes |
|---|---|---|
| F1 query correctness (own data: window boundary, MIN_REQUESTS 9 vs 10, p95 nearest-rank, slow requests, keyword, duplicates) | PASS | tester's independently computed expectations matched the store on every value |
| F2 time semantics (`received_at` decides, not client `ts`) | PASS | 3-day-old ts counted, 10-minute-old received_at excluded |
| F3 health (unknown / up / idle, inclusive 300 s boundary) | PASS | |
| F4 end-to-end with an exact request sequence | PASS | counts per route template and status, levels, no events for /health or control-plane calls, no raw ids, context suffix on every row |
| F5 five causes x wrong/right action | PASS | all five reproduced as specified; bad_config vs bad_deploy differ only in `config_rev` vs `release`; wrong actions leave the symptom, right action clears it |
| F6 watcher | PASS for healthy periods and bad_config (breach counts matched the client-side counts exactly); **FAIL** on the slow-request rule | see D1 |
| F7 restarts | PASS | Copilot restart keeps all rows (688 before and after); demo restart clears chaos and events resume |

Defects found and their disposition (all re-verified against code and the live stack):

| ID | Sev | Finding | Action |
|---|---|---|---|
| D1 | Med | Slow-request breach ignored MIN_REQUESTS and was not per route (5 slow requests on a 5-request route fired) | **Fixed.** `slow_requests` now reports per-route slow and request counts; the detector judges only routes with enough data. Regression tests added. **Live retest:** the tester's repro gives no breach; a 12-of-12 positive control still fires and names route and window. |
| D2 | Low | `recent_logs` ordered by insertion, not arrival time | **Fixed** (`ORDER BY received_at DESC, pk DESC`), regression test, live check |
| D3 | Low | The same breach re-fires every 15 s with no cooldown, so one agent run per cycle (11 in about 5 minutes) | **Deferred to M2** (incident fingerprint, dedupe and cooldown are the M2 deliverable) |
| D4 | Low | Breach lines did not state their window (15 s) or the route for slow requests, so they did not line up with the 5-minute tool figures | **Fixed**: every breach line carries `[window Nm]`; slow breaches name the route |
| n/a | | Row totals exclude log-style events without route or status (1120 vs 1125 raw rows) | By design; `error_rate` output now says "counts request events only", test added |
| n/a | | `worker_uptime_s` seemed to reset on rollbacks | Not a defect: only `restart_worker` and `chaos/reset` reset it (the tester reset between causes); test added |
| n/a | | Unmatched `/todos/...` paths emit `GET (unmatched)` events | Consistent with the "paths under /todos" rule; harmless (4xx is not counted as an error) |

Not verified by the tester: other fault/route combinations (one route per cause was tested; the mechanics are route-agnostic and covered by unit tests).

### Final M1 status
- **Automated:** Copilot 110 tests, Demo 116 tests, 0 failing; 7/7 injected mutations caught earlier in the round.
- **Live:** two-service stack; ingest, storage, tools, watcher, restarts and all five fault scenarios verified against real traffic by two independent testers (Sonnet security scenarios, Opus functional scenarios) plus my own re-verification of every finding.
- **Compose:** `copilot` and `demo` only.
- **Confidence: about 90%** that M1 does what it claims in the tested environment. Residual risk: behaviour under real production traffic, TLS/proxy concerns, and the LLM-dependent path (the watcher's agent call has not run with a working model yet, gate O2).
- **Carried forward:** D3 (alert dedupe) and gate O2 (agent on Groq) into M2; chat auth and UI escaping into M4.

### Published
M0 (`af3a821`) and M1 (`6dbc150`) were pushed to `origin/main`. The first GitHub Actions run (`ci` #1, run 36456523551) passed all three jobs: `copilot-tests`, `demo-tests`, `hygiene` (event-wording, forbidden-files, compose-size and gitleaks checks). This closes the "CI never ran" item.
