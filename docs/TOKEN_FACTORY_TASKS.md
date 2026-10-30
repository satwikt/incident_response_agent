# Claude Code task list: run everything on Nebius Token Factory (deadline Fri 30 Oct, 22:30 IST)

Rules: never print or commit secrets (.env is gitignored; do not cat it). Work on branch `nvidia-hackathon`. Commit after each task. Stop and report if a task fails twice.

## T1. Confirm the provider path works (30 min)
1. Read `Copilot/agent/config.py` and the LiteLLM call site. Confirm `INFERENCE_PROVIDER=nebius` uses base URL `https://api.tokenfactory.nebius.com/v1` with `NEBIUS_API_KEY` and the per-tier `NEBIUS_NANO/SUPER/ULTRA_MODEL` vars.
2. I will add `NEBIUS_API_KEY` to local `.env` myself. Run `python scripts/list_models.py`, save output to `docs/token_factory_models.txt`.
3. Pick models: Nano = smallest Nemotron, Super = mid Nemotron, Ultra = largest Nemotron available. All three must be NVIDIA open models. Make Super and Ultra DIFFERENT if two or more larger Nemotron models exist; otherwise keep and document it. Put IDs in `.env.example` as defaults.
4. Write a 10-line script or pytest (mocked-off, real call opt-in) that sends one prompt per tier and prints model + latency. Run it.

## T2. Hindsight memory LLM on Token Factory (20 min)
Set `HINDSIGHT_LLM_PROVIDER=openai` with the Token Factory base URL and a small model (see `.env.example` Option B). Start the stack and verify a diagnosis is retained and recalled (`fix:*` tag appears on the next incident).

## T3. Chat UI tier badge (20 min)
`/ask` already returns `tier` and `model`. In `Copilot/frontend/index.html` show a badge next to "N LLM calls", e.g. `Nano - nemotron-... - Token Factory`. Keep it tiny.

## T4. Run the stack and the demo flow end to end (45 min)
`MEMORY_MODE=on`. Follow `docs/DEMO_TEST_FLOW.md`: payment_failure -> Ultra, repeat after fix -> Nano. Confirm the `diagnosis:tier` timeline events. Fix any bug found, with a test.

## T5. Benchmark (about 1 h, run in background)
`python scripts/benchmark_tiers.py --runs 3` (use `--runs 1` if short on time). Output: `docs/benchmark.md` and `docs/benchmark.json`. Paste the table into the README Benchmark section.

## T6. License and repo hygiene (10 min)
Confirm `LICENSE` is Apache-2.0, MIT or MPL-2.0 (required by the hackathon) and GitHub detects it. Run `pytest`. Run `git grep -n -I -E "(gsk_|nvapi-|sk-|tf_)"` and make sure no keys are tracked.

## T7. Docs (20 min)
1. `mv readme.proposed.md readme.md`; fill: provider status (state exactly what ran on Token Factory vs NVIDIA NIM; the video was recorded on NIM), benchmark table, model table, demo/video URLs (placeholders until I have them), track.
2. Update `docs/DEPLOY.md` to match where it is hosted (see T8).
3. Create `SUBMISSION.md`: text description (150-250 words), Token Factory usage, Nemotron usage, feedback on Token Factory/AI Cloud/NVIDIA tools (honest, specific), what changed (link `docs/WHAT_CHANGED.md`).

## T8. Hosted demo (2-3 h, start by 12:00 IST)
Run `deploy/docker-compose.prod.yml` on one VM (4 vCPU / 16 GB, x86 preferred; Oracle ARM only if every image has arm64). Caddy TLS via sslip.io, ports 80/443 only, CHAOS_KEY set, caps on. Then `scripts/smoke_hosted.sh https://$DEMO_DOMAIN` and run the demo flow from the public URL. Write the exact steps into DEPLOY.md.

## T9. Ship
Commit, push, merge `nvidia-hackathon` into `main` (judges see the default branch). Confirm CI is green.

Order if time is short: T1, T4, T6, T7, T8, T5, T3, T2.
