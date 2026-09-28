# Known Limitations

Accepted, documented trade-offs. Each is a technical fact; whether it is acceptable is a product decision.

## Environment and providers
- **Free LLM tiers are the weakest link.** Gemini free tier: 5 requests/min on `gemini-3.5-flash`, frequent 503 overloads on `gemini-3.1-flash-lite`. Groq free tier: about 8,000 tokens/min per model; a burst can put Hindsight into a multi-minute cooldown for that model. Mitigations: per-model buckets, paced outbox, pre-built seed bank, paid tier if needed.
- **Hindsight `reflect` does not run on the free tier** (agentic loop exceeds the per-minute token budget). Runbook summaries are produced by our own LLM call over recall results.
- **Hindsight has no built-in auth in the local image**: bind to localhost or the private compose network only.
- **Demo state is in process memory** (fault state, pool counters): one worker only, resets on restart.

## Testing
- Browser-level XSS verification was done in jsdom, not a real browser, at M0.
- Until an independent Tester round runs (M1 onward), verification is by the Developer only.
- CI has not yet run on GitHub.

## Repository
- Older git history still contains pre-rename wording in blobs; CI greps check the current tree and commit messages only.

## Ingest and telemetry (from the M1 tester round)
- **No body-read timeout** on ingest: a client that declares a body and sends it very slowly holds a connection open. Put a reverse proxy with read timeouts in front for any real deployment; not needed for the local demo.
- **Unauthenticated requests are not rate limited.** Auth is a cheap constant-time compare and the limiter is per authenticated service, so an attacker cannot lock the real key out (tested: 300 wrong-key requests, then a valid request succeeds).
- **Telemetry is best effort during a Copilot outage.** The demo emitter drops a batch after 3 failed attempts; in a 30 s outage about 88 of about 200 events arrived later. Detection resumes as soon as traffic and the Copilot are back. A durable spool is a possible later improvement.
- **Lenient parsing by design:** level is case-insensitive, a numeric string is coerced for `duration_ms`, unknown fields are ignored, Content-Type is not enforced. Type errors in `message`/`exception`, invalid ids, routes, timestamps and statuses are rejected (422, whole batch, nothing stored).
- **Health is traffic-based.** `idle` means no events in 5 minutes and is deliberately not an outage; detecting a silent crash would need a heartbeat event.
- **Demo app 422 responses** echo the offending input and the `Server: uvicorn` header is present (FastAPI/uvicorn defaults). Only the demo app is affected.
- **Plain HTTP only**; TLS termination is out of scope.
- **Copilot chat endpoints are unauthenticated and the chat UI renders text through innerHTML.** Both are scheduled for the M4 UI/auth work; do not expose the Copilot beyond localhost until then.
- **Alert repetition (until M2):** an ongoing breach is re-detected every watcher cycle and triggers a new agent run and notification each time. Incident fingerprinting, dedupe and cooldown are the M2 deliverable.

The full list of open defects and gates is in [OPEN_DEFECTS.md](OPEN_DEFECTS.md).
