# Submission: Incident Response Agent: an SRE copilot that remembers

## Description
Incident Response Agent is an AI on-call teammate for a microservice stack. A background watcher reads telemetry (request errors, latency, logs, process memory) and opens an incident on its own; it does not wait to be asked. A Laya decision engine routes chat questions to an RCA agent or a query agent. The RCA agent recalls similar past incidents from Hindsight, an episodic memory that stores each diagnosis together with the fix that worked or failed, and then chooses a Nemotron tier by how novel the incident is: a new incident goes to Ultra, a known problem in a new context to Super, and a repeat of a known fix to Nano with a short verification instead of a full investigation. Operators mark Fix Worked or Fix Failed, so the next occurrence starts from what the team has already learned, including failed attempts the agent should not repeat. The demo runs the Astronomy Shop with a fault deck (payment failure, DB pool exhaustion, memory leak, a silent "paid, no order" failure) so the whole loop can be shown in about five minutes: first fault, diagnosis, fix, recurrence, cheaper diagnosis. For a repeat incident the watcher verifies the remembered fix with one short Nano call (about 470 tokens in our benchmark run) instead of a multi-call investigation (about 24,500 tokens for a new incident). Track: Best apps and agents. Demo: https://34-30-44-247.sslip.io/ (faults deck: https://34-30-44-247.sslip.io:8443/faults). Video: https://youtu.be/s0BdthyU7To.

## How we used Nebius Token Factory
- Primary inference provider (`INFERENCE_PROVIDER=nebius`, OpenAI-compatible endpoint, LiteLLM `openai/` prefix, no custom client). One key serves all three tiers.
- Per-tier model ids come from `NEBIUS_NANO_MODEL`, `NEBIUS_SUPER_MODEL` and `NEBIUS_ULTRA_MODEL`; `scripts/list_models.py` lists what the key can use and `scripts/tier_ping.py` sends one prompt per tier (all three answered, 1.2 to 1.8 s for a ~150-token reply).
- Optionally also the memory LLM behind Hindsight (`HINDSIGHT_LLM_PROVIDER=openai` with the Token Factory base URL).
- **Status, exactly:** The demo video was recorded earlier on NVIDIA NIM, so its model ids differ from the Token Factory ids below. The hosted demo and the benchmark run on Nebius Token Factory (`INFERENCE_PROVIDER=nebius`). Token Factory was exercised by: a three-tier ping (`scripts/tier_ping.py`), the chat badge check (tier, model and provider shown on answers), a full three-scenario tier benchmark (`scripts/benchmark_tiers.py`, 1 replicate, see `docs/benchmark.md`) and the hosted demo.

## How we used NVIDIA Nemotron
Three open Nemotron models, one per tier: Nano `NVIDIA-Nemotron-3-Nano-30B-A3B`, Super `nemotron-3-super-120b-a12b`, Ultra `Nemotron-3-Ultra-550b-a55b`. Tier selection is `select_model_tier` in `Copilot/agent/rca_agent.py`; each decision is recorded as a `diagnosis:tier` event on the incident timeline and shown as a badge on chat answers. Benchmark on Token Factory (`scripts/benchmark_tiers.py`, one replicate, so indicative only; full table in `docs/benchmark.md`): the watcher's automatic diagnosis took 25.2 s with 4 calls and 24,477 tokens for a new incident (Ultra), 10.7 s for a context shift (Super), and 3.0 s with 1 call and 474 tokens for a repeat of a remembered fix (Nano). In the operator chat the repeat used 64,127 tokens against 182,009 for the new incident, but the repeat answer was slower (77.8 s against 37.8 s), so we make no end-to-end chat-latency claim for repeats. An earlier replicate's repeat chat answer timed out at 120 s and is not included.

## Feedback
Token Factory / AI Cloud:
- The `/models` listing and OpenAI-compatible API made integration trivial; nothing beyond a base URL and key was needed.
- Nemotron model ids are inconsistent in casing and separators (`NVIDIA-Nemotron-3-Nano-30B-A3B`, `nemotron-3-super-120b-a12b`, `Nemotron-3-Ultra-550b-a55b`, `Nemotron-3_5-Lightning`). It made per-tier configuration easy to get wrong; a consistent scheme, or a `family`/`size` field in the listing, would help.
- We could not find a request log or per-request usage view in the console to confirm that runtime calls were arriving; we added our own per-call logging. During one benchmark replicate a Nano chat call failed with an agent error after 120 s and the cause was not visible from the Token Factory side; a request log with status and latency would have shortened that diagnosis.
- The AI Cloud free trial is suspended and its compute options (Serverless Jobs, Endpoints, DevPods) did not fit a multi-container Docker Compose app, so the demo is hosted on Google Cloud and only inference runs on Nebius. A guide for running a multi-service app on Nebius would help.

NVIDIA tools:
- The model names on NVIDIA NIM (`nemotron-3.5-lightning-30b-a3b`, `nemotron-3.5-super-vl-120b-a12b`) differ from the ids Token Factory serves, so we keep two sets of tier settings and a fallback chain.
- Moving from NIM (video recording) to Token Factory (hosted demo, benchmark) needed no code change, only model ids and the base URL, which is a strength of the OpenAI-compatible approach.

## What changed during the Submission Period
See [docs/WHAT_CHANGED.md](docs/WHAT_CHANGED.md).
