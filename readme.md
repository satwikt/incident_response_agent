# Incident Response Agent

An on-call assistant that watches a running application's telemetry, notices when something breaks, diagnoses it with
live evidence, and (in progress) **remembers every incident it has handled** so the next similar failure is diagnosed
faster. Built on Google ADK, with [Hindsight](https://github.com/vectorize-io/hindsight) as the memory layer.

> **Status: work in progress.** Telemetry ingest, detection and the demo scenarios are done and tested. Incident
> lifecycle, Hindsight memory, the approval workflow and the incident UI are next. See
> [`docs/DESIGN.md`](docs/DESIGN.md) for the design, [`docs/TEST_LOG.md`](docs/TEST_LOG.md) for what has been verified,
> and [`docs/OPEN_DEFECTS.md`](docs/OPEN_DEFECTS.md) for what is still open.

## How it works today

```
demo app ──(one event per request, batched, never blocks)──▶ POST /ingest/events ──▶ SQLite
                                                                                      │
                                              watcher (every N s): error rate, p95, slow requests, keywords
                                                                                      │ breach
                                                                agent (Google ADK) + read-only tools ──▶ RCA ──▶ Discord
```

- **`Demo/`** is a small Todo API that can be broken on purpose. It has five injectable causes, each with exactly one
  correct fix and some wrong ones: `pool_exhausted` (flush_pool), `bad_config` (rollback_config), `bad_deploy`
  (rollback_release), `memory_leak` (restart_worker), `slow_downstream` (enable_fallback). `bad_config` and `bad_deploy`
  look identical except for the release and config revision in the log context.
- **`Copilot/`** stores pushed events, detects breaches, and runs the agent. Its tools are read-only.
- Two containers, no observability stack to run: applications simply push events over HTTP with an API key.

## Run it

Prerequisite: Docker Desktop.

```bash
cp .env.example .env
# fill in INGEST_KEY, CHAOS_KEY, OPS_KEY (each: openssl rand -hex 24) and, for the agent, an LLM key
docker compose up -d --build
```

- Demo app: http://127.0.0.1:8000 (Todo UI)
- Copilot: http://127.0.0.1:8001
- Both ports are bound to localhost only. The Copilot chat endpoints are not authenticated yet (see the open defects),
  so do not expose it beyond your machine.

Break something and watch the watcher react (keys from your `.env`):

```bash
curl -X POST "http://127.0.0.1:8000/chaos/bad_deploy/post_todos?enabled=true" -H "X-Api-Key: $CHAOS_KEY"
# generate some traffic, then:
docker compose logs -f copilot        # "Watcher: 1 breach(es) detected ..."
curl -X POST http://127.0.0.1:8000/ops/rollback_release -H "X-Api-Key: $OPS_KEY"   # the right fix
curl -X POST http://127.0.0.1:8000/chaos/reset -H "X-Api-Key: $CHAOS_KEY"          # back to normal
```

## Tests

No local Python needed; the suites run in a container:

```bash
docker run --rm -v "$PWD/Demo:/w" -w /w python:3.13-slim sh -c \
  "pip install -q fastapi==0.115.0 -r requirements-dev.txt && python -m pytest -q"
docker run --rm -v "$PWD/Copilot:/w" -w /w python:3.13-slim sh -c \
  "pip install -q fastapi pydantic python-dotenv -r requirements-dev.txt && python -m pytest -q"
```

CI (`.github/workflows/ci.yml`) runs both suites plus repository hygiene checks.

## Documents

| File | What |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | Architecture, memory design, security model, edge cases, acceptance criteria |
| [`docs/TEST_LOG.md`](docs/TEST_LOG.md) | What was verified live, by whom, and with what confidence |
| [`docs/OPEN_DEFECTS.md`](docs/OPEN_DEFECTS.md) | Every open defect, limitation and carried-forward item |
| [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) | Accepted trade-offs |
| [`docs/TESTER_BRIEFS.md`](docs/TESTER_BRIEFS.md) | Scenario briefs used in the independent testing rounds |
| [`spike/`](spike) | Local Hindsight setup and the spike scripts that measured its behaviour |
