import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.record_scenario import build_record, find_scenario, load_catalog
from tools.scenario_report import build_report, load_runs, main


def record(scenario_id, start, end, *, observed, result="", restored=True):
    scenario = find_scenario(load_catalog(), scenario_id)
    payload = build_record(
        scenario,
        start=start,
        end=end,
        traffic_profile="baseline",
        observed=observed,
        copilot_result=result,
        hindsight_path="incidents/test",
    )
    payload["flag_restored"] = restored
    payload["flag_snapshot"] = "snapshot.json"
    return payload


class ReportTests(unittest.TestCase):
    def test_latest_run_is_used_and_snapshots_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            first = record("T-02", "2026-10-20T10:00:00Z", "2026-10-20T10:05:00Z", observed=["old"])
            second = record(
                "T-02",
                "2026-10-20T12:00:00Z",
                "2026-10-20T12:05:00Z",
                observed=["checkout 5xx"],
                result="payment failure",
            )
            (runs_dir / "a.json").write_text(json.dumps(first))
            (runs_dir / "b.json").write_text(json.dumps(second))
            (runs_dir / "b.snapshot.json").write_text(json.dumps(second))

            runs = load_runs(runs_dir)
            self.assertEqual(len(runs["T-02"]), 2)
            markdown, summary = build_report(load_catalog(), runs)

        self.assertIn("T-02", markdown)
        self.assertIn("paymentFailure / 100%", markdown)
        self.assertIn("checkout 5xx", markdown)
        self.assertNotIn("old", markdown)
        self.assertIn("| 2 |", markdown)
        self.assertEqual(summary["covered"], 1)
        self.assertEqual(summary["runs"][0]["runs"], 2)
        self.assertIn("T-01", summary["missing"])

    def test_empty_directory_reports_no_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = load_runs(Path(tmp))
        markdown, summary = build_report(load_catalog(), runs)
        self.assertIn("No recorded runs.", markdown)
        self.assertEqual(summary["covered"], 0)
        self.assertEqual(summary["missing"], [f"T-{index:02d}" for index in range(1, 11)])

    def test_render_escapes_table_pipes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            payload = record(
                "T-05",
                "2026-10-20T10:00:00Z",
                "2026-10-20T10:05:00Z",
                observed=["latency | only international"],
            )
            (runs_dir / "run.json").write_text(json.dumps(payload))
            markdown, _ = build_report(load_catalog(), load_runs(runs_dir))
        self.assertIn("latency \\| only international", markdown)

    def test_cli_writes_markdown_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            runs_dir.mkdir()
            payload = record(
                "T-04",
                "2026-10-20T10:00:00Z",
                "2026-10-20T10:05:00Z",
                observed=["checkout connection failure"],
            )
            (runs_dir / "run.json").write_text(json.dumps(payload))
            md_path = Path(tmp) / "report.md"
            json_path = Path(tmp) / "report.json"
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["--runs", str(runs_dir), "--out", str(md_path)]), 0)
                self.assertEqual(
                    main(
                        [
                            "--runs",
                            str(runs_dir),
                            "--format",
                            "json",
                            "--out",
                            str(json_path),
                        ]
                    ),
                    0,
                )
            self.assertIn("T-04", md_path.read_text())
            payload = json.loads(json_path.read_text())
            self.assertEqual(payload["covered"], 1)
            self.assertEqual(payload["runs"][0]["scenario_id"], "T-04")

    def test_cli_prints_without_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--runs", tmp]), 0)
            self.assertIn("No recorded runs.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
