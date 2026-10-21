# Fault Scenarios

`catalog.json` maps the curriculum in `docs/test-scenarios.md` (T-01 … T-10) to concrete flagd flags and variants. Every flag and variant is validated against the pinned `src/flagd/demo.flagd.json`, so upstream drift fails the tests instead of silently invalidating a scenario.

Fields per scenario: `id`, `name`, `kind`, `mode` (`minimal`/`full`), `flag`, `variant`, `expected_services`, `expected_cause`, `evidence_hints`, `recovery`, and `repeat_of` where applicable.

## Automated run

`tools/run_scenario.py` uses the flagd-ui REST API (`GET /feature/api/read`, `POST /feature/api/write`) documented in the vendored flagd-ui README:

```bash
cd integration/astronomy-shop
python3 tools/run_scenario.py T-02 --duration 300 \
  --observed "checkout 5xx" \
  --observed "payment error spans" \
  --copilot-result "payment failure" \
  --hindsight-path "incidents/payment"
```

Safety behavior:

- The full flag state is snapshotted to `scenarios/runs/<stamp>-<scenario>.snapshot.json` **before** any write.
- The target flag's `defaultVariant` is set to the catalog variant.
- The snapshot is restored in a `finally` block, including on Ctrl-C, so a normal failure cannot leave a fault active.
- If restore itself fails, the error prints the snapshot path for manual recovery.
- `--dry-run` prints the plan without sending requests or writing files.

Baseline scenarios (`T-01`, `T-09`, `T-10`) have no flag; the runner only holds the window and records it. For T-09/T-10 the operator still performs the observability stop or log injection manually.

`--flagd-ui-url` defaults to `http://localhost:8080/feature` (through the frontend proxy). The write endpoint replaces the whole document, so a `$schema` value is included; do not run multiple writers concurrently.

## Report coverage

```bash
python3 tools/scenario_report.py --format md
python3 tools/scenario_report.py --format json --out scenarios/report.json
```

The report uses the latest run per scenario, lists scenarios with no record, and shows observed evidence, Copilot result, and whether flags were restored.

## Manual record

If a run was performed by hand, record it without toggling:

```bash
python3 tools/record_scenario.py T-02 \
  --start 2026-10-20T10:00:00Z \
  --end 2026-10-20T10:05:00Z \
  --observed "checkout 5xx" \
  --copilot-result "payment failure" \
  --hindsight-path "incidents/payment"
```

The recorder writes `scenarios/runs/<start-stamp>-<scenario>.json` with the expected cause, observed evidence, Copilot result, Hindsight path, recovery, and `recorded_at`.
