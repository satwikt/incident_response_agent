# Open Defects and Carried-Forward Items

Single list of everything not yet fixed or verified as of the M0 + M1 push (2026-09-28). Evidence and history are in
[`TEST_LOG.md`](TEST_LOG.md); accepted trade-offs are described in [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).
Severity: High = wrong result or exposure in the intended use, Med = wrong in a plausible case, Low = cosmetic or
edge. "Blocks" says which milestone cannot be called done until the item is closed.

## Defects

| ID | Sev | Description | Found by | Owner / blocks | Status |
|---|---|---|---|---|---|
| S1 | Med | Copilot chat endpoints (`/chats`, `/chat/*`, `/ask/*`) have no authentication, so anyone who can reach the port can read chats and spend LLM budget. Ports are bound to 127.0.0.1 as the mitigation. | Design review (DESIGN section 11) | M4 (operator token) | Open, mitigated by localhost binding |
| S2 | Med | The chat UI renders LLM and user text into the DOM through `innerHTML` (`renderMarkdownLite`), a stored-XSS surface once log-derived text reaches the chat. | Code reading during M1 | M4 (escape, CSP header) | Open |
| S3 | Low | The ingest endpoint has no body-read timeout: a client that declares a body and sends it very slowly holds a connection open. | Sonnet security round | Deployment (reverse proxy timeouts) | Accepted for the local demo |
| S4 | Low | Unauthenticated requests to ingest are not rate limited (the limiter is per authenticated service; no lock-out risk was found). | Sonnet security round | Later | Accepted |
| S5 | Low | Demo app 422 responses echo the offending input and send `Server: uvicorn` (FastAPI defaults). Demo app only. | Sonnet security round | Later | Accepted |
| T1 | Low | Telemetry is best effort during a Copilot outage: the demo emitter drops a batch after 3 failed attempts (about 88 of about 200 events arrived after a 30 s outage). | Sonnet security round | Optional durable spool | Accepted |
| T2 | Low | Health is traffic-based. `idle` means no events for 5 minutes and is not an outage; a silent crash needs a heartbeat event to be detected as `down`. | Design decision | Optional | Accepted |

## Verification gaps and gates

| ID | Item | Blocks | Plan |
|---|---|---|---|
| O2 | ~~Agent LLM on Groq untested~~ **Closed with caveat (see DESIGN 15.2):** 18 of 20 completed, 0 function-calling errors; the limit is provider quota. | none | none |
| O13 | **Groq free tier: 200,000 tokens per day per model** (about 20 diagnoses). Daily-budget guard added; seed history cannot be retained live in one day. | M3 (seed bank), demo day | Build the seed bank in batches, use a paid tier, or spread across models; run the preflight check before any demo |
| O14 | Effect of `reasoning_effort=low` and the reply cap on tokens per diagnosis is **unmeasured** (quota exhausted). | Article B numbers | Re-run the agent gate on fresh quota |
| O15 | M2 independent tester rounds not yet run (briefs ready in TESTER_BRIEFS.md). | M2 gate | Run Opus functional and Sonnet security-style rounds |
| O16 | Ideas from the team kickoff not yet built: memory-first fast path, Slack/Teams renderers, integrations documentation, preflight script. | M3 to M5 | See KICKOFF_INSIGHTS.md |
| O3 | Hindsight `reflect` does not complete on any free Groq model tried (token-per-minute limits). Runbook summaries will come from our own LLM call over recall results. | M3 (optional feature) | Re-test on a paid tier; consider `enable_observations=false` |
| O4 | Effect of Hindsight directives on reflect is unverified (depends on O3). | M3 (operator preferences) | Fall back to tagged preference memories |
| O5 | XSS fix in the Todo UI was checked in jsdom, not a real browser (the browser tool blocks 127.0.0.1). | M4 tester round | Re-check in a real browser |
| O11 | Only one route per fault cause was exercised live; other route combinations rely on unit tests. | none | Optional extra live pass |
| O12 | Not tested live: the 2,000 requests/s flood, access from outside the host, a 1 MB header value, cross-service id suppression with a second key (all covered by unit tests or by localhost binding where applicable). | none | Optional |

| O17 | Med | `/chaos/reset` on the demo app clears its action journal, which can erase the record of what fixed an incident if reset is called before the async retain runs. Reproduced live. | M3/M4 | Snapshot actions into the incident at resolve time instead of reading the journal at retain time; or stop clearing the journal on chaos reset |
| O18 | Low (accepted, fail-safe) | On a verify "mismatch" the incident does not escalate to a full diagnosis; it stays open with no proposed action. Reproduced live (INC-0009, the decoy case). | M4/M5 | Needs `submit`/telemetry plumbing through `on_diagnosis_done`; deferred as too risky right before submission |
| O19 | Low (accepted) | Full diagnosis can misattribute a decoy pair that shares an exception type/status code and differs only in a global runtime field (`bad_config` vs `bad_deploy`, both mutate a single shared `FaultState` instance, so every route reflects the new value the instant the fault fires). SYSTEM_INSTRUCTION was tuned twice to make the model diff an older baseline line against the current one; the model did start calling `get_recent_logs(limit=200)` as instructed, and direct SQL against the events table confirmed the healthy baseline line (`cfg-r41`) was at rank 156 of 280 returned lines — inside that limit — yet the model still concluded "no difference" and proposed the wrong fix. Root cause is model attention over a large unstructured text dump at `reasoning_effort=low`, not a prompt-wording or limit-sizing gap, so no further prompt tuning is expected to fix it reliably. Reproduced live on both decoy directions (4 runs total, real Cerebras calls). | None (out of core demo path; only triggers if `bad_config` and `bad_deploy` are injected back to back) | A deterministic context-diff tool (query the DB directly for the before/after value of each context field, return it as structured data instead of raw log text) would remove the model's need to eyeball the diff and should be built if this path becomes demo-critical later |

## Housekeeping

| ID | Item | Plan |
|---|---|---|
| H1 | Old git history blobs still contain pre-rename wording (CI greps only the current tree and commit messages). | Squash-rewrite before submission if the history must be clean |
| H2 | `PROJECT_OVERVIEW.md` describes the old Prometheus/Loki/Tempo architecture and is marked historical. | Rewrite in M5 |
| H3 | The spike Hindsight container may still be running locally (`docker compose -f spike/docker-compose.yml down`). | Stop when not needed |
| H4 | `spike/.env` should use `HINDSIGHT_LLM_MODEL=openai/gpt-oss-20b` (the 120b model hit a 27-minute cooldown). | Owner change |
| H5 | The `Copilot/pyproject.toml` / `uv.lock` are leftovers from the original project (`name = "adk"`). | Tidy in M5 |
| H6 | Public content deliverables (article, post, video) not started. | M5 |

## Closed in M0 and M1 (for reference)
D3 (the same breach re-alerting every 15 s) is closed by the M2 incident engine: one incident, one opening alert, verified live.
CI had never run on GitHub (O10): the first run (`ci` #1, commit `6dbc150`, run 36456523551) passed all three jobs: `copilot-tests`, `demo-tests`, `hygiene` (event-wording, forbidden-files, compose-size and gitleaks checks).

Surrogate-character 500 on ingest, demo 500 leaking an internal message, Host-header redirect, slow-request alert ignoring
the minimum-request rule (D1), log ordering (D2), alert lines without a window (D4), chat cache and RAG removal,
committed secrets and databases, stored XSS in the Todo UI. Details and evidence: [`TEST_LOG.md`](TEST_LOG.md).
