# Tier benchmark

Measured on 2026-10-30 12:47 UTC against `http://localhost:9911` (commit `640a6f7`), 1 replicate(s); every number is a median of the runs, taken from the stack's own API and clock. Raw runs: [`benchmark.json`](benchmark.json). Reproduce: `python scripts/benchmark_tiers.py`.

| Scenario | Expected tier | Tier chosen (hits) | Model | Detection (s) | Chat RCA (s) | LLM calls | Prompt tok | Completion tok | Auto-diagnosis (s) / mode | Known-good fix proposed |
|---|---|---|---|---|---|---|---|---|---|---|
| New incident (empty memory) | ultra | ultra (1/1) | `nvidia/Nemotron-3-Ultra-550b-a55b` | 62.78 | 13.54 | 5 | 39952 | 1706 | 14.32 / full | 1/1 |
| Context shift (same service, new cause) | super | super (1/1) | `nvidia/nemotron-3-super-120b-a12b` | 57.23 | 9.08 | 5 | 34960 | 1194 | 7.88 / full | 1/1 |
| Repeat after Fix Worked | nano | nano (1/1) | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 9.46 | 120.24 | 0 | n/a | n/a | 3.02 / verify | 0/1 |

## What the numbers say

The repeat incident's chat diagnosis took 120.24 s against 13.54 s for the new one (-788% slower). **The repeat was not faster in this run; the claim is not supported by these measurements.**

Method notes:

- The three scenarios run back to back on one stack per replicate, so the repeat incident has two precedents in memory.
- *Detection* = fault switched on -> incident opened by the watcher (the stack's 1-minute window). *Chat RCA* = the operator's question -> answer, including memory recall. *Auto-diagnosis* = incident opened -> the watcher's own diagnosis stored (`full` = tool-using RCA, `verify` = light memory-first check).
- A settling pause after each Fix Worked (longer than `RESOLVE_GRACE_S`) is excluded from every number.
- Tier models come from the chat reply (`model`). Fix proposed = the reply ends with `Proposed action: disable_fault`.
