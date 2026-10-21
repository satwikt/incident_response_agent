import argparse
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from adapter.__main__ import _configured_services, _parser, build_sources, main
from adapter.loki import LogEntry
from adapter.poller import parse_ingest_keys
from adapter.presets import PRESETS, QueryPreset, presets_by_backend
from adapter.prometheus import Sample
from adapter.sources import loki_source, prometheus_source


class PresetIntegrityTests(unittest.TestCase):
    def test_presets_are_well_formed(self):
        self.assertGreaterEqual(len(PRESETS), 9)
        names = set()
        for preset in PRESETS.values():
            with self.subTest(preset=preset.name):
                self.assertIsInstance(preset, QueryPreset)
                self.assertNotIn(preset.name, names)
                names.add(preset.name)
                self.assertIn(preset.backend, {"prometheus", "loki"})
                self.assertTrue(preset.query.strip())
                self.assertTrue(preset.service.strip())
                self.assertIn("service_name", preset.query)
                self.assertIn(preset.service, preset.query)

    def test_expected_backends_and_metrics(self):
        prometheus = presets_by_backend("prometheus")
        loki = presets_by_backend("loki")
        self.assertGreaterEqual(len(prometheus), 6)
        self.assertGreaterEqual(len(loki), 3)
        queries = " ".join(preset.query for preset in prometheus)
        for metric in (
            "http_server_request_duration_seconds_count",
            "http_server_request_duration_seconds_bucket",
            "traces_span_metrics_calls_total",
            "traces_span_metrics_duration_milliseconds_bucket",
        ):
            self.assertIn(metric, queries)


class SourceOverrideTests(unittest.TestCase):
    def test_prometheus_aggregate_keeps_explicit_service(self):
        class FakeClient:
            def query(self, expression, *, at=None):
                return [Sample("error_ratio", {}, 0.42, 1700000000.0)]

        preset = PRESETS["checkout-error-ratio"]
        evidence = prometheus_source(
            FakeClient(), preset.query, service=preset.service
        )()
        self.assertEqual(evidence[0].service, "checkout")
        self.assertIn("error_ratio", evidence[0].message)

    def test_loki_preset_overrides_missing_service_label(self):
        class FakeClient:
            def query_range(self, query, start_ns, end_ns, *, limit=100, direction="backward"):
                return [LogEntry(1700000000000000000, "boom", {"trace_id": "abc"})]

        preset = PRESETS["payment-error-logs"]
        evidence = loki_source(
            FakeClient(), preset.query, service=preset.service
        )()
        self.assertEqual(evidence[0].service, "payment")
        self.assertEqual(evidence[0].trace_id, "abc")


class CliPresetTests(unittest.TestCase):
    def test_list_presets_needs_no_ingest_config(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--list-presets"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("checkout-error-ratio", text)
        self.assertIn("payment-error-logs", text)
        self.assertIn("prometheus", text)
        self.assertIn("loki", text)

    def test_unknown_preset_is_rejected(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--preset", "nope"])
        self.assertEqual(raised.exception.code, 2)

    def test_build_sources_includes_selected_presets(self):
        args = argparse.Namespace(
            prometheus_query=[],
            loki_query=[],
            preset=["checkout-error-ratio", "checkout-error-logs"],
            tempo_service=[],
            jaeger_service=[],
            prometheus_url="http://prometheus:9090",
            loki_url="http://loki:3100",
            tempo_url="http://tempo:3200",
            jaeger_url="http://jaeger:16686",
            jaeger_api_prefix="/jaeger/ui/api",
            timeout=5.0,
            window=300.0,
        )
        sources = build_sources(args)
        self.assertEqual(
            set(sources),
            {"prometheus:checkout-error-ratio", "loki:checkout-error-logs"},
        )
        self.assertTrue(all(callable(source) for source in sources.values()))

    def test_default_jaeger_url_targets_the_proxy_base_path(self):
        # Jaeger v2 serves its API under base_path (/jaeger/ui in the upstream
        # stack), so the default must carry that prefix.
        args = _parser().parse_args([])
        self.assertEqual(args.jaeger_url, "http://localhost:8080/jaeger/ui")
        self.assertEqual(args.jaeger_api_prefix, "/api")

    def test_ingest_keys_flag_is_parsed(self):
        args = _parser().parse_args(["--ingest-keys", "checkout:k1,payment:k2"])
        self.assertEqual(
            parse_ingest_keys(args.ingest_keys),
            {"checkout": "k1", "payment": "k2"},
        )

    def test_api_key_is_optional_when_ingest_keys_are_given(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--ingest-url", "http://copilot:8000/ingest/events"])
        self.assertEqual(raised.exception.code, 2)

        code = main(
            [
                "--ingest-url",
                "http://copilot:8000/ingest/events",
                "--ingest-keys",
                "checkout:k1",
                "--prometheus-query",
                "up",
                "--once",
            ]
        )
        self.assertEqual(code, 0)

    def test_watch_service_warns_about_other_services(self):
        # The Copilot watches one service; evidence for the others can never
        # raise a breach, so the operator should be told.
        errors = StringIO()
        with redirect_stderr(errors):
            main(
                [
                    "--ingest-url",
                    "http://copilot:8000/ingest/events",
                    "--api-key",
                    "k",
                    "--watch-service",
                    "checkout",
                    "--preset",
                    "payment-error-logs",
                    "--jaeger-service",
                    "checkout",
                    "--once",
                ]
            )
        text = errors.getvalue()
        self.assertIn("payment", text)
        self.assertIn("checkout", text)
        self.assertIn("dropped", text)

    def test_watch_service_is_silent_when_every_source_matches(self):
        errors = StringIO()
        with redirect_stderr(errors):
            main(
                [
                    "--ingest-url",
                    "http://copilot:8000/ingest/events",
                    "--api-key",
                    "k",
                    "--watch-service",
                    "checkout",
                    "--preset",
                    "checkout-error-logs",
                    "--jaeger-service",
                    "checkout",
                    "--once",
                ]
            )
        self.assertNotIn("warning", errors.getvalue())

    def test_configured_services_maps_sources_to_services(self):
        args = _parser().parse_args(
            [
                "--preset",
                "checkout-error-ratio",
                "--preset",
                "payment-error-logs",
                "--tempo-service",
                "checkout",
                "--jaeger-service",
                "payment",
            ]
        )
        services = _configured_services(args)
        self.assertEqual(set(services), {"checkout", "payment"})
        self.assertIn("preset:checkout-error-ratio", services["checkout"])
        self.assertIn("tempo:checkout", services["checkout"])
        self.assertIn("jaeger:payment", services["payment"])


if __name__ == "__main__":
    unittest.main()
