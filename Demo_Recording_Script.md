# Demo Recording Script & Commands

Internal working doc — not for publishing. Everything below was run live and verified today
(2026-09-29) against this exact repo state. Timings mirror the video script table in
`Content_Plan_and_Drafts.docx` section 15. Read the **Gotchas** section once before recording;
each one there cost real time to find.

Two terminals recommended: **Terminal A** for the commands below, **Terminal B** running a
continuous traffic loop (see Gotcha #1) muted/cropped out of the recording frame.

---

## Exact narration script

Word-for-word, ~470 words / about 3 minutes at a natural pace. This is a scaffold, not a
teleprompter -- read it once or twice, then say it in your own words on the actual take (the
video checklist itself says not to read a script verbatim, and it sounds better that way).
Bracketed lines are actions to perform on screen, not to say out loud.

> **[0:00 -- 0:30] Intro**
> "Hey, I'm Satwik. This is an incident-response agent I built with a memory layer on Hindsight.
> Here's the problem it's solving: when a service breaks at 3 a.m., the first twenty minutes
> usually don't go to fixing anything -- they go to figuring out where to even look. Half the
> time, someone already knows the answer, from an almost identical incident weeks ago that's
> sitting in nobody's searchable memory. This agent remembers those outages, so it doesn't start
> from zero every time."
>
> *[Show: README, architecture diagram]*

> **[0:30 -- 1:00] The problem**
> "Let me show you the problem first, with memory turned off. I'll inject a fault -- a bad
> deploy that raises a database error on this route.
>
> *[inject bad_deploy]*
>
> Here's the alert and the diagnosis: four tool calls, about eleven thousand tokens, and it
> correctly finds the bad release. Now watch what happens when the exact same fault comes back.
>
> *[approve fix, resolve, re-inject bad_deploy]*
>
> Same diagnosis, same four calls, roughly the same cost again. It has no idea it's seen this
> before."

> **[1:00 -- 1:45] Incident 1, with memory on**
> "Now with memory switched on. Same kind of fault, a fresh incident.
>
> *[inject bad_deploy]*
>
> The opening alert fires immediately -- it never waits on the model. The diagnosis follows a
> few seconds later: two tool calls, about six and a half thousand tokens, correctly pointing at
> the bad release.
>
> *[approve fix via ops endpoint]*
>
> I approve the fix here, and once traffic looks healthy again, the incident resolves on its own
> and the record gets retained.
>
> *[show Hindsight UI, port 9999]*
>
> That's the new memory -- symptoms, root cause, the fix that worked, tagged to this incident's
> fingerprint. This is exactly what a runbook should be, except nobody had to write it down."

> **[1:45 -- 2:30] Incident 2: the before/after moment, then the decoy**
> "Here's where it gets interesting. Same fault, one more time.
>
> *[re-inject bad_deploy]*
>
> This time the agent recognizes it immediately: known issue, this fix worked before -- one
> model call, about five hundred and sixty tokens. Roughly twelve times cheaper than the first
> time, because it isn't re-diagnosing from scratch, it's verifying a hypothesis against live
> evidence.
>
> But memory isn't blind trust. Watch this: I inject a completely different fault -- a bad
> config change -- that happens to produce the exact same error text.
>
> *[approve fix, resolve, inject bad_config]*
>
> The agent pulls up the old memory, checks it against what's actually happening right now,
> notices the config revision doesn't match, and says so: verdict, mismatch. It refuses to reuse
> a fix that wouldn't have worked."

> **[2:30 -- 3:00] Wrap-up**
> "Two things surprised us building this. One: memory only helps if incidents actually have an
> identity -- without fingerprinting, the same problem looks like five different alerts. Two:
> free-tier token limits shaped more of this design than the model itself did. The code, the
> design doc, and the full write-up are linked below, along with how Hindsight's retain and
> recall actually work under the hood."
>
> *[Show: learning-curve / cost comparison chart]*

---

## 0. Pre-flight (run before every recording session, not on camera)

```bash
cd /path/to/incident_response_agent   # repo root, where .env lives

# 1. Containers up and stable
docker compose ps
# expect: demo, copilot, hindsight all "Up" (hindsight should NOT show "Up 1 second" repeatedly --
# that means it's crash-looping; see Gotcha #2)

# 2. Memory really on, model correct
docker compose exec -T copilot printenv MEMORY_MODE AGENT_MODEL
# expect: on / cerebras/gpt-oss-120b

# 3. Clean slate: no fault injected, no leftover incidents
CK=$(grep '^CHAOS_KEY=' .env | cut -d= -f2-)
curl -s http://127.0.0.1:8000/chaos -H "X-Api-Key: $CK"
# expect: {}
docker compose exec -T copilot python -c "
import sqlite3;c=sqlite3.connect('/app/data/copilot.db')
print('events:', c.execute('select count(*) from events').fetchone()[0])
print('incidents:', c.execute('select count(*) from incidents').fetchone()[0])
"
# expect: both 0 -- if not, run the RESET block at the bottom of this doc first
```

If anything above is wrong, fix it before recording -- do not narrate over a broken stack.

---

## 1. 0:00 -- 0:30 -- Intro

**No commands.** Talking head / voiceover only.

**Say:** who you are, what this is (an incident agent that remembers outages, built on
Hindsight), one sentence on the 3 a.m. problem (the first twenty minutes of an outage go to
finding where to look, not fixing it).

**Show:** repository README, the architecture diagram, the Hindsight-usage diagram.

---

## 2. 0:30 -- 1:00 -- The problem (memory OFF)

Shows the same fault twice costing the same, because nothing is remembered.

```bash
# Turn memory off and restart copilot to pick it up
if grep -q '^MEMORY_MODE=' .env; then
  sed -i 's/^MEMORY_MODE=.*/MEMORY_MODE=off/' .env
else
  echo "MEMORY_MODE=off" >> .env
fi
docker compose up -d copilot
sleep 3
docker compose exec -T copilot printenv MEMORY_MODE   # confirm: off

# Baseline traffic so there's a healthy "before" in the logs
for i in $(seq 1 20); do
  curl -s -o /dev/null -X POST http://127.0.0.1:8000/todos -H "Content-Type: application/json" -d "{\"title\":\"b$i\"}"
  sleep 0.4
done

# Inject the fault (start recording just before this line if you want the alert on camera)
CK=$(grep '^CHAOS_KEY=' .env | cut -d= -f2-)
curl -s -X POST "http://127.0.0.1:8000/chaos/bad_deploy/post_todos?enabled=true" -H "X-Api-Key: $CK"
```

**In Terminal B (keep running throughout):**
```bash
for i in $(seq 1 500); do
  curl -s -o /dev/null -X POST http://127.0.0.1:8000/todos -H "Content-Type: application/json" -d "{\"title\":\"i$i\"}"
  sleep 0.4
done
```

Wait ~15-30s for the watcher to open the incident, then another ~30-60s for the LLM diagnosis to
land (full diagnosis both times -- check with the query in step 3 of the pre-flight). **Show:**
the Discord alert (if wired up) and the diagnosis message; point out the model call count /
token cost.

To repeat the fault a second time for the "costs the same" beat: apply the fix
(`curl -X POST http://127.0.0.1:8000/ops/rollback_release -H "X-Api-Key: $OK"`), keep Terminal
B's traffic running until `status` flips to `RESOLVED`, then re-inject the same `bad_deploy`
chaos command again -- with memory off it will run full diagnosis again at the same order of
magnitude cost, no recall.

**Verified live today:** occurrence 1 = 4 calls / 10,954 tokens; occurrence 2 (same fault, memory
still off) = 4 calls / 14,403 tokens, `recalled_json: None` both times. No discount at all --
that's the point of this segment.

**When done with this segment**, turn memory back on before segment 3:
```bash
sed -i 's/^MEMORY_MODE=.*/MEMORY_MODE=on/' .env
docker compose up -d copilot
sleep 3
docker compose exec -T copilot printenv MEMORY_MODE   # confirm: on
```
(`MEMORY_MODE` will already exist in `.env` by this point since the block above created it, so
the plain `sed` here is fine.)
Then run the **RESET block** at the bottom before continuing -- this segment's incidents must
not leak into the "fresh" Incident 1 below.

---

## 3. 1:00 -- 1:45 -- Incident 1 with memory (fresh, full diagnosis)

Memory is ON, environment is reset (events/incidents/outbox empty, no old memories in the
`incident-response` Hindsight bank -- see RESET block if unsure).

```bash
# Baseline traffic
for i in $(seq 1 20); do
  curl -s -o /dev/null -X POST http://127.0.0.1:8000/todos -H "Content-Type: application/json" -d "{\"title\":\"b$i\"}"
  sleep 0.4
done

# Inject the fault
CK=$(grep '^CHAOS_KEY=' .env | cut -d= -f2-)
curl -s -X POST "http://127.0.0.1:8000/chaos/bad_deploy/post_todos?enabled=true" -H "X-Api-Key: $CK"
```

Keep Terminal B's traffic loop running. Watch for the incident (~15-30s):
```bash
docker compose exec -T copilot python -c "
import sqlite3;c=sqlite3.connect('/app/data/copilot.db');c.row_factory=sqlite3.Row
r = c.execute('select id,status,diagnosis_mode,proposed_action,llm_calls,llm_tokens from incidents order by seq desc limit 1').fetchone()
print(dict(r) if r else 'none yet')
"
```
Expect `diagnosis_mode: full`, `proposed_action: rollback_release`.

**Approve the fix (on camera):**
```bash
OK=$(grep '^OPS_KEY=' .env | cut -d= -f2-)
curl -s -X POST "http://127.0.0.1:8000/ops/rollback_release" -H "X-Api-Key: $OK"
```

Keep traffic flowing (Terminal B) so healthy windows accumulate, then poll for resolution:
```bash
docker compose exec -T copilot python -c "
import sqlite3;c=sqlite3.connect('/app/data/copilot.db');c.row_factory=sqlite3.Row
r = c.execute(\"select status,memory_status from incidents order by seq desc limit 1\").fetchone()
print(dict(r))
"
```
Expect `status: RESOLVED`, `memory_status: indexed` within ~30-40s of continuous traffic.

**Show:** the incident going OPEN -> RESOLVED, then the Hindsight UI at `http://localhost:9999`
-- point at the new memory for this incident (tags include `fix:rollback_release`).

---

## 4. 1:45 -- 2:30 -- Incident 2: before/after, then the decoy

**Same fault again** -- this is the "it remembered" moment:
```bash
CK=$(grep '^CHAOS_KEY=' .env | cut -d= -f2-)
curl -s -X POST "http://127.0.0.1:8000/chaos/bad_deploy/post_todos?enabled=true" -H "X-Api-Key: $CK"
```
Keep Terminal B running. Poll the same way as above. Expect within ~15-25s:
`diagnosis_mode: verify`, `proposed_action: rollback_release`, and **far fewer tokens** than
Incident 1 (today's live run: 1 call / 564 tokens vs. 2 calls / 6,497 tokens on the first
occurrence). **Show:** the recalled memory with its score/provenance, and the token/call
comparison side by side.

Approve and let it resolve, same as before:
```bash
OK=$(grep '^OPS_KEY=' .env | cut -d= -f2-)
curl -s -X POST "http://127.0.0.1:8000/ops/rollback_release" -H "X-Api-Key: $OK"
```
(poll for `status: RESOLVED` again, keep traffic flowing)

**Now the decoy** -- same error text, different real cause:
```bash
CK=$(grep '^CHAOS_KEY=' .env | cut -d= -f2-)
curl -s -X POST "http://127.0.0.1:8000/chaos/bad_config/post_todos?enabled=true" -H "X-Api-Key: $CK"
```
Poll the same way. Expect `diagnosis_mode: verify`, `proposed_action: None`, and the `rca_text`
column will contain `Verdict: mismatch` with the reasoning ("runtime context differs... config_rev
cfg-r42 vs cfg-r41"). **Show:** this exact RCA text -- this is the "it noticed and refused to
reuse the old fix" beat.

Clean up the decoy before wrap-up:
```bash
OK=$(grep '^OPS_KEY=' .env | cut -d= -f2-)
curl -s -X POST "http://127.0.0.1:8000/ops/rollback_config" -H "X-Api-Key: $OK"
```

---

## 5. 2:30 -- 3:00 -- Wrap-up

**No commands.** One takeaway, one surprise (suggested in the doc: memory only helps if
incidents have identity and a lifecycle; free-tier token limits shaped the design more than
expected). **Show:** the learning-curve / cost comparison chart.

---

## Gotchas found during today's dry run (read before recording)

1. **Traffic must be continuous, not a burst.** `healthy_windows` only increments on cycles
   with enough traffic on that route (`MIN_REQUESTS`, currently 10). Fire a burst of requests
   then stop, and the incident gets stuck OPEN indefinitely with `healthy_windows` frozen at 1
   -- it looks broken but it's just waiting for real traffic. Always keep Terminal B's loop
   running through every "wait for resolution" step above.

2. **Hindsight needs its own LLM key.** Separate from the main agent's `CEREBRAS_API_KEY`,
   Hindsight itself needs `HINDSIGHT_LLM_API_KEY` (+ `HINDSIGHT_LLM_PROVIDER`,
   `HINDSIGHT_LLM_MODEL`) in `.env`, or its container crash-loops with
   `ValueError: LLM API key is required`. If `docker compose ps` shows hindsight's uptime
   resetting to a few seconds repeatedly, this is why -- check `docker compose logs hindsight`.

3. **A stale Hindsight bank breaks "Incident 1: full diagnosis."** If a previous test run left
   memories in the real `incident-response` bank under the same fingerprint, injecting the fault
   again will hit the `verify` (recall) path immediately instead of a fresh `full` diagnosis --
   wrong beat. Always run the RESET block below before Incident 1's segment.

4. **A container restart does not clear Hindsight's memory** -- only the local SQLite events/
   incidents tables get cleared by a DB wipe. Hindsight's bank is a separate persistent store;
   clear it explicitly (RESET block below) or old incidents accumulate across takes.

5. **`MEMORY_MODE` toggles need a container recreate, not just an env file edit** --
   `docker compose up -d copilot` after editing `.env` is enough (no rebuild needed unless code
   changed).

6. **`sed -i 's/PATTERN/.../' .env || fallback` does not detect "no match."** GNU `sed` exits 0
   even when nothing matched, so a `||`-chained fallback to append a missing line never fires --
   the file silently stays unchanged. Always check for the line's existence with `grep -q` first
   (as the commands in this doc now do), not a bare `sed ... || echo ...` one-liner.

---

## RESET block (run before every fresh take of "Incident 1", or between takes)

```bash
cd /path/to/incident_response_agent
OK=$(grep '^OPS_KEY=' .env | cut -d= -f2-); CK=$(grep '^CHAOS_KEY=' .env | cut -d= -f2-)

# Clear any injected fault and apply both possible fixes so app state is clean either way
curl -s -o /dev/null -X POST http://127.0.0.1:8000/ops/rollback_release -H "X-Api-Key: $OK"
curl -s -o /dev/null -X POST http://127.0.0.1:8000/ops/rollback_config -H "X-Api-Key: $OK"
curl -s -o /dev/null -X POST http://127.0.0.1:8000/chaos/reset -H "X-Api-Key: $CK"

# Clear local events/incidents/outbox
docker compose exec -T copilot python -c "
import sqlite3;c=sqlite3.connect('/app/data/copilot.db')
[c.execute('delete from '+t) for t in ('events','incidents','incident_events','outbox')];c.commit()
print('local db cleared')"

# Clear this take's incident memories from the real Hindsight bank (adjust IDs to whatever
# this session actually created -- check with the query below first)
docker compose exec -T copilot python -c "
from hindsight_client import Hindsight
client = Hindsight(base_url='http://hindsight:8888')
hits = client.recall(bank_id='incident-response', query='incident', tags=['kind:incident'], tags_match='all_strict', max_tokens=500)
for item in (getattr(hits,'results',None) or []):
    print(getattr(item,'document_id',None))
" 2>&1 | grep -v "Unclosed\|connections\|connector\|RuntimeWarning\|Enable tracemalloc"
# ^ lists current memory doc IDs; delete each with:
docker compose exec -T copilot python -c "
from hindsight_client import Hindsight
from hindsight_client.hindsight_client import _run_async
client = Hindsight(base_url='http://hindsight:8888')
for doc_id in ['INC-XXXX']:   # <-- replace with the IDs printed above
    try:
        _run_async(client.documents.delete_document(bank_id='incident-response', document_id=doc_id))
        print('deleted', doc_id)
    except Exception as e:
        print('note', doc_id, type(e).__name__)
" 2>&1 | grep -v "Unclosed\|connections\|connector\|RuntimeWarning\|Enable tracemalloc"

echo "chaos: $(curl -s http://127.0.0.1:8000/chaos -H "X-Api-Key: $CK")"   # expect {}
```
