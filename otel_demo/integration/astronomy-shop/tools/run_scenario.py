"""Run one fault-curriculum scenario through flagd-ui and record the run.

The runner snapshots the full flag state before mutating anything and restores
that snapshot in a finally block, including when the wait is interrupted.
The snapshot is written to disk first so an operator can recover manually.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import URLError

# Allow `python3 tools/run_scenario.py` in addition to `python3 -m tools.run_scenario`.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.flagd_ui import FLAGD_SCHEMA, FlagdUiClient
from tools.record_scenario import (
    RUNS,
    build_record,
    find_scenario,
    load_catalog,
)

Clock = Callable[[], datetime]
Sleep = Callable[[float], None]


def set_variant(flags: Mapping[str, Any], name: str, variant: str) -> dict[str, Any]:
    """Return a copy of the flag map with the target flag's default variant set."""
    if name not in flags:
        raise KeyError(f"flag not present in the running configuration: {name}")
    spec = flags[name]
    variants = spec.get("variants") or {}
    if variant not in variants:
        raise ValueError(f"variant {variant!r} is not defined for flag {name}")
    updated = copy.deepcopy(dict(flags))
    updated[name]["defaultVariant"] = variant
    return updated


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_scenario(
    client: FlagdUiClient,
    scenario: Mapping[str, Any],
    *,
    duration: float = 300.0,
    out: Path = RUNS,
    sleep: Sleep = time.sleep,
    now: Clock = _utcnow,
    traffic_profile: str = "baseline load generator",
    observed: Sequence[str] = (),
    copilot_result: str = "",
    hindsight_path: str = "",
    notes: str = "",
) -> tuple[Path, dict]:
    """Toggle, wait, restore, and record. Returns the record path and payload."""
    if duration < 0:
        raise ValueError("duration must be >= 0")

    flags = client.read()
    snapshot = copy.deepcopy(flags)
    out.mkdir(parents=True, exist_ok=True)
    started_at = now()
    snapshot_path = out / f"{_stamp(started_at)}-{scenario['id']}.snapshot.json"
    snapshot_path.write_text(
        json.dumps({"$schema": FLAGD_SCHEMA, "flags": snapshot}, indent=2) + "\n",
        encoding="utf-8",
    )

    attempted_write = False
    try:
        if scenario.get("flag"):
            updated = set_variant(flags, scenario["flag"], scenario["variant"])
            attempted_write = True
            client.write(updated)
        start = now().astimezone(timezone.utc)
        sleep(duration)
        end = now().astimezone(timezone.utc)
    finally:
        if attempted_write:
            try:
                client.write(snapshot)
            except Exception as exc:  # noqa: BLE001 - surface recovery path
                print(
                    f"flag restore failed: {exc}; snapshot at {snapshot_path}",
                    file=sys.stderr,
                )
                raise

    record = build_record(
        dict(scenario),
        start=start.isoformat(),
        end=end.isoformat(),
        traffic_profile=traffic_profile,
        observed=observed,
        copilot_result=copilot_result,
        hindsight_path=hindsight_path,
        notes=notes or "recorded by tools/run_scenario.py",
    )
    record["flag_restored"] = attempted_write
    record["flag_snapshot"] = snapshot_path.name
    record_path = out / f"{_stamp(started_at)}-{scenario['id']}.json"
    record_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record_path, record


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one fault scenario and restore flags afterwards."
    )
    parser.add_argument("scenario_id")
    parser.add_argument("--duration", type=float, default=300.0, help="seconds to hold")
    parser.add_argument(
        "--flagd-ui-url",
        default="http://localhost:8080/feature",
        help="flagd-ui base URL reachable from the operator host",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--traffic-profile", default="baseline load generator")
    parser.add_argument("--observed", action="append", default=[])
    parser.add_argument("--copilot-result", default="")
    parser.add_argument("--hindsight-path", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--out", type=Path, default=RUNS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    scenario = find_scenario(load_catalog(), args.scenario_id)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "scenario_id": scenario["id"],
                    "flag": scenario["flag"],
                    "variant": scenario["variant"],
                    "mode": scenario["mode"],
                    "duration": args.duration,
                    "flagd_ui_url": args.flagd_ui_url,
                    "note": "dry run; no request sent and nothing recorded",
                },
                indent=2,
            )
        )
        return 0

    client = FlagdUiClient(args.flagd_ui_url, timeout=args.timeout)
    try:
        path, record = run_scenario(
            client,
            scenario,
            duration=args.duration,
            out=args.out,
            traffic_profile=args.traffic_profile,
            observed=args.observed,
            copilot_result=args.copilot_result,
            hindsight_path=args.hindsight_path,
            notes=args.notes,
        )
    except (URLError, RuntimeError, KeyError, ValueError) as exc:
        print(f"scenario failed: {exc}", file=sys.stderr)
        return 2
    print(path)
    print(
        f"{record['scenario_id']} {record['flag'] or 'no-flag'} "
        f"{record['variant'] or ''} {record['start_utc']} -> {record['end_utc']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
