# Open Defects and Carried-Forward Items

Single list of everything not yet fixed or verified as of the M0 + M1 push (2026-09-28). Evidence and history are in
[`TEST_LOG.md`](TEST_LOG.md); accepted trade-offs are described in [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).
Severity: High = wrong result or exposure in the intended use, Med = wrong in a plausible case, Low = cosmetic or
edge. "Blocks" says which milestone cannot be called done until the item is closed.

## Defects

| ID | Sev | Description | Found by | Owner / blocks | Status |
|---|---|---|---|---|---|
| D3 | Low | An ongoing breach is re-detected every watcher cycle (15 s in the demo profile), so each cycle triggers a new agent run and notification (11 in about 5 minutes observed). | Opus functional QA | M2 (incident fingerprint, dedupe, cooldown) | Open, planned |
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
| O2 | **Agent LLM on Groq through ADK/LiteLLM is untested** (function-calling reliability; the free Gemini tier was unreliable). The watcher's agent call has never run with a working model. | **M2 and M3** | Gate before M2: 20-run tool-call test; fall back to a paid tier or another model if flaky |
| O3 | Hindsight `reflect` does not complete on any free Groq model tried (token-per-minute limits). Runbook summaries will come from our own LLM call over recall results. | M3 (optional feature) | Re-test on a paid tier; consider `enable_observations=false` |
| O4 | Effect of Hindsight directives on reflect is unverified (depends on O3). | M3 (operator preferences) | Fall back to tagged preference memories |
| O5 | XSS fix in the Todo UI was checked in jsdom, not a real browser (the browser tool blocks 127.0.0.1). | M4 tester round | Re-check in a real browser |
| O10 | CI (`.github/workflows/ci.yml`) has never run on GitHub. | M1 gate | Confirm the first run after this push |
| O11 | Only one route per fault cause was exercised live; other route combinations rely on unit tests. | none | Optional extra live pass |
| O12 | Not tested live: the 2,000 requests/s flood, access from outside the host, a 1 MB header value, cross-service id suppression with a second key (all covered by unit tests or by localhost binding where applicable). | none | Optional |

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
Surrogate-character 500 on ingest, demo 500 leaking an internal message, Host-header redirect, slow-request alert ignoring
the minimum-request rule (D1), log ordering (D2), alert lines without a window (D4), chat cache and RAG removal,
committed secrets and databases, stored XSS in the Todo UI. Details and evidence: [`TEST_LOG.md`](TEST_LOG.md).
