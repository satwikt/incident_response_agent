"""Record one fault-curriculum run as JSON next to the scenario catalog."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "scenarios" / "catalog.json"
RUNS = ROOT / "scenarios" / "runs"


def load_catalog(path: Path = CATALOG) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_scenario(catalog: dict, scenario_id: str) -> dict:
    for scenario in catalog.get("scenarios") or []:
        if scenario.get("id") == scenario_id:
            return scenario
    raise KeyError(f"unknown scenario: {scenario_id}")


def _parse_utc(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat()


def build_record(
    scenario: dict,
    *,
    start: str,
    end: str,
    traffic_profile: str,
    observed: Sequence[str],
    copilot_result: str,
    hindsight_path: str,
    notes: str = "",
) -> dict:
    start_utc = _parse_utc(start)
    end_utc = _parse_utc(end)
    if datetime.fromisoformat(end_utc) < datetime.fromisoformat(start_utc):
        raise ValueError("end must not be before start")
    return {
        "catalog_version": 1,
        "scenario_id": scenario["id"],
        "name": scenario["name"],
        "kind": scenario["kind"],
        "mode": scenario["mode"],
        "flag": scenario["flag"],
        "variant": scenario["variant"],
        "repeat_of": scenario.get("repeat_of"),
        "expected_cause": scenario["expected_cause"],
        "start_utc": start_utc,
        "end_utc": end_utc,
        "traffic_profile": traffic_profile,
        "observed_evidence": list(observed),
        "copilot_result": copilot_result,
        "hindsight_path": hindsight_path,
        "recovery": scenario["recovery"],
        "notes": notes,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Record one fault-curriculum run.")
    parser.add_argument("scenario_id")
    parser.add_argument("--start", required=True, help="UTC start, ISO 8601")
    parser.add_argument("--end", required=True, help="UTC end, ISO 8601")
    parser.add_argument("--traffic-profile", default="baseline load generator")
    parser.add_argument("--observed", action="append", default=[])
    parser.add_argument("--copilot-result", default="")
    parser.add_argument("--hindsight-path", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--out", type=Path, default=RUNS)
    args = parser.parse_args(argv)

    scenario = find_scenario(load_catalog(), args.scenario_id)
    record = build_record(
        scenario,
        start=args.start,
        end=args.end,
        traffic_profile=args.traffic_profile,
        observed=args.observed,
        copilot_result=args.copilot_result,
        hindsight_path=args.hindsight_path,
        notes=args.notes,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = record["start_utc"].replace(":", "").replace("-", "")
    path = args.out / f"{stamp}-{record['scenario_id']}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
