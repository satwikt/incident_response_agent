import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT.parents[1] / "vendor" / "opentelemetry-demo" / "src" / "otel-collector"


class OverlayContractTests(unittest.TestCase):
    def test_overlay_mounts_integration_extras(self):
        text = (ROOT / "compose.overlay.yaml").read_text()
        self.assertIn("lgtm:", text)
        self.assertIn("127.0.0.1:3001:3000", text)
        self.assertRegex(
            text,
            r"\.\./\.\./integration/astronomy-shop/otelcol-config-extras\.yml:"
            r"/etc/otelcol-config-extras\.yml",
        )

    def test_overlay_publishes_tempo_and_loki_for_the_adapter(self):
        # The upstream stack ships neither backend; LGTM does. The adapter's
        # default --tempo-url/--loki-url are the loopback ports published here.
        text = (ROOT / "compose.overlay.yaml").read_text()
        self.assertIn("127.0.0.1:3200:3200", text)
        self.assertIn("127.0.0.1:3100:3100", text)

    def test_overlay_exports_only_defined_components(self):
        extras = (ROOT / "otelcol-config-extras.yml").read_text()
        upstream = (VENDOR / "otelcol-config-observability.yml").read_text()
        base = (VENDOR / "otelcol-config.yml").read_text()
        available = extras + upstream + base
        referenced = re.findall(r"exporters:\s*\[([^\]]+)\]", extras)
        self.assertEqual(len(referenced), 3)  # traces, metrics, logs
        names = {
            name.strip()
            for pipeline in referenced
            for name in pipeline.split(",")
            if name.strip()
        }
        for name in names:
            with self.subTest(exporter=name):
                self.assertIn(f"{name}:", available)

    def test_grafana_lgtm_datasources_are_mounted(self):
        overlay = (ROOT / "compose.overlay.yaml").read_text()
        self.assertIn(
            "../../integration/astronomy-shop/grafana/provisioning/datasources/"
            "lgtm.yaml:/etc/grafana/provisioning/datasources/lgtm.yaml",
            overlay,
        )
        datasources = (
            ROOT / "grafana" / "provisioning" / "datasources" / "lgtm.yaml"
        ).read_text()
        self.assertIn("uid: lgtm-traces", datasources)
        self.assertIn("uid: lgtm-logs", datasources)
        self.assertIn("url: http://lgtm:3200", datasources)
        self.assertIn("url: http://lgtm:3100", datasources)
        self.assertIn("datasourceUid: lgtm-logs", datasources)

    def test_alert_rules_are_mounted_and_cover_curriculum(self):
        overlay = (ROOT / "compose.overlay.yaml").read_text()
        self.assertIn(
            "../../integration/astronomy-shop/grafana/provisioning/alerting/"
            "astronomy-shop-alerts.yaml:/etc/grafana/provisioning/alerting/"
            "astronomy-shop-alerts.yaml",
            overlay,
        )
        alerts = (
            ROOT / "grafana" / "provisioning" / "alerting" / "astronomy-shop-alerts.yaml"
        ).read_text()
        self.assertEqual(alerts.count("\n      - uid: as-"), 7)
        self.assertEqual(alerts.count("condition: threshold"), 7)
        self.assertEqual(alerts.count("noDataState: OK"), 7)
        self.assertIn("datasourceUid: webstore-metrics", alerts)
        self.assertIn("http_response_status_code", alerts)
        for metric in (
            "http_server_request_duration_seconds_count",
            "traces_span_metrics_calls_total",
            "traces_span_metrics_duration_milliseconds_bucket",
            "postgresql_deadlocks_total",
            "kafka_consumer_group_lag",
        ):
            with self.subTest(metric=metric):
                self.assertIn(metric, alerts)
        for scenario in ("T-02", "T-04", "T-05", "T-06", "T-07", "T-08"):
            with self.subTest(scenario=scenario):
                self.assertIn(scenario, alerts)

    def test_lgtm_exporter_targets_internal_endpoint(self):
        extras = (ROOT / "otelcol-config-extras.yml").read_text()
        self.assertIn("otlphttp/lgtm:", extras)
        self.assertIn("http://lgtm:4318", extras)


if __name__ == "__main__":
    unittest.main()
