# Tier benchmark

Measured on 2026-10-30 13:20 UTC against `http://localhost:9911` (commit `974909e`), 1 replicate(s); every number is a median of the runs, taken from the stack's own API and clock. Raw runs: [`benchmark.json`](benchmark.json). Reproduce: `python scripts/benchmark_tiers.py`.

| Scenario | Expected tier | Tier chosen (hits) | Model | Detection (s) | Chat RCA (s) | LLM calls | Prompt tok | Completion tok | Auto-diagnosis (s) / mode | Known-good fix proposed |
|---|---|---|---|---|---|---|---|---|---|---|
| New incident (empty memory) | ultra | ultra (1/1) | `nvidia/Nemotron-3-Ultra-550b-a55b` | 56.81 | 37.82 | 12 | 176335 | 5674 | 25.17 / full | 1/1 |
| Context shift (same service, new cause) | super | super (1/1) | `nvidia/nemotron-3-super-120b-a12b` | 25.87 | 6.62 | 4 | 24150 | 796 | 10.68 / full | 1/1 |
| Repeat after Fix Worked | nano | nano (1/1) | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 66.37 | 77.81 | 7 | 55831 | 8296 | 3.04 / verify | 1/1 |

## What the numbers say

The repeat incident's chat diagnosis took 77.81 s against 37.82 s for the new one (-106% slower). Tokens: 64127 against 182009 (+65% fewer). **The repeat was not faster in this run; the claim is not supported by these measurements.**

Method notes:

- The three scenarios run back to back on one stack per replicate, so the repeat incident has two precedents in memory.
- *Detection* = fault switched on -> incident opened by the watcher (the stack's 1-minute window). *Chat RCA* = the operator's question -> answer, including memory recall. *Auto-diagnosis* = incident opened -> the watcher's own diagnosis stored (`full` = tool-using RCA, `verify` = light memory-first check).
- A settling pause after each Fix Worked (longer than `RESOLVE_GRACE_S`) is excluded from every number.
- Tier models come from the chat reply (`model`). Fix proposed = the reply ends with `Proposed action: disable_fault`.
