"""Summarize recorded fault runs against the scenario catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

# Allow `python3 tools/scenario_report.py` in addition to module execution.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.record_scenario import RUNS, load_catalog

CELL_LIMIT = 80


def load_runs(runs_dir: Path) -> dict[str, list[dict]]:
    """Load run records, grouping by scenario and ignoring recovery snapshots."""
    runs: dict[str, list[dict]] = {}
    if not runs_dir.is_dir():
        return runs
    for path in sorted(runs_dir.glob("*.json")):
        if path.name.endswith(".snapshot.json"):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        scenario_id = record.get("scenario_id")
        if isinstance(scenario_id, str):
            runs.setdefault(scenario_id, []).append(record)
    for records in runs.values():
        records.sort(key=lambda record: str(record.get("start_utc", "")))
    return runs


def _cell(value: Any, limit: int = CELL_LIMIT) -> str:
    text = str(value or "").replace("|", "\\|").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _row(scenario: Mapping[str, Any], record: Mapping[str, Any], runs: int) -> dict:
    return {
        "scenario_id": scenario["id"],
        "name": scenario["name"],
        "flag": scenario.get("flag"),
        "variant": scenario.get("variant"),
        "runs": runs,
        "start_utc": record.get("start_utc", ""),
        "end_utc": record.get("end_utc", ""),
        "expected_cause": scenario.get("expected_cause", ""),
        "observed_evidence": "; ".join(record.get("observed_evidence") or []),
        "copilot_result": record.get("copilot_result", ""),
        "flag_restored": bool(record.get("flag_restored")),
    }


def build_report(
    catalog: Mapping[str, Any], runs: Mapping[str, list[dict]]
) -> tuple[str, dict]:
    """Return a Markdown report and a machine-readable summary."""
    scenarios = catalog.get("scenarios") or []
    latest = {sid: records[-1] for sid, records in runs.items() if records}
    rows: list[dict] = []
    missing: list[str] = []
    for scenario in scenarios:
        scenario_id = scenario["id"]
        record = latest.get(scenario_id)
        if record is None:
            missing.append(scenario_id)
            continue
        rows.append(_row(scenario, record, len(runs.get(scenario_id, []))))

    lines = [
        "# Scenario Run Report",
        "",
        f"Covered {len(rows)} of {len(scenarios)} scenarios.",
        "",
    ]
    if rows:
        lines.extend(
            [
                "| Scenario | Flag / Variant | Runs | Window (UTC) | Expected cause | Observed evidence | Copilot result | Restored |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        for row in rows:
            flag = f"{row['flag']} / {row['variant']}" if row["flag"] else "none"
            lines.append(
                "| {sid} {name} | {flag} | {runs} | {start} → {end} | {cause} | {evidence} | {result} | {restored} |".format(
                    sid=row["scenario_id"],
                    name=_cell(row["name"], 40),
                    flag=_cell(flag, 40),
                    runs=row["runs"],
                    start=_cell(row["start_utc"], 25),
                    end=_cell(row["end_utc"], 25),
                    cause=_cell(row["expected_cause"], 60),
                    evidence=_cell(row["observed_evidence"], 60),
                    result=_cell(row["copilot_result"], 40),
                    restored="yes" if row["flag_restored"] else "n/a",
                )
            )
    else:
        lines.append("No recorded runs.")

    if missing:
        lines.extend(["", "## Missing runs", ""])
        lines.extend(f"- {scenario_id}" for scenario_id in missing)

    summary = {
        "scenarios": len(scenarios),
        "covered": len(rows),
        "missing": missing,
        "runs": rows,
    }
    return "\n".join(lines) + "\n", summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize recorded scenario runs.")
    parser.add_argument("--runs", type=Path, default=RUNS)
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    catalog = load_catalog()
    runs = load_runs(args.runs)
    markdown, summary = build_report(catalog, runs)
    text = markdown if args.format == "md" else json.dumps(summary, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(args.out)
    else:
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
