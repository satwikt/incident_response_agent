# Inputs from the team kickoff discussion

The kickoff walked through the earlier version of the tool (chat plus a proactive Discord alert, Prometheus/Loki/Tempo,
a RAG over documentation) and then discussed how to add memory. This file records every idea that came out of it, whether
the current design already covers it, and what we do about it. "Covered" means it is in `DESIGN.md` or already built.

## Summary

| # | Idea from the discussion | Status | Action | Priority |
|---|---|---|---|---|
| 1 | Cap the number of LLM and tool calls per question; avoid loops; let a human decide when the answer is enough | Partly covered (hourly cap, timeout, breaker). **Per-run cap was missing** | **Done:** `AGENT_MAX_LLM_CALLS` (ADK `RunConfig`), plus LLM calls and tokens recorded on every incident | Done |
| 2 | Show how many LLM calls each answer used (they added this to the UI) and treat cost as a first-class number | **Missing** | Cost per incident is stored now; the incident page and Memory panel show it, and the benchmark reports cost with and without memory | M4 (UI), M3 (benchmark) |
| 3 | If the same symptoms and evidence recur, return the stored RCA instead of running a full analysis | Partly covered (recall feeds the agent) but **no fast path** | Memory-first diagnosis: a fingerprint with confirmed past fixes alerts immediately with "known issue, this fix worked k of k times" and runs a light verification instead of the full agent. Cuts LLM cost, which is the one metric memory should visibly improve | **M3, P0** |
| 4 | Store symptom keywords, the final solution, and ask the user what they did to resolve it | Covered (retain on resolve, human feedback, tags) | Add an explicit free-text "what did you do?" field on resolve | M4 |
| 5 | Two memory types: **episodic** (what happened) and **procedural** (how we fixed it) | Not framed this way | Adopt the taxonomy: episodic = incident narratives (document per incident); procedural = distilled fix steps per fingerprint (tagged `kind:procedure`, updated on each confirmed resolution); semantic = service facts. Use it in the UI and in the article | M3, P1 |
| 6 | Root cause across services (B is slow because it calls A) needs knowledge of how services connect; that was the reason for the RAG | The RAG was removed so memory stays the star, but the **use case is real** | Keep the use case as semantic memory: seed the bank with a service map ("todo-app depends on ... "), and let recall surface it. Show one multi-hop diagnosis in the demo | M3, P2 |
| 7 | Hybrid retrieval (semantic and keyword), not semantic only | Covered by the choice of Hindsight | Recall results already carry separate `semantic`, `keyword` and `reranker` scores (measured in the spike). No work needed; worth a sentence in the article | none |
| 8 | Only Discord, one way. Business users live in Slack or Teams. They also wanted to chat from the alert channel | Notifier is Discord-shaped | Keep the incident page for actions (decided). Add Slack and Teams webhook renderers behind the same outbox: the outbox and delivery classification are already provider-neutral | M5, P2 |
| 9 | "Does it work with our stack (for example Power BI / Fabric)?" It could only attach to Prometheus, Loki and Tempo | **Solved by M1**: any application that can POST JSON events works | Document integrations: curl, a Python snippet, a Fluent Bit / log-shipper config, a file-tail adapter. This is the answer to "who can use it" for the Impact criterion | M5, P2 |
| 10 | Security signals (a container scan finds a threat) should also raise an incident | Not covered | Ingest accepts arbitrary events, so a scanner can push `level=CRITICAL` events that the keyword detector turns into an incident with the same lifecycle and memory. Document as an example adapter | M5, P3 |
| 11 | The live demo failed (quota, 503 overload, expired key, no fallback) | **Not covered**: our own spike hit the same limits | `scripts/preflight` that checks the stack, the LLM key and Hindsight before any demo or recording; pre-seeded bank; record the video with a paid or verified-healthy provider; keep a recorded fallback | M5, P1 |
| 12 | Model flexibility: the agent framework needs workarounds for non-Gemini models; Ollama was suggested as a free local option | We hit exactly this (Groq rejected reasoning content in the history) and fixed it | Provider-neutral `AGENT_MODEL` through LiteLLM is built. An `ollama/...` model works the same way but tool calling on small local models is **untested**; add it to the LLM gate | P2 |
| 13 | Noisy alerts: the earlier version paged on a single error | Solved (minimum-sample rule, hysteresis, one incident per problem) | none | Done |
| 14 | Extend the keyword list for log scans (they used four) | Configurable already | none | none |
| 15 | Human in the loop before acting | Covered (approval workflow, allow-listed actions only) | none | M4 |

## What this changes in the plan

1. **M3 gets a memory-first fast path** (item 3). It is the clearest before/after for the memory criterion: without memory
   every incident costs a full diagnosis; with memory a repeat costs a lookup and one verification call. The benchmark
   reports LLM calls and tokens per incident, not only time to resolution.
2. **The memory taxonomy** (item 5) becomes how memory is explained: episodic, procedural, semantic.
3. **A preflight check** (item 11) is added to M5 so the demo and the recording do not depend on luck.
4. **Cost per incident** (items 1 and 2) is recorded from now on, so the with/without comparison has real data.

## Not adopted, and why

- **Chat inside the alert channel** would need a full Discord application (the presenter said the same). Alerts link to an
  incident page instead.
- **Switching the agent framework** to LangChain or LangGraph: not needed; the ADK-plus-LiteLLM route works and is tested.
