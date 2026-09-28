# Design Document: Memory-Driven Incident Response Agent

Status: v2 (after independent Opus review round 1). Owner: team. Date: 2026-09-28.
**Where §11–§13 conflict with §1–§10, §11–§13 win.** They record review findings that I verified against the code or docs.
Base: existing "SRE Copilot" (Google ADK + FastAPI) in `Copilot/`, target app in `Demo/`.

---

## 1. Purpose and requirements (the "product document")

We are building the **Incident Response Agent** from the event's problem list: *"Remembers past incidents, their root causes, resolution steps, and which runbooks worked. Learns from post-mortems to suggest faster fixes for similar issues."*

Hard requirements (from the problem statement and content guide):
- R1. Must be built using **Hindsight** (retain / recall / reflect) and must clearly demonstrate how memory is used.
- R2. Memory must be central and show a visible before/after; the agent must visibly improve over repeated incidents.
- R3. Submission: clean documented GitHub repo, demo video, live demo, explanation of Hindsight usage.
- R4. Professional (non-student) use case; realistic data; tight scope; understandable value within 60 seconds.
- R5. Public artefacts (repo copy, README, article, posts) must not reference the event they were built for.
- R6. Any LLM allowed; handle function-calling errors.

Judging weights: Innovation 30, Hindsight memory 25, Technical implementation 20 (clean, well-architected, edge cases), UX 15, Real-world impact 10.

### 1.1 Non-goals (scope discipline)
Multi-tenant SaaS, Slack/PagerDuty adapters, distributed tracing, custom dashboards, autonomous (unapproved) remediation, Kubernetes. One persona: an on-call SRE. One workflow: detect → recall → diagnose → approve fix → learn.

---

## 2. Architecture

### 2.1 What changes vs. the current repo
| Current | Target | Reason |
|---|---|---|
| OTel Collector, Prometheus, Loki, Tempo, Grafana (6 containers) | Push-based `POST /ingest/events` + SQLite `events` table | Complexity unrelated to memory; those tools were for an earlier contest |
| PromQL/LogQL/Tempo tools | SQL-backed tools with the same names/return shapes | Keeps `watcher._evaluate` and prompt mostly intact |
| No memory; `search_docs` RAG over README | Hindsight bank (retain/recall/reflect) | Requirement R1/R2 |
| Watcher alerts every cycle | Fingerprinted incidents with a lifecycle | Alert-storm bug; needed for learning |
| Discord only, diagnose only | Discord notify + incident page (approve/feedback) + allowlisted remediation | Closes the learning loop |
| Redis chat cache, Chroma | Removed | No story value; less to secure/operate |

### 2.2 Components
```
Target app(s) --HTTP push--> [Ingest API] --> SQLite(events)
                                                  |
                                   [Watcher] (timer, single-flight)
                                                  |
                                   [Incident Manager] (fingerprint, dedupe, state machine)
                                        |                     |
                              [MemoryStore] <---recall/retain---> Hindsight
                                        |
                                   [Agent (ADK)] tools: read-only SQL tools + recall
                                        |
                        [Notifier: Discord webhook]      [Incident API + UI]
                                                              | approve / feedback
                                                     [Remediation Executor]
                                                      (allowlisted actions only)
```
Modules (new unless noted):
- `api/ingest.py` – authenticated batch ingest, validation, size/rate limits.
- `db/events.py`, `db/incidents.py` – SQLite access, parametrised queries only, retention purge.
- `agent/tools.py` (rewrite) – read-only SQL tools; **no shell, no arbitrary HTTP**.
- `agent/incidents.py` – fingerprinting, dedupe, state machine, cooldown/hysteresis.
- `agent/memory.py` – `MemoryStore` interface with `HindsightMemory`, `NullMemory` (memory-off ablation), `FakeMemory` (tests). All calls have timeouts and fail open.
- `agent/remediation.py` – action registry (name → fixed HTTP call, validated params), executed only for an APPROVED incident.
- `agent/watcher.py` (modify) – single-flight lock, uses incident manager.
- `agent/notifier.py` (modify) – retry + outbox; alert includes incident link.
- `api/incidents.py` + `frontend/` – incident page, memory panel, MTTR trend.
- `scripts/seed_history.py`, `scripts/replay_events.py`, `scripts/bench_learning.py`.

### 2.3 Data model (SQLite, UTC everywhere)
- `events(id TEXT PK, ts INTEGER, service, level, route, status INT, duration_ms INT, message, exception, received_at)`; index (service, ts), (service, route, ts). `id` is client-supplied or hash; duplicates ignored (`INSERT OR IGNORE`).
- `incidents(id TEXT PK 'INC-0001', fingerprint, service, status, severity, opened_at, acked_at, resolved_at, rca_text, proposed_action_json, action_status, recalled_ids_json, version INT, source 'live'|'seed')`.
- `feedback(id, incident_id, actor, verdict 'correct'|'wrong'|'partial', actual_cause, fix_applied, created_at)`.
- `outbox(id, kind 'retain'|'discord', payload_json, attempts, next_attempt_at, status)` – durable retries so Hindsight/Discord outages lose nothing.
- Retention: events older than `EVENT_RETENTION_HOURS` (default 24) purged by the watcher; hard cap on rows.

### 2.4 Incident state machine
`OPEN → ACKED → REMEDIATING → MONITORING → RESOLVED`; side exits: `SUPPRESSED` (human marks noise), `REOPENED` (same fingerprint recurs within `REOPEN_WINDOW`). Transitions are compare-and-swap on `version`; illegal transitions return 409. RESOLVED requires `K` consecutive healthy windows (not just one), so a fix is only recorded as "worked" when recovery is confirmed.

### 2.5 Fingerprint
`sha256(service | sorted(breached routes) | breach kinds | normalised exception type+top frame)`. Numbers, ids, timestamps in messages are normalised out. Minimum sample size (`MIN_REQUESTS`, default 20) before an error-rate breach counts, to avoid "1 of 1 failed = 100%".

---

## 3. Memory design (Hindsight)  — the star

> Exact SDK/API names must be verified against https://hindsight.vectorize.io/ before coding; everything is behind `MemoryStore` so a mismatch costs one file.

### 3.1 Bank layout
One bank per environment (`incident-response-<env>`); items carry metadata: `service`, `route`, `fingerprint`, `incident_id`, `kind` (`incident|feedback|preference|postmortem|failed_fix`), `source` (`live|seed`), `outcome`, `mttr_s`.

### 3.2 Retain (write path)
Trigger only from trusted, structured moments, never from raw log text:
1. Incident RESOLVED → one structured narrative: symptoms (routes, error %, p95), exception signature, RCA, proposed vs. applied fix, outcome, MTTR, who approved.
2. Human feedback (RCA wrong + actual cause) → `feedback` item linked to the incident.
3. **Negative memory:** fixes that failed and hypotheses ruled out (`failed_fix`).
4. Operator preferences from chat ("no restarts at peak") → `preference`.
Idempotent by `incident_id` (upsert/document id if supported; otherwise dedupe key in outbox). PII/secret scrubbing runs before retain (§5).

### 3.3 Recall (read path)
On new incident and on chat questions: query built from fingerprint + symptoms. Results are filtered by relevance threshold and shown with **provenance** (incident id, date, `seed|live`, similarity). Agent prompt: use recalled items as *hypotheses to verify with live tools*, never as facts; if nothing relevant, say "no close match". Recall is also exposed as an agent tool `recall_similar_incidents`.

### 3.4 Reflect
Scheduled (and on-demand from UI): synthesise patterns ("POST /todos 500 storms: fix X worked 4/5") into runbook insights shown in the Memory panel and used in prompts. Outputs are labelled as generated.

### 3.5 Memory ablation (judging asset)
`MEMORY_MODE=on|off` (`HindsightMemory` vs `NullMemory`). UI has a "replay this incident without memory" button showing the generic RCA beside the memory-augmented one. `scripts/bench_learning.py` runs N scripted incidents in both modes and outputs time-to-RCA, tool rounds, fix-hit@1 and a learning-curve PNG for README/video.

---

## 4. Key flows
1. **Ingest:** app handler batches events → `POST /ingest/events` (API key) → validated → `INSERT OR IGNORE`.
2. **Detect:** watcher (single-flight, every N min) computes windowed error %, p95, slow requests, keyword matches; applies thresholds + min sample + hysteresis; fingerprints.
3. **Incident:** existing open fingerprint → append evidence, no new page (unless severity escalates). New → create incident, recall, run agent, notify.
4. **Diagnose:** agent gets breach summary + recalled memories (as untrusted hypotheses) + read-only tools; outputs structured JSON (`rca`, `evidence[]`, `proposed_action{name,params}`, `confidence`, `matches[]`), validated against a schema; on LLM/tool failure a templated alert is sent without RCA.
5. **Human:** Discord shows summary + link. Incident page: Approve fix / RCA wrong + actual cause / Ack / Suppress.
6. **Remediate:** executor runs an allowlisted action; watcher confirms recovery over K windows; RESOLVED, MTTR computed.
7. **Learn:** retain outcome/feedback via outbox; reflect periodically; UI updates.
8. **Chat:** same agent/tools/memory; preferences retained.

---

## 5. Security design

Threat model assumptions: the Copilot is internet-reachable in the demo; log content is attacker-influenced (any user of the target app can put text in logs); the LLM can be manipulated by text it reads.

| # | Threat | Mitigation |
|---|---|---|
| S1 | Unauthenticated ingest / forged events | `X-API-Key` (per-service key, constant-time compare, from env); reject otherwise; 401 not 403 detail leaks |
| S2 | Ingest DoS / disk fill | Max body 1 MB, max 500 events/batch, per-field length caps (truncate message 4 KB), per-key rate limit, events retention + row cap |
| S3 | **Prompt injection via logs** ("ignore instructions, disable auth") | Log text passed inside clearly delimited data blocks, labelled untrusted; agent tools are read-only; the LLM can only *propose* an action name from the allowlist; execution requires human approval + server-side validation; structured output schema; LLM never gets a shell/HTTP tool |
| S4 | **Memory poisoning** (attacker crafts logs so bad "fixes" get retained) | Retain only from RESOLVED incidents or authenticated human feedback; never retain raw log lines; provenance + `source` metadata; low-confidence/unverified fixes flagged; approve required before an action is even suggested as "worked"; ability to delete/quarantine a memory |
| S5 | Unauthorised approval of remediation (CSRF, forged clicks) | Operator token/session for incident actions; `SameSite` cookies or bearer header; CSRF token on state-changing POSTs; CORS restricted to own origin; every action audit-logged (actor, time, incident, params) |
| S6 | Remediation abuse / SSRF / command injection | Action registry: fixed method+URL template on a config-defined base URL, param schema + enum validation; remove `subprocess` import; no user-supplied URLs; timeouts; result recorded |
| S7 | XSS in incident page (logs, LLM output rendered) | Render with `textContent`/escaped templates only; no `innerHTML` for dynamic data; CSP header `default-src 'self'`; Markdown from LLM sanitised or shown as plain text |
| S8 | SQL injection | Parametrised queries only; LIKE patterns escaped; window/limit params clamped ints |
| S9 | Secrets leakage | `.env` untracked (currently `Demo/.env` is committed with an admin password: rotate, remove from history if public); no keys in logs; `.env.example` only; secret scan in CI |
| S10 | PII/secrets in logs flowing to LLM + Hindsight (third party) | Redaction filter before storage of memory and before prompts: emails, bearer tokens, API keys, JWTs, card-like numbers, IPs (configurable); documented data-handling note |
| S11 | LLM cost/abuse | Per-hour cap on agent runs, circuit breaker, chat rate limit per client, max output tokens |
| S12 | Discord webhook URL leak | Env only; never logged or returned by API; redact in error messages |
| S13 | Container hardening | Non-root user, pinned dependency versions + hashes/lock file, no debug server, health endpoints don't expose config |
| S14 | Information disclosure in errors | Generic error bodies; stack traces only in server logs |
| S15 | Seed data being mistaken for live evidence | `source=seed` badge everywhere; recall UI shows it; agent instructed to weight live over seed |

---

## 6. Edge cases and failure modes (must each have a test)

Detection: empty window; single request (min sample); out-of-order / late / duplicate events; clock skew (use `received_at` fallback); huge burst; events for unknown service; flapping around threshold (hysteresis/cooldown); multiple simultaneous incidents in different routes; watcher overlap when a cycle exceeds the interval (single-flight lock); process restart mid-incident (state in SQLite; `MONITORING` resumes).
Incident: dedupe across cycles; reopen within window; approve on RESOLVED/SUPPRESSED (409); two operators approving at once (CAS on version); remediation HTTP failure/timeout → `action_status=failed`, nothing retained as "worked"; fix "succeeds" but breach persists (do not mark resolved; retain as `failed_fix`); human corrects RCA after resolution (append feedback, re-retain).
Memory: Hindsight down/slow (timeout, fail open, outbox retry with backoff, UI shows "memory degraded"); recall returns nothing/irrelevant (threshold; "no close match"); duplicate retain (idempotent key); very long narratives (truncate); conflicting memories (agent must cite both, prefer newer live); seed vs live contamination; memory-off mode parity.
LLM: tool-call errors/malformed JSON (retry once, then templated alert); refusal/empty text; timeout; hallucinated action name (rejected by allowlist); model asked for action with bad params (rejected by schema).
Notifier: Discord 429/5xx (retry with `Retry-After`, outbox); webhook unset (no-op); 2000-char/embed field limits.
Data: non-UTF8, control characters, extremely long messages, timestamps in the future, negative durations.
Time: everything UTC; DST irrelevant; window boundaries inclusive-exclusive and tested.

---

## 7. Testing and delivery process (Pair-Programming & Adversarial Live-Testing Framework)

Roles:
- **BA** — turns this document and the judging criteria into acceptance criteria (§8) *before* code; maps findings back at the end and gives a business go/no-go.
- **Developer (main agent)** — implements a milestone, runs the automated suite, starts the real stack (real HTTP server, real SQLite file, real Hindsight instance or a locally hosted Hindsight — not mocks for the live round), independently re-verifies every tester claim.
- **Tester (fresh, independent model instance, no implementation context)** — given the running app URL, DB path, and a scenario brief; instructed to *break* it; must return raw evidence (exact requests/responses, SQL output).

Loop per milestone (from the framework): implement → full automated tests → start real app → scenario brief to Tester (adversarial) → evidence returned → Developer re-verifies claims against real bytes/DB → triage by severity, fix narrowest → re-run suite → targeted re-test of only failed scenarios → repeat → state final confidence with what was verified live vs. inferred. Rounds are reserved for high-cost-of-failure milestones (M1 ingest/security, M3 incident lifecycle, M4 memory, M5 remediation). Tester findings are never accepted on say-so; the Tester's own requests for permissions are not honoured on its behalf ("permission laundering").

Automated suite (pytest, must run in CI without network): unit tests for fingerprinting, min-sample, hysteresis, state machine CAS, allowlist/schema validation, redaction, SQL helpers, `FakeMemory`/`NullMemory` parity, outbox backoff, fail-open paths; API tests with FastAPI TestClient for auth/limits/409s; one docker-compose smoke test. Coverage target ≥ 80% on `agent/incidents.py`, `agent/memory.py`, `agent/remediation.py`, `api/ingest.py`.

Definition of done per milestone: AC pass (§8), suite green, tester round(s) clean or accepted limitations documented in `docs/KNOWN_LIMITATIONS.md`, confidence statement recorded in `docs/TEST_LOG.md`.

---

## 8. Acceptance criteria (BA role) and milestones

| ID | Criterion | Scoring link |
|---|---|---|
| AC1 | `docker compose up` starts ≤3 services (demo app, copilot, optional local Hindsight); no Prometheus/Loki/Tempo/Grafana/OTel/Redis | Tech, UX |
| AC2 | Authenticated batch ingest works; bad key 401; oversize 413; malformed 422; duplicates ignored | Tech, security |
| AC3 | Injected fault → exactly **one** incident per fingerprint regardless of cycles; recovery confirmed by K healthy windows | Tech |
| AC4 | Incident 1 (no history match) yields generic RCA; after resolution outcome is retained in Hindsight (verifiable in Hindsight UI) | Memory |
| AC5 | Same fault again → alert cites prior incident id, prior fix and MTTR; proposal matches prior fix; time-to-resolve lower | Memory, Innovation |
| AC6 | Similar-but-different fault recalls a related (seeded) incident and adapts advice, labelled `seed` | Memory, Innovation |
| AC7 | Human feedback "RCA wrong: actual cause Y" changes the next diagnosis of that fingerprint | Memory |
| AC8 | Restart of Copilot preserves incidents and recall; Hindsight outage degrades gracefully with visible banner and no lost retains | Tech |
| AC9 | Remediation only via allowlist after approval; failed remediation not recorded as success | Tech, security |
| AC10 | Incident page + Memory panel + learning curve understandable in 60 s; memory on/off comparison available | UX, Memory |
| AC11 | Prompt-injection log line cannot trigger an action or alter allowlist; XSS payload in log renders inert | Security |
| AC12 | `bench_learning.py` produces a reproducible chart comparing memory on/off | Memory, Innovation |
| AC13 | README documents architecture, Hindsight usage, security notes, run/seed/demo steps; repo free of secrets, DBs, pyc, any reference to the originating event | R3, R5 |
| AC14 | Public content deliverables (per-person article + post, team video) exist and follow the content guide | Submission |

Milestones (each ends with the gate in §7):
- **M0 hygiene:** untrack DBs/.env/pyc, rotate password, `.gitignore`, rename the event-specific service name, remove `Claude outputs/`, CI skeleton.
- **M1 ingest + SQL tools (AC1,2):** remove CNCF stack; tester round on security/limits.
- **M2 incident manager (AC3):** fingerprint, state machine, watcher single-flight, notifier outbox.
- **M3 memory (AC4–8, 12):** `MemoryStore`, Hindsight integration, outbox, seed script, bench script; tester round on poisoning/outage.
- **M4 human loop + UI (AC7, 9, 10, 11):** incident page, feedback, remediation, memory panel; tester round on XSS/CSRF/injection/races.
- **M5 polish:** README, demo mode/reset, video, content.

---

## 9. Where we can earn more judging points (beyond the base plan)

**Innovation (30)**
- *Negative memory*: remembers failed fixes and ruled-out hypotheses so it stops re-suggesting them.
- *Calibrated confidence*: track how often past RCAs for a fingerprint were confirmed; show "correct 4 of 5 times" instead of model self-confidence.
- *Change correlation*: ingest a deploy/config event stream; memory learns "incidents after deploys touching X".
- *Auto post-mortem*: on resolution, draft a blameless post-mortem (retained + downloadable Markdown).
- *Noise learning*: dismissed alerts teach suppression of recurring false positives.
- *Live A/B*: memory-on vs memory-off side by side.
**Hindsight memory (25)**
- Use all three verbs visibly (retain, recall, reflect), typed metadata, provenance in UI, preference memory, and a benchmark-generated learning curve with real numbers (time-to-RCA, tool rounds, fix-hit@1 vs incident count).
**Technical (20)**
- Tests + CI + fail-open + outbox + idempotency + state machine + documented threat model (this doc) + `KNOWN_LIMITATIONS.md` + typed schemas + structured logging + pinned deps.
**UX (15)**
- One-click approve, timeline per incident, "why this suggestion" (recalled items), one-button demo scenario runner with reset, dark/light.
**Impact (10)**
- Generic ingest contract (any app), documented path to Slack/PagerDuty/Datadog log sources, cost estimate per incident, MTTR reduction story, data-handling/redaction story for enterprises.
**Submission bonuses**
- Content guide compliance (article, LinkedIn post, YouTube video with thumbnail, Reddit share, tag Code.in), architecture diagram with Hindsight in the stack, "Hindsight usage" section in README with real retain/recall snippets.

---

## 10. Open questions for reviewers
1. Is the Hindsight retain/recall/reflect mapping in §3 realistic against the actual API (metadata filters, document ids, latency)? Self-hosted vs Cloud for the demo?
2. Is the scope achievable in the remaining time? What would you cut first?
3. Any security gap in §5 that would embarrass us in front of judges?
4. Any edge case in §6 mishandled, or missing?
5. Which additional features from §9 give the best score per hour?

---

## 11. Review round 1: resolutions

Reviewer: fresh Opus instance, read-only. Its Hindsight API claims came from its own memory (its fetch failed); I re-fetched the docs and marked what is confirmed. Claims about our code I checked myself.

### 11.1 Verified facts
- **Confirmed in code:** the only "fix" in the demo is `toggle_fault(..., False)` (`Demo/app/faults.py:76`), i.e. flipping the switch that injected the bug. `/admin/faults` endpoints have no auth (`Demo/app/main.py:143,158`). `faults_active.add(-1)` runs even if the fault was already off (`main.py:178`). `faults.py:88-89` logs "Cleared N MB" *after* clearing, so it always says 0. `watcher._poll_once` awaits the full LLM RCA inline (`watcher.py:226`), blocking detection.
- **Confirmed in Hindsight docs/README:** Python `from hindsight_client import Hindsight; Hindsight(base_url=...)`, `retain(bank_id, content)`, `recall(bank_id, query)`, `reflect(bank_id, query)`; self-host via Docker image `ghcr.io/vectorize-io/hindsight` (API 8888, UI 9999, needs `HINDSIGHT_API_LLM_API_KEY`, 25+ providers incl. Gemini, data in a volume); features listed: tags and metadata, document IDs, async retain, mental models; banks have mission/directives/disposition.
- **Still unverified (M0 spike must settle):** exact parameter names for tags/metadata/document_id on retain and filtering on recall; upsert semantics for a repeated document id; delete API; recall consistency latency right after retain; built-in auth (assume none); whether recall scores are exposed.

### 11.2 Accepted changes (with the design change)
**Blockers**
- **B1 (fix is the injection switch): accept.** Separate *chaos* (test harness) from *operations*. The demo app gets injectable **causes** with distinct real fixes, plus decoys:
  - `pool_exhausted` (connection leak) → fix `flush_pool`; `restart_worker` only partly helps.
  - `bad_config` (bad timeout/flag value → 500 storm) → fix `rollback_config`; restarting does nothing.
  - `memory_leak` → fix `restart_worker`; flushing the pool does nothing.
  - `slow_downstream` (dependency latency) → fix `enable_fallback` / raise timeout.
  Copilot's action registry contains only these operational actions; the chaos endpoints are not in the registry and are never callable by the agent. Wrong actions must be able to fail so negative memory is real. Decoys: two causes with the same symptom (500s) but different fixes.
- **B2 (demo timing): accept.** `WINDOW_S`, `INTERVAL_S`, `RECOVERY_WINDOWS` are env config; a `demo` profile uses 15 s windows and K=2 (`prod` profile 5 min, K=3). Primary metrics are time-to-correct-proposal and time-to-approve, not wall-clock resolve.
- **B3 (retain latency): accept.** A retain counts as `indexed` only after a recall for that `incident_id` returns it (bounded poll). UI shows "memory indexed" per incident; the scripted demo waits for it. Prefer Hindsight async retain if the spike confirms it.
- **B4 (metadata filtering unverified): accept.** Local `memory_index(incident_id, fingerprint, kind, source, hindsight_ref, indexed_at)` table gives exact fingerprint lookup. Hindsight recall provides similarity and reflect. Provenance is also written into the retained text (`[INC-0007 fp=ab12 source=live outcome=resolved]`) so it survives extraction.
- **B5 (self-host needs LLM): accept.** Decision: default to **local Docker Hindsight using the same Gemini key**, Cloud (promo credits) as fallback. Service count for AC1 is demo app, copilot, hindsight (3). Budget: two LLM consumers (agent + Hindsight extraction). CI uses the fake memory.
- **B6 (prompt/tool mismatch): accept.** Rewrite system prompt and tool docstrings together with the SQL tools; remove trace/PromQL/Loki language.

**Security additions** (all High items accepted)
- Demo app admin API gets a shared-secret header and is bound to the compose-internal network only; chaos endpoints separate from ops.
- **Retain policy tightened:** structured fields only; never exception *message* text (only type + top frame); RCA retained as `unconfirmed` until a human confirms; per-fingerprint influence cap on recall; a real quarantine/delete endpoint (authenticated) with a test.
- **Indirect injection through memory:** recalled items and reflect output are delimited and labelled untrusted like logs; the Approve button shows action name and params **from the server-side registry**, never LLM prose.
- **Chat endpoint is in scope:** authenticated; preference retains require explicit confirm and record the actor.
- Auth scheme (decided): one operator bearer token in an `Authorization` header (pasted once, kept in memory not localStorage), which replaces cookie-based CSRF machinery; Discord link carries no token. Ingest keys are bound to a service (`key → service`); mismatched service rejected.
- Abuse caps: max open incidents per service, max agent runs per hour, chat rate limit. FastAPI `/docs` disabled on the copilot. `Demo/.env` secret rotated and repo history checked before any public push.
- Redaction is best effort; retain content comes from structured fields. Data-handling note added to README (Hindsight and the LLM provider are third-party processors).

**Edge cases and logic**
- Run with a single worker (`--workers 1`, documented); detection and diagnosis split via an in-process job queue so the LLM never blocks the watcher.
- **Fingerprint** per (service, route, breach kind, exception type + top frame). One incident owns a primary fingerprint; other breaching fingerprints attach as evidence (no splitting when a second route starts breaching). **Hysteresis** uses separate open and close thresholds.
- A recurrence is a **new incident linked via `recurrence_of`** (no REOPENED state), so MTTR and the learning curve stay clean.
- "Unknown" (below `MIN_REQUESTS`) is **not** healthy; it blocks RESOLVED.
- Windows are computed from `received_at`; `ts` is display-only and rejected if more than 5 min in the future. Event IDs are namespaced by service.
- **Outbox:** claim/lease column, max-attempts dead letter visible in UI, per-incident ordering (feedback retain never precedes its incident retain), supersede marker for corrections instead of contradicting duplicates.
- **Action journal:** intent written before the remediation call and result after; restart never re-fires an action. A timed-out call that actually succeeded is reconciled by the recovery check.
- Discord notifications de-duplicated per incident across restarts. `NullMemory` also suppresses reflect text in prompts. Fix `faults.py` log/gauge bugs when touching that file.

**Testability:** AC rewrites in §12; adversarial tester briefs in `docs/TESTER_BRIEFS.md`.

### 11.3 Scope cuts and rejections
- **Cut (deferred, won't build unless everything else is done):** change correlation, noise learning, dark/light toggle, scheduled reflect (on-demand only), CI compose smoke test, coverage targets, separate per-service key management (one config map is enough), cookie CSRF, separate auto post-mortem (the reflect runbook card serves).
- **Simplified:** state machine is OPEN, ACKED, REMEDIATING, MONITORING, RESOLVED, SUPPRESSED. One `TestMemory` class serves Null/Fake roles.
- **Kept against reviewer suggestion:** tester round for M4, but restricted to XSS/auth/races (the framework exists for exactly this kind of live-only defect). Tester rounds: M1, M3, M4-lite.
- **Rejected concern:** tagging Code.in (content guide) conflicts with the no-event-reference rule; it does not, tagging does not require one. The compliance check does expand to commit messages, branch names, image names, Docker labels, video.
- **Ordering fixed:** M0 now includes a 1-hour **Hindsight spike** (retain → recall round trip, latency, tags/metadata, document id, delete) and the fault/action redesign; thin memory UI is built alongside M3, not after.

## 12. Revised acceptance criteria (replaces §8 table wording)
| ID | Objective, testable criterion |
|---|---|
| AC1 | `docker compose config --services` lists ≤3 services and none of prometheus, loki, tempo, grafana, otel-collector, redis |
| AC2 | No/wrong/wrong-length key → 401 with identical body; key for service A posting service B → rejected; 501-event batch and >1 MB body → 413/422; 5 MB message stored ≤4096 bytes; duplicate id → 200 and row count unchanged; no 500s |
| AC3 | Fault on for 10 watcher cycles → `count(incidents WHERE fingerprint=?)`=1; RESOLVED only after exactly K healthy windows each with ≥`MIN_REQUESTS` |
| AC4 | First incident: recall returns 0 items above threshold and `matches[]` empty; after resolve, recall for the incident id returns it within 60 s and UI shows "memory indexed" |
| AC5 | Second occurrence: `matches[]` contains INC-1; `proposed_action.name` equals INC-1's applied action; tool calls ≤ INC-1's; alert text contains INC-1's MTTR |
| AC6 | Defined decoy pair: same symptom, different cause. `matches[]` contains a `source=seed` item; proposed action follows the *verified* cause, not blindly the recalled one |
| AC7 | After feedback "RCA wrong, actual cause Y", the next incident with that fingerprint states cause Y and does not propose the rejected action first |
| AC8 | `kill -9` during MONITORING → state unchanged after restart and incident resolves; Hindsight stopped 2 min → banner shown, outbox pending >0 then 0 after restore, exactly one retained item per incident |
| AC9 | Remediation only via registry after approval; failed remediation retained as `failed_fix`, never as success; restart mid-REMEDIATING does not re-fire the action |
| AC10 | Three people unfamiliar with the project each state, within 60 s of the demo, the problem, the prior incident and the fix (results recorded in `docs/TEST_LOG.md`) |
| AC11 | Injection payloads (see briefs) cannot create an action, alter the registry or appear in retained content; `<script>` in a log renders inert |
| AC12 | `bench_learning.py` with fixed seeds: ≥3 seeds, decoys, hidden fault→cause mapping, memory-off gets same tools and budget, includes a "wrong memory" condition, per-run JSON published, chart with error bars, same seed reproduces within ±5% |
| AC13 | `git grep -iE 'hack[a]thon'` and `git log --all -i -E --grep='hack[a]thon'` empty; image names, labels, branch names clean; gitleaks clean; no DBs/.env/pyc tracked |
| AC14 | Checklist of published URLs (article and post per member, team YouTube video, repo), each opened and verified |

## 13. Extra judging points (ranked by gain per hour, after review)
1. Distinct causes/actions/decoys (B1) — credibility for Innovation and Impact.
2. Recall → hypothesis → verify trace and "why this suggestion" panel with provenance (`live`/`seed`, similarity).
3. Negative memory shown in the demo script (wrong fix tried, remembered, avoided).
4. Calibrated confidence ("confirmed 4 of 5") from the local feedback table.
5. Honest benchmark (AC12) with decoys; a curve that hits 100% at incident 2 would look rigged.
6. One-button scenario runner with reset (live-demo safety).
7. Reflect runbook card per fingerprint with a diff against its previous version.

## 14. M0 results (2026-09-28)

### 14.1 Hindsight facts, now verified from the installed client (`hindsight-client`) and the image
- **Write path:** `retain(bank_id, content, timestamp, context, document_id, metadata, entities, tags, update_mode, retain_async, operation_id)`. `update_mode` is `'replace'` or `'append'` for an existing document id, so retain-by-`incident_id` is an upsert.
- **Async + confirmation:** `retain_async=True` with a caller-supplied `operation_id` (idempotent retries) and `operations.get_operation_status(bank_id, operation_id)`. **This replaces the "poll recall to confirm indexed" workaround in B3**: "memory indexed" is read from operation status.
- **Read path:** `recall(bank_id, query, types, max_tokens, budget, tags, tags_match['any'|'all'|'any_strict'|'all_strict'|'exact'], min_scores, temporal_window, ...)`. Each `RecallResult` has structured `scores`, `tags`, `metadata`, `document_id`, `occurred_*`, so **provenance is read from fields, not parsed from text** (B4 simplified). Exact fingerprint lookup = tag `fp:<fingerprint>` with `tags_match='all_strict'`.
- **Reflect:** `reflect(bank_id, query, budget, context, response_schema, tags, tags_match, ...)` returns `ReflectResponse(text, structured_output, based_on, ...)`. `response_schema` gives structured output for the runbook card.
- **Delete / quarantine:** `documents.delete_document(bank_id, document_id)`, plus `delete_bank`, `clear_bank_memories`.
- **Operator preferences:** `create_directive(bank_id, name, content, priority, is_active, tags)` (hard rules, e.g. "never restart_worker at peak"). Better fit than retaining preferences as ordinary memories.
- **Runbook cards:** `create_mental_model(bank_id, name, source_query, tags, trigger)` with `get_mental_model_history` gives versioned standing answers, so the "runbook card with a diff against the previous version" is native.
- **Bank config:** `create_bank(..., reflect_mission, retain_mission, enable_observations, ...)`.
- **Self-host env (from the image):** `HINDSIGHT_API_LLM_PROVIDER` (**default `openai`, must be set to `gemini`**), `HINDSIGHT_API_LLM_MODEL`, `HINDSIGHT_API_LLM_API_KEY`, `HINDSIGHT_API_LLM_MAX_CONCURRENT`, `HINDSIGHT_API_LLM_TIMEOUT`; embeddings default to local `BAAI/bge-small-en-v1.5` (first-start download) or `HINDSIGHT_API_EMBEDDINGS_PROVIDER=google` (valid values: local, onnx, tei, openai, openai-codex, openrouter, requesty, cohere, google, zeroentropy, litellm, litellm-sdk; `gemini` is rejected at startup, found in the spike). Its config references models `gemini-3.5-flash`, `gemini-3.1-flash-lite`; **the repo's `gemini-3.5-flash-lite` is not among them, verify with the real key.** API 8888, UI 9999, data volume `/home/hindsight/.pg0`. No built-in auth seen: bind to localhost or the private compose network only.

### 14.2 Measured in the spike (2026-09-28, local Docker Hindsight, free-tier Gemini key)
- Works end to end: bank create, retain, recall. Retain of one ~300-char incident took **7.8 s** (sync, includes LLM fact extraction); recall took **0.41 s** and returned the correct memory with the correct text.
- **Gemini free tier is not viable as Hindsight's LLM.** `gemini-3.5-flash`: `generate_content_free_tier_requests limit: 5` per minute per model, context-cache quota 0, Hindsight retried 4 times then failed the retain (HTTP 500 from Hindsight). `gemini-3.1-flash-lite`: succeeded once, then `503 UNAVAILABLE: model experiencing high demand` on about half of calls.
- Consequences: (1) the outbox with retry/backoff (§11.2) is mandatory, not optional; (2) sync retain latency is seconds, so retain is async and the UI shows "memory indexed" from operation status; (3) the agent LLM has the same free-tier exposure and must be chosen with the same care; (4) Hindsight supports Groq natively (default model `openai/gpt-oss-120b`, the model the event guideline recommends) via `HINDSIGHT_API_LLM_PROVIDER=groq`; (5) paid Gemini or Hindsight Cloud are the other routes.

### 14.2a Measured on Groq (`openai/gpt-oss-120b`, free `on_demand` tier, local embeddings), same day
| Question | Result |
|---|---|
| Q1 retain / recall | retain 3.4 to 6 s per incident (sync); recall 0.2 to 0.4 s; recall returns the right memory text |
| Q2 tag filtering | `tags=["fp:..."], tags_match="all_strict"` isolates exactly: 2 filtered results all carrying the tag, 4 unfiltered results without it. **Exact fingerprint lookup works.** |
| Q3 document upsert | `retain(document_id=..., update_mode="replace")`: memory count unchanged (6 to 6), corrected text present, old text gone. **Idempotent by incident id works.** |
| Q5 scores | Each result has `scores.final / reranker / semantic / keyword`. Unrelated query: `final` about 1e-5 (semantic still 0.34 to 0.46, so **do not threshold on semantic**). Relevant hit: `final` 0.98. Use `final` (or `min_scores`) for the "no close match" cut-off. Provenance is available from `tags` and `document_id`. |
| Q6 delete | `documents.delete_document` deleted the document and its memory units (1, then 3 in later runs); recall by tag then returned 0. **Quarantine works.** |
| Q4 async retain | On `openai/gpt-oss-20b`: returns in 0.01 s with an `operation_id`; status `pending` then `completed` at **3.1 s**; recallable at **3.2 s**. (On `gpt-oss-120b` it stayed `pending` during provider cooldown.) |
| Q7/Q8 directive + reflect | Directive created and listed. **Reflect did not complete on any free-tier Groq model tried** (`gpt-oss-120b`, `qwen/qwen3.8-27b` at 7000 input tokens/min, `gpt-oss-20b`): it fails on iteration 1 with 429 (`scope=reflect_tool_call`), because it is an agentic loop that re-sends a growing context, and retain plus consolidation share the same per-minute budget. |

Note on API shape: the client's sub-APIs (`operations`, `documents`, `directives`) are **async coroutines**; only the top-level verbs (`retain`, `recall`, `reflect`, ...) have sync wrappers. Our FastAPI app should use the async client (`aretain`, `arecall`, `await client.operations...`) throughout.

**Model availability and buckets (measured):** the key's chat models are `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`, `qwen/qwen3.8-27b`, `allam-2-7b` (`llama-3.3-70b-versatile` returns `model_not_found`). Limits and Hindsight's cooldown are **per model**, so switching the retain model to `openai/gpt-oss-20b` avoided the 27-minute cooldown on `gpt-oss-120b` immediately. **Chosen for retain: `openai/gpt-oss-20b`** (3 s async retain, recallable immediately).

**Reflect decision:** Hindsight `reflect` and mental-model refresh are treated as an optional, on-demand enhancement, not something the live demo depends on. Runbook cards are instead built by our own agent LLM call over `recall` results (we control the token use and can fall back). Also consider `enable_observations=false` on the bank to stop the extra `scope=consolidation` LLM calls after each retain. If a paid tier is available, reflect can be re-enabled without design changes.

**Operational hazard found: provider quota cooldown.** Groq free tier on `gpt-oss-120b` allows 8,000 tokens per minute; one retain requests about 3,000 to 4,000, and reflect makes several calls. After 429s Hindsight parks the work and reports a cooldown (`retry at` about 27 minutes ahead on this run; config knob `HINDSIGHT_API_BACKPRESSURE_DEFER_SECONDS`, default 120, does not explain the whole delay), leaving retains `pending`. A single burst can therefore freeze memory writes for a long time. Mitigations adopted: (1) our outbox paces retains (one in flight, at most about 1 per 40 s on this tier) and honours `retry at`; (2) seed history is **pre-built and imported** (`export_bank` / `import_bank`) instead of retained live; (3) per-operation LLM overrides exist (`HINDSIGHT_API_RETAIN_LLM_*`, `REFLECT_LLM_*`, `CONSOLIDATION_LLM_*`), so reflect gets a different model and its own token budget (compose wires `HINDSIGHT_REFLECT_LLM_MODEL`); (4) the UI shows "memory indexing" / "memory delayed" from operation status; (5) if the free tier still proves too tight, the Groq Dev tier or Gemini billing removes the limit.

### 14.2b Still open
Time for an async retain to complete (needs a run with an unexhausted quota), reflect quality and latency (try `HINDSIGHT_REFLECT_LLM_MODEL=llama-3.3-70b-versatile` after the cooldown clears), whether directives change reflect output, and whether the agent LLM (ADK) works reliably on Groq via LiteLLM. Scripts: `spike/hindsight_spike.py` (Q1 to Q3, Q5) and `spike/hindsight_spike2.py` (Q4, Q6, Q7).

### 14.3 Design amendments from these facts
- §3.1: tags carry `fp:<fingerprint>`, `svc:<service>`, `source:live|seed`, `kind:<incident|feedback|failed_fix>`; `metadata` carries `incident_id`, `outcome`, `mttr_s`. Local `memory_index` table stays only for offline/ordering and the UI, not as the source of exact lookup.
- §3.2/§11 B3: indexing confirmation via `operations.get_operation_status`.
- §3.4: reflect output stored as mental models (versioned); operator preferences stored as directives.
- Quarantine endpoint = `delete_document` on the incident's `document_id`.

### 14.4 Demo app delivered in M0 (all verified live in the real container, see 14.5)
- Causes `pool_exhausted`, `bad_config`, `bad_deploy` (decoy of `bad_config`), `memory_leak`, `slow_downstream`; five actions each clearing exactly one cause; wrong actions genuinely fail (`restart_worker` only mitigates a pool leak, `rollback_config` does nothing for `bad_deploy`).
- Chaos (`/chaos/*`, `CHAOS_KEY`) separated from operations (`/ops/*`, `OPS_KEY`); both fail closed, constant-time compare, distinct keys; old unauthenticated `/admin/faults` removed; Swagger/OpenAPI disabled.
- `ops/state` exposes release, config revision, pool usage, leaked MB, fallback flag, worker uptime; the same facts are in each fault log line (the only evidence separating the decoy pair).
- Todo UI stored XSS fixed (DOM APIs + `textContent`); container runs non-root, single worker, port bound to 127.0.0.1.
- Repo hygiene: root `.gitignore`, secrets/DBs/pyc/local files untracked, history scrubbed and force-pushed, event-specific wording removed from tracked docs and config, CI workflow (`Demo` tests, hygiene greps, gitleaks).

### 14.5 M0 verification record
| Check | Method | Result |
|---|---|---|
| Cause/action matrix (5x5), pool/memory/latency mechanics, decoy pair, bounds | pytest (`Demo/tests/test_faults.py`) | pass |
| Auth: missing/wrong/wrong-length/non-ASCII key, unset key, key swap, 422s, no effect leak | pytest with TestClient (`Demo/tests/test_admin.py`) | pass; 87 tests in total, 0 failing |
| Real container, real HTTP | `docker run` of the built image | non-root user; `/docs`, `/openapi.json`, old `/admin/faults` all 404; chaos without key 401; ops call with the chaos key 401 |
| Decoy pair live | 40 POSTs per phase | ~40% failing under `bad_deploy` (16/40); wrong fix `rollback_config` still 12/40 failing; `rollback_release` 0/40 |
| Pool live | 14 POSTs | 10 succeed then 503; a different route also 503; `restart_worker` mitigates (10 more OK, then 503 again); `flush_pool` fixes |
| Memory leak live | 155 POSTs | 149 OK then 500s; `flush_pool` no effect; `restart_worker` fixes |
| Slow downstream live | timing | 3.3 s then 0.007 s after `enable_fallback` |
| XSS | jsdom against old (git) and new page with a hostile title | old: 2 injected elements; new: 0, title rendered as text |
| Hygiene | local run of CI greps | tree, commit messages, tracked forbidden files all clean |

Limitations recorded honestly: these were verified by the developer, not yet by an independent Tester instance (rounds start at M1 per §7); the browser tool in this environment blocks `127.0.0.1`, so XSS was checked in jsdom rather than a real browser; CI has not run on GitHub yet; old history blobs still contain pre-rename wording (checks are HEAD-based; squash-rewrite again before submission if the history itself must be clean).
