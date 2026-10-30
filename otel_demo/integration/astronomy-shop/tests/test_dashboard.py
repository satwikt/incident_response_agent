import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

ROOT = Path(__file__).parents[1]
DASHBOARD = ROOT / "grafana" / "dashboards" / "astronomy-shop-investigation.json"
ALLOWED_DATASOURCES = {"webstore-metrics", "lgtm-logs"}

EXPECTED_PANELS = {
    "Checkout 5xx ratio": "http_server_request_duration_seconds_count",
    "Payment error spans": "traces_span_metrics_calls_total",
    "Shipping p95 latency": "http_server_request_duration_seconds_bucket",
    "Recommendation p95 span latency": "traces_span_metrics_duration_milliseconds_bucket",
    "Product catalog p95 latency": "http_server_request_duration_seconds_bucket",
    "Postgres deadlocks": "postgresql_deadlocks_total",
    "Kafka consumer lag (full mode)": "kafka_consumer_group_lag",
}


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
        cls.panels = cls.dashboard["panels"]

    def test_identity_and_panel_count(self):
        self.assertEqual(self.dashboard["uid"], "as-investigation")
        self.assertIn("Astronomy Shop", self.dashboard["title"])
        self.assertEqual(len(self.panels), 8)
        self.assertEqual(len({panel["id"] for panel in self.panels}), 8)

    def test_grid_positions_fit_twenty_four_columns(self):
        for panel in self.panels:
            with self.subTest(panel=panel["title"]):
                grid = panel["gridPos"]
                self.assertGreaterEqual(grid["x"], 0)
                self.assertGreaterEqual(grid["y"], 0)
                self.assertLessEqual(grid["x"] + grid["w"], 24)
                self.assertGreater(grid["h"], 0)

    def test_datasources_use_provisioned_uids(self):
        for panel in self.panels:
            with self.subTest(panel=panel["title"]):
                self.assertIn(panel["datasource"]["uid"], ALLOWED_DATASOURCES)
                for target in panel["targets"]:
                    self.assertIn(target["datasource"]["uid"], ALLOWED_DATASOURCES)
                    self.assertTrue(target["refId"])

    def test_prometheus_panels_use_verified_metrics(self):
        found = {}
        for panel in self.panels:
            for target in panel["targets"]:
                if target["datasource"]["uid"] == "webstore-metrics":
                    found[panel["title"]] = target["expr"]
        for title, metric in EXPECTED_PANELS.items():
            with self.subTest(panel=title):
                self.assertIn(title, found)
                self.assertIn(metric, found[title])

    def test_logs_panel_uses_lgtm_loki(self):
        logs = [panel for panel in self.panels if panel["type"] == "logs"]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["datasource"]["uid"], "lgtm-logs")
        self.assertIn("service_name", logs[0]["targets"][0]["expr"])

    def test_dashboard_is_mounted_into_upstream_grafana(self):
        overlay = (ROOT / "compose.overlay.yaml").read_text(encoding="utf-8")
        self.assertIn(
            "../../integration/astronomy-shop/grafana/dashboards/"
            "astronomy-shop-investigation.json:"
            "/etc/grafana/provisioning/dashboards/demo/"
            "astronomy-shop-investigation.json",
            overlay,
        )


if __name__ == "__main__":
    unittest.main()
