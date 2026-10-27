# Demo test flow (hands-on checklist)

Everything below was run end to end on the live Docker stack before this checklist was written. Timings are what was
measured; the model calls go to NVIDIA NIM (20-40 s per RCA answer is normal).

## 0. Start clean

```powershell
.\scripts\reset_demo.ps1            # fresh take: no incidents, no chats, no memory  (add -KeepMemory to keep memory)
```

Wait until Hindsight is ready (~30 s, up to a few minutes on a slow start): `docker compose logs hindsight --tail 5` shows
`Hindsight is running`, or just retry step 3 until memory answers.

| What | URL |
|---|---|
| Storefront | http://localhost:9922 |
| Faults control deck | http://localhost:9922/faults |
| Copilot chat | http://localhost:9911 |
| Incident dashboard | http://localhost:9911/dashboard |
| Grafana (admin / admin) | http://localhost:3001 |

Check: dashboard shows **0 incidents**; Memory panel is empty; `docker compose ps` shows everything Up.

## 1. First incident (expect the **ultra** tier)

1. Open the faults deck and switch **Payment Failure** on.
2. The shop's synthetic traffic keeps ordering; checkouts now fail with 500. (Optional: click through the storefront and
   check out yourself to speed it up.)
3. **Watch the dashboard.** The watcher opens an incident on its own, measured at **~35 s** after the fault was
   switched on with no manual traffic: `INC-0001`, *critical*, evidence "Error rate on 'POST /checkout' ...". Its
   automatic diagnosis (Symptom / Evidence / Root cause, `Proposed action: disable_fault`) appears within another
   ~30-60 s; until then it reads *pending*.
4. Open the chat and ask: **`why is checkout failing?`**

Expect (about 25-40 s):
- the answer has Symptom / Evidence / Root cause / Recommendation with real numbers (error %, p95) and quotes the log
  line where the fault was switched on;
- last line `Proposed action: disable_fault`;
- the reply ends with "Incident Registered: INC-0001" - **the same incident the watcher opened**, not a new one;
- the incident timeline shows `diagnosis:tier  Nemotron ultra tier (...)` (no precedent yet).

## 2. Operator feedback loop

1. In the dashboard open INC-0001 -> **Fix Failed** with `rollback_config`. Expect status **REMEDIATING** and a new
   recommendation (not `rollback_config` again).
2. Switch **Payment Failure off** on the faults deck, then **Fix Worked** with `disable_fault`. Expect **RESOLVED**.
3. Memory is written a few seconds to a minute after the resolve. Groq's free tier allows only ~2 memory writes per
   minute; if you click several times quickly Hindsight waits for the quota to reset and finishes by itself (a few
   minutes at most).
4. Dashboard -> the **Hindsight Memory Bank (N)** tab at the top (not the incident detail view): a card for INC-0001 with
   fix `disable_fault` and failed `rollback_config`. The incident detail view only shows the one-line "Procedural Fix
   Verification" panel. Wait for the card before step 3.

## 3. Repeat incident (expect the **nano** tier)

1. Switch **Payment Failure on** again, wait for the new incident (INC-0002).
2. Chat: **`why is checkout failing?`**

Expect (about 20-30 s): tier **nano** (`nemotron-3.5-lightning`), the answer cites INC-0001 and says `disable_fault`
worked and `rollback_config` did not; the incident's *recalled* list shows INC-0001.

## 4. Different fault (expect the **super** tier)

Resolve INC-0002 first (Fix Worked). Then switch **DB Pool Exhaustion** on (checkout starts returning 503) and ask:
**`checkout is returning 503 errors, what is the root cause and how do we fix it?`**

Expect tier **super** (similar service, different symptom), the 503s and the pool-exhaustion fault toggle in the
evidence, `Proposed action: disable_fault`.

## 5. Read-only questions (no incident must be created)

Ask each in a **new chat**; every one must answer in 5-15 s and leave the incident count unchanged:
- `show me the last 5 error logs for checkout`
- `show failed requests for checkout`
- `what is the p95 latency of checkout?`
- `is the frontend healthy?`

## 6. Quick checks

| Check | Expected |
|---|---|
| Blank message or 8000+ characters in chat | rejected (HTTP 422) before any model call |
| "Fix Failed" on an already resolved incident | rejected (HTTP 409) |
| `GET /api/telemetry/overview?service=ghost` | no data, not checkout's numbers |
| Chat API paths (for scripted checks) | `POST /chat/new` then `POST /ask/{id}` with `{"message": ...}`; these have no `/api` prefix, the dashboard and incident APIs do |
| A chat whose model call fails | "Agent error: ... (reference ERR-xxxxxxxx)" - no keys or provider text |

## Known limits (not bugs, plan around them)

- **Chaos key.** If `CHAOS_KEY` is set in `.env`, switching faults on or off (faults page, `/api/faults`, `/admin/faults` on
  checkout and catalog) needs it as the `X-Chaos-Key` header. The faults page asks for it once per browser session. Leave
  `CHAOS_KEY` empty for an open local setup. `reset_demo.ps1` reads `$env:CHAOS_KEY` or `-ChaosKey`.

- **`MIN_REQUESTS`** (in `.env`) is how many checkout requests the watcher's 1-minute window needs before it will call
  an error rate a breach. The shop's background traffic is only ~4 checkouts/min, so the default of 20 never opens an
  incident on its own; the demo uses `MIN_REQUESTS=3`. Apply a change with `docker compose up -d copilot`.
- After you switch a fault off, wait ~60-90 s before **Fix Worked**: the window still holds the last failing requests,
  and the watcher ignores such a tail for `RESOLVE_GRACE_S` (90 s) after an operator resolve.
- **Only `checkout` is watched.** The adapter ingests checkout events; faults on `catalog` or `frontend` (Memory Leak,
  Error Spike 503, Image Delay) do not open incidents by themselves. Asking about them in chat still works through the
  live Prometheus/Loki/Tempo tools.
- **Groq free tier** (memory model): ~7000 input tokens per minute. Memory indexing can lag a few minutes after rapid
  clicking. It never loses data; Hindsight retries. If you want to avoid it entirely for a take, set
  `MEMORY_RETAIN_FAILED_ATTEMPTS=off` in `.env` (failed actions are then stored once, with the fix, at resolution).
- **NVIDIA NIM can be slow or return a 500.** Each model call has a 55 s limit and one retry; a chat that cannot be
  answered ends in the clean error above after at most ~2 minutes. Ask again.
- The Groq **fallback** is a last resort and cannot take a long tool conversation (its token limit is too small), so a
  failed NVIDIA call during a long RCA usually ends in the clean error instead of a fallback answer.

## Troubleshooting

```powershell
docker compose ps
docker compose logs copilot --tail 40
docker compose logs hindsight --tail 20
Invoke-RestMethod -Method Post http://localhost:9922/api/faults/reset     # all faults off
```
