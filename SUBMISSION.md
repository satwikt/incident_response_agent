# Submission: Incident Response Copilot

> Fill the `<...>` placeholders before submitting.

## Description
Incident Response Copilot is an AI on-call teammate for a microservice stack. A background watcher reads telemetry (request errors, latency, logs, process memory) and opens an incident on its own; it does not wait to be asked. A Laya decision engine routes chat questions to an RCA agent or a query agent. The RCA agent recalls similar past incidents from Hindsight, an episodic memory that stores each diagnosis together with the fix that worked or failed, and then chooses a Nemotron tier by how novel the incident is: a new incident goes to Ultra, a known problem in a new context to Super, and a repeat of a known fix to Nano with a short verification instead of a full investigation. Operators mark Fix Worked or Fix Failed, so the next occurrence starts from what the team has already learned, including failed attempts the agent should not repeat. The demo runs the Astronomy Shop with a fault deck (payment failure, DB pool exhaustion, memory leak, a silent "paid, no order" failure) so the whole loop can be shown in about five minutes: first fault, diagnosis, fix, recurrence, cheaper diagnosis. Repeat incidents cost one small-model call instead of a full multi-tool investigation. Track: Best apps and agents. Demo: <DEMO_URL>. Video: <YOUTUBE_URL>.

## How we used Nebius Token Factory
- Primary inference provider (`INFERENCE_PROVIDER=nebius`, OpenAI-compatible endpoint, LiteLLM `openai/` prefix, no custom client). One key serves all three tiers.
- Per-tier model ids come from `NEBIUS_NANO_MODEL`, `NEBIUS_SUPER_MODEL` and `NEBIUS_ULTRA_MODEL`; `scripts/list_models.py` lists what the key can use and `scripts/tier_ping.py` sends one prompt per tier (all three answered, 1.2 to 1.8 s for a ~150-token reply).
- Optionally also the memory LLM behind Hindsight (`HINDSIGHT_LLM_PROVIDER=openai` with the Token Factory base URL).
- **Status, exactly:** <FILL: what the demo video and hosted demo actually ran on. The video was recorded on NVIDIA NIM. Token Factory was exercised by: the three-tier ping, the chat badge check (Nano tier on Token Factory), <benchmark run / hosted demo, if done>.>

## How we used NVIDIA Nemotron
Three open Nemotron models, one per tier: Nano `NVIDIA-Nemotron-3-Nano-30B-A3B`, Super `nemotron-3-super-120b-a12b`, Ultra `Nemotron-3-Ultra-550b-a55b`. Tier selection is `select_model_tier` in `Copilot/agent/rca_agent.py`; each decision is recorded as a `diagnosis:tier` event on the incident timeline and shown as a badge on chat answers. Benchmark of latency and tokens per tier: <FILL from docs/benchmark.md>.

## Feedback
Token Factory / AI Cloud:
- The `/models` listing and OpenAI-compatible API made integration trivial; nothing beyond a base URL and key was needed.
- Nemotron model ids are inconsistent in casing and separators (`NVIDIA-Nemotron-3-Nano-30B-A3B`, `nemotron-3-super-120b-a12b`, `Nemotron-3-Ultra-550b-a55b`, `Nemotron-3_5-Lightning`). It made per-tier configuration easy to get wrong; a consistent scheme, or a `family`/`size` field in the listing, would help.
- <ADD: rate limits, quota, error messages, latency under load, billing visibility: anything you hit during the benchmark or hosted run.>

NVIDIA tools:
- The model names on NVIDIA NIM (`nemotron-3.5-lightning-30b-a3b`, `nemotron-3.5-super-vl-120b-a12b`) differ from the ids Token Factory serves, so we keep two sets of tier settings and a fallback chain.
- <ADD: anything from using NIM during the video recording.>

## What changed during the Submission Period
See [docs/WHAT_CHANGED.md](docs/WHAT_CHANGED.md).
