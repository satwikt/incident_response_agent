import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.record_scenario import build_record, find_scenario, load_catalog, main

ROOT = Path(__file__).parents[1]
VENDOR = ROOT.parents[1] / "vendor" / "opentelemetry-demo"
FLAGS = VENDOR / "src" / "flagd" / "demo.flagd.json"


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()
        cls.flags = json.loads(FLAGS.read_text(encoding="utf-8"))["flags"]
        cls.scenarios = {row["id"]: row for row in cls.catalog["scenarios"]}

    def test_curriculum_covers_t01_to_t10(self):
        self.assertEqual(
            sorted(self.scenarios), [f"T-{index:02d}" for index in range(1, 11)]
        )

    def test_flags_and_variants_exist_in_vendored_definitions(self):
        for scenario in self.scenarios.values():
            flag = scenario["flag"]
            if flag is None:
                self.assertIsNone(scenario["variant"])
                continue
            with self.subTest(scenario=scenario["id"]):
                self.assertIn(flag, self.flags)
                variants = self.flags[flag]["variants"]
                self.assertIn(scenario["variant"], variants)
                self.assertNotEqual(scenario["variant"], "off")

    def test_modes_are_valid_and_kafka_requires_full_mode(self):
        for scenario in self.scenarios.values():
            self.assertIn(scenario["mode"], {"minimal", "full"})
        self.assertEqual(self.scenarios["T-08"]["mode"], "full")

    def test_repeat_scenario_references_previous_run(self):
        repeat = self.scenarios["T-03"]
        self.assertEqual(repeat["repeat_of"], "T-02")
        self.assertEqual(
            build_record(
                repeat,
                start="2026-10-20T10:00:00Z",
                end="2026-10-20T10:05:00Z",
                traffic_profile="baseline",
                observed=[],
                copilot_result="",
                hindsight_path="",
            )["repeat_of"],
            "T-02",
        )
        original = self.scenarios["T-02"]
        self.assertEqual(repeat["flag"], original["flag"])
        self.assertEqual(repeat["variant"], original["variant"])

    def test_changed_context_differs_from_original_cause(self):
        first = self.scenarios["T-02"]
        changed = self.scenarios["T-04"]
        # Same symptom family, different root cause.
        self.assertTrue(
            set(first["expected_services"]) & set(changed["expected_services"])
        )
        self.assertNotEqual(first["expected_cause"], changed["expected_cause"])

    def test_non_fault_scenarios_have_no_flag(self):
        for scenario_id in ("T-01", "T-09", "T-10"):
            self.assertIsNone(self.scenarios[scenario_id]["flag"])


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.scenario = find_scenario(load_catalog(), "T-02")

    def record(self, **overrides):
        values = {
            "start": "2026-10-20T10:00:00Z",
            "end": "2026-10-20T10:05:00Z",
            "traffic_profile": "baseline",
            "observed": ["checkout 5xx", "payment error spans"],
            "copilot_result": "payment failure",
            "hindsight_path": "incidents/payment",
        }
        values.update(overrides)
        return build_record(self.scenario, **values)

    def test_record_contains_required_fields(self):
        record = self.record()
        for key in (
            "scenario_id",
            "flag",
            "variant",
            "start_utc",
            "end_utc",
            "traffic_profile",
            "observed_evidence",
            "copilot_result",
            "hindsight_path",
            "recovery",
            "recorded_at",
        ):
            self.assertIn(key, record)
        self.assertEqual(record["scenario_id"], "T-02")
        self.assertEqual(record["observed_evidence"], ["checkout 5xx", "payment error spans"])
        datetime.fromisoformat(record["recorded_at"])

    def test_rejects_naive_or_reversed_timestamps(self):
        with self.assertRaises(ValueError):
            self.record(start="2026-10-20T10:00:00")
        with self.assertRaises(ValueError):
            self.record(
                start="2026-10-20T10:05:00Z", end="2026-10-20T10:00:00Z"
            )

    def test_cli_writes_run_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "T-04",
                    "--start",
                    "2026-10-20T11:00:00Z",
                    "--end",
                    "2026-10-20T11:05:00Z",
                    "--traffic-profile",
                    "baseline",
                    "--observed",
                    "checkout connection failure",
                    "--copilot-result",
                    "payment unreachable",
                    "--hindsight-path",
                    "incidents/payment-unreachable",
                    "--out",
                    tmp,
                ]
            )
            self.assertEqual(code, 0)
            files = list(Path(tmp).glob("*.json"))
            self.assertEqual(len(files), 1)
            record = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(record["scenario_id"], "T-04")
            self.assertEqual(record["flag"], "paymentUnreachable")


if __name__ == "__main__":
    unittest.main()
