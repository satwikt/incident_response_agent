# Hosting the demo on a Nebius AI Cloud VM

Goal: one public, free-to-use URL that stays up until the judging window closes (15 Dec 2026), without an open model-spend
tap and without exposing internal services. Everything here is in the repo; the steps marked **VERIFY** depend on Nebius
console details that were not checked from this repo.

## What you get

| URL | What | Served by |
|---|---|---|
| `https://<DEMO_DOMAIN>/` | Copilot chat | Caddy -> `copilot:8001` |
| `https://<DEMO_DOMAIN>/dashboard` | Incident dashboard, Hindsight Memory Bank tab | Caddy -> `copilot:8001` |
| `https://<DEMO_DOMAIN>:8443/` | Astronomy shop | Caddy -> `frontend:8080` |
| `https://<DEMO_DOMAIN>:8443/faults` | Faults control deck (asks for the chaos key) | Caddy -> `frontend:8080` |

Nothing else is published: Postgres, Prometheus, Loki, Tempo, Grafana (default `admin/admin`), Hindsight and the telemetry
ingest API are reachable only inside the Docker network. Caddy answers 404 for `/ingest/*` because the adapter's default
ingest keys are not secret.

## 1. VM

- Ubuntu LTS image, Docker Engine and the Compose plugin **>= 2.24** (the override uses `!reset`). `docker compose version` to check.
- Size: the stack is 12 containers including a Postgres, Loki, Tempo, Prometheus, Grafana and Hindsight (which embeds its own
  Postgres and a local embedding model). **Sizing was not measured.** Start with 4 vCPU / 16 GB RAM / 50 GB disk, watch
  `docker stats` during a benchmark run, and adjust. **VERIFY** which Nebius AI Cloud platform and preset fit this.
- Firewall / security group: allow inbound **22** (your IP only), **80**, **443** and **8443**. Nothing else.
- Point a hostname at the VM. With no domain of your own, `<ip-with-dashes>.sslip.io` resolves to that IP and Let's Encrypt will
  issue a certificate for it, e.g. `203-0-113-5.sslip.io`. Port 80 must be reachable for the certificate challenge.

## 2. Configure

```bash
git clone <repo-url> && cd incident_response_agent
git checkout main                       # or nvidia-hackathon until it is merged
cp .env.example .env
```

Edit `.env` (it is git-ignored; never commit it):

| Variable | Value |
|---|---|
| `DEMO_DOMAIN` | the hostname from step 1 |
| `CHAOS_KEY` | a fresh random value (`openssl rand -hex 24`). Publish **this** value in the judges' testing instructions and use it nowhere else |
| `NEBIUS_API_KEY` | your Token Factory key, and `NEBIUS_NANO_MODEL` / `NEBIUS_SUPER_MODEL` / `NEBIUS_ULTRA_MODEL` from `python scripts/list_models.py` |
| `MEMORY_MODE` | `on` |
| `INGEST_KEYS`, other secrets | fresh random values; the repo defaults are not secret |
| Hindsight LLM | the Groq or Nebius option documented in `.env.example` (a Hindsight key is needed for memory) |

Cost guards (set explicitly in `.env` if you want different numbers; an `.env` value beats the defaults below):

| Setting | Prod default | Effect at the limit |
|---|---|---|
| `AGENT_MAX_RUNS_PER_HOUR` | 6 | automatic diagnoses beyond it are replaced by "Automatic diagnosis unavailable (demo budget reached: ...)" |
| `AGENT_DAILY_TOKEN_BUDGET` | 300000 | same, once the last 24 h of diagnoses used that many tokens |
| `CHAT_MAX_LLM_CALLS_PER_HOUR` | 40 | chat makes **no** model call and shows "Demo budget reached" |
| `CHAT_MAX_LLM_CALLS_PER_DAY` | 300 | same |

The chat limits are counted from stored messages, so a restart does not reset them. At a chat limit the reply is a clearly
labelled **replay** of a real recorded run when `Copilot/data/replay.json` exists (written by `scripts/benchmark_tiers.py`
from a real run; it is never invented). Without that file the reply is only the notice. These numbers are starting points,
not measured needs: size them from the benchmark's tokens-per-diagnosis once it exists.

Synthetic shop traffic (~4 checkouts per minute) was **not** lowered: the watcher needs at least `MIN_REQUESTS` (3) checkout
requests in its 1-minute window to declare a breach, so a lower rate would stop detection. It generates no model calls.

## 3. Start

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml ps
```

The first start takes several minutes (image builds, Hindsight model download). Compose refuses to start if `DEMO_DOMAIN` or
`CHAOS_KEY` is missing.

## 4. Smoke test

```bash
COPILOT_URL=https://$DEMO_DOMAIN SHOP_URL=https://$DEMO_DOMAIN:8443 CHAOS_KEY=... bash scripts/smoke_hosted.sh
```

It checks reachability, that the ingest API is not public, that fault changes without the key are refused, that a fault
produces an incident and a stored diagnosis, and (one real model call) that the chat returns a proposed action. Use
`SKIP_CHAT=1` to skip the model call. Then open the URL in a private window and walk through
[`docs/DEMO_TEST_FLOW.md`](DEMO_TEST_FLOW.md).

## 5. Run it until 15 Dec

- Containers have `restart: unless-stopped`; Docker's own start-on-boot makes the stack come back after a VM reboot.
- Logs rotate (10 MB x 3 per container). Watch disk: `df -h` and `docker system df`.
- Reset between judging sessions or after experiments: `COMPOSE_FILES="-f docker-compose.yml -f deploy/docker-compose.prod.yml" CHAOS_KEY=... bash scripts/reset_demo.sh`
  (wipes the copilot and Hindsight volumes by exact name; it also clears the memory a benchmark run created).
- Check the spend: Token Factory usage in the Nebius console **(VERIFY)** next to the `CHAT_MAX_*` and `AGENT_*` caps above.
- After 15 Dec: `docker compose down` and delete the VM so it stops billing.

## Known limits of the hosted demo

- The chat, the dashboard and the incident feedback buttons have no login: anyone with the URL can use them. The model-call caps
  and the labelled replay are what protect the budget; the chaos key is what stops strangers from breaking the shop.
- Only `checkout` is watched. Use Payment Failure, DB Pool Exhaustion and High Latency in walkthroughs.
- After **Fix Worked**, wait about 90 s before switching the same fault on again (the watcher ignores the tail of a just-resolved
  incident for `RESOLVE_GRACE_S`).
- Memory indexing depends on the memory model's rate limit; on a free tier it can lag a few minutes after rapid clicking.
