# What changed during the Submission Period (26 Aug – 30 Oct 2026)

This project existed before the Submission Period. This note separates what was already there from what was built for this hackathon.

## Before 26 Aug 2026 (pre-existing)
- Idea and a first prototype of an incident-response copilot, built by two team members (Sahil Deo, Satwik Thakkar) for the Cloud Native Hackathon, Pune (2nd place).
- A phase-1 prototype in mid-August: basic watcher plus LLM summary of failures. No memory layer, no model tiering, no hosted deployment.
- The repository was imported to its current home on 28 Sep 2026. The git history in this repo starts there.

## Built or significantly changed during the Submission Period
1. **Multi-agent architecture (Google ADK):** Proactive Watcher, Reactive RCA Agent, Reactive Query Agent, and the Laya decision engine (same_context vs different_context, rca vs query intent).
2. **Hindsight episodic memory layer:** every diagnosis and every fix outcome is stored (`fix:*` / `failed:*` tags) and recalled on later incidents, including failed attempts so the agent does not repeat them.
3. **Dynamic Nemotron tiering:** `select_model_tier` picks Nano / Super / Ultra by incident novelty and memory. New incident -> Ultra, context shift -> Super, repeat of a known fix -> Nano. Each decision is recorded as a `diagnosis:tier` timeline event.
4. **Nebius Token Factory provider:** OpenAI-compatible provider chain (`INFERENCE_PROVIDER=nebius` -> NVIDIA NIM -> Groq), per-tier model env vars, `scripts/list_models.py` to discover available models.
5. **Observability and fault-injection stack:** OTel + Prometheus, Loki, Tempo, Grafana; Astronomy Shop microservices with a runtime fault deck.
6. **Docs RAG:** ChromaDB + BM25 index over runbooks.
7. **Dashboard and chat UI:** incident timeline, Fix Worked / Fix Failed feedback, chat with per-answer LLM-call count.
8. **Hosted-demo hardening:** `CHAOS_KEY` on fault endpoints, per-hour and per-day LLM/token caps, labelled replay fallback, `docs/DEPLOY.md`, Caddy TLS compose file.
9. **Benchmark harness:** `scripts/benchmark_tiers.py` measures latency/tokens per tier.
10. **Tests and CI:** 32 test modules under `Copilot/tests`.
11. **Fixes (27-28 Oct):** L01/L02 scenario fixes, "paid, no order" scenario, license, README/docs rewrite.

Commit history for the period: `git log --since=2026-08-26 --oneline`.
