import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from adapter.events import to_event
from adapter.jaeger import JaegerClient, JaegerTrace, to_evidence as jaeger_evidence
from adapter.loki import LogEntry, LokiClient
from adapter.loki import to_evidence as loki_evidence
from adapter.prometheus import PrometheusClient, Sample
from adapter.prometheus import to_evidence as prom_evidence
from adapter.tempo import TempoClient
from adapter.tempo import to_evidence as tempo_evidence


class PrometheusTests(unittest.TestCase):
    def test_query_parses_vector(self):
        payload = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {
                            "__name__": "http_server_duration_count",
                            "service_name": "checkout",
                            "http_status_code": "500",
                            "http_route": "/checkout",
                        },
                        "value": [1700000000.0, "7"],
                    }
                ],
            },
        }
        with patch("adapter.prometheus.get_json", return_value=payload) as fake:
            samples = PrometheusClient("http://prometheus:9090").query("up", at=1700000000)
        self.assertEqual(fake.call_args.args[1], "api/v1/query")
        self.assertEqual(fake.call_args.args[2]["query"], "up")
        self.assertEqual(fake.call_args.args[2]["time"], 1700000000)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].labels["service_name"], "checkout")

        evidence = prom_evidence(samples[0])
        self.assertEqual(evidence.service, "checkout")
        self.assertEqual(evidence.status, 500)
        self.assertEqual(evidence.route, "/checkout")
        self.assertEqual(evidence.source, "prometheus")
        self.assertEqual(evidence.level, "WARNING")

    def test_query_range_parses_matrix(self):
        payload = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"__name__": "up", "service_name": "ad"},
                        "values": [[1700000000.0, "1"], [1700000030.0, "1"]],
                    }
                ],
            },
        }
        with patch("adapter.prometheus.get_json", return_value=payload):
            samples = PrometheusClient("http://prometheus:9090").query_range(
                "up", 1700000000, 1700000060, "30s"
            )
        self.assertEqual([sample.value for sample in samples], [1.0, 1.0])
        self.assertEqual(
            [sample.timestamp for sample in samples], [1700000000.0, 1700000030.0]
        )

    def test_repeated_polls_produce_stable_ids(self):
        sample = Sample("up", {"__name__": "up", "service_name": "ad"}, 1.0, 1700000000.0)
        self.assertEqual(prom_evidence(sample).id, prom_evidence(sample).id)
        later = Sample("up", {"__name__": "up", "service_name": "ad"}, 1.0, 1700000060.0)
        self.assertNotEqual(prom_evidence(sample).id, prom_evidence(later).id)

    def test_failed_query_raises(self):
        with patch(
            "adapter.prometheus.get_json",
            return_value={"status": "error", "error": "bad query"},
        ):
            with self.assertRaises(ValueError):
                PrometheusClient("http://prometheus:9090").query("up")


class LokiTests(unittest.TestCase):
    def test_query_parses_streams_and_preserves_trace_id(self):
        payload = {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {
                            "service_name": "checkout",
                            "trace_id": "abc123",
                            "span_id": "def456",
                        },
                        "values": [
                            [
                                "1700000000000000000",
                                '{"level":"error","message":"payment call failed"}',
                            ]
                        ],
                    }
                ],
            },
        }
        with patch("adapter.loki.get_json", return_value=payload) as fake:
            entries = LokiClient("http://loki:3100").query_range(
                '{service_name="checkout"}', 1, 1700000000000000000
            )
        self.assertEqual(fake.call_args.args[1], "loki/api/v1/query_range")
        self.assertEqual(fake.call_args.args[2]["limit"], 100)
        self.assertEqual(len(entries), 1)

        event = to_event(loki_evidence(entries[0]))
        self.assertEqual(event["level"], "ERROR")
        self.assertEqual(event["service"], "checkout")
        self.assertIn("trace_id=abc123", event["message"])
        self.assertIn("span_id=def456", event["message"])

    def test_request_log_line_carries_route_status_and_duration(self):
        line = '{"level":"ERROR","message":"POST /checkout -> 500 (10.9ms)","exception":"Boom"}'
        evidence = loki_evidence(LogEntry(1700000000000000000, line, {"service_name": "checkout"}))
        self.assertEqual(evidence.route, "POST /checkout")
        self.assertEqual(evidence.status, 500)
        self.assertAlmostEqual(evidence.duration_ms, 10.9)
        self.assertEqual(evidence.exception, "Boom")
        event = to_event(evidence)
        self.assertEqual((event["route"], event["status"]), ("POST /checkout", 500))

    def test_healthy_request_line_has_status_200(self):
        line = '{"level":"INFO","message":"GET /products -> 200 (3.0ms)"}'
        evidence = loki_evidence(LogEntry(1700000000000000000, line, {"service_name": "catalog"}))
        self.assertEqual((evidence.route, evidence.status), ("GET /products", 200))

    def test_non_request_lines_have_no_route_or_status(self):
        for line in ("plain text", '{"message":"Checkout payment_failure fault active"}', "[1,2]"):
            evidence = loki_evidence(LogEntry(1700000000000000000, line, {"service_name": "checkout"}))
            self.assertEqual((evidence.route, evidence.status, evidence.duration_ms), (None, None, None))

    def test_invalid_level_falls_back_to_info(self):
        entry = LogEntry(1700000000000000000, "plain text line", {"service_name": "ad"})
        self.assertEqual(loki_evidence(entry).level, "INFO")


class TempoTests(unittest.TestCase):
    def test_trace_evidence_id_is_stable_per_backend(self):
        from adapter.tempo import TraceSummary

        summary = TraceSummary("4f1a", "checkout", "POST /checkout", 1, 1.0)
        self.assertEqual(tempo_evidence(summary).id, tempo_evidence(summary).id)
        self.assertTrue(tempo_evidence(summary).id.startswith("trace-tempo:"))

    def test_search_builds_tag_filter_and_evidence(self):
        payload = {
            "traces": [
                {
                    "traceID": "4f1a",
                    "rootServiceName": "checkout",
                    "rootTraceName": "POST /checkout",
                    "startTimeUnixNano": "1700000000000000000",
                    "durationMs": 812.5,
                }
            ]
        }
        with patch("adapter.tempo.get_json", return_value=payload) as fake:
            summaries = TempoClient("http://tempo:3200").search(
                service="checkout", start_ns=1, end_ns=1700000000000000000
            )
        self.assertEqual(fake.call_args.args[1], "api/search")
        self.assertEqual(fake.call_args.args[2]["tags"], "service.name=checkout")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].trace_id, "4f1a")

        evidence = tempo_evidence(summaries[0])
        self.assertEqual(evidence.service, "checkout")
        self.assertEqual(evidence.trace_id, "4f1a")
        self.assertEqual(evidence.duration_ms, 812.5)


class JaegerTests(unittest.TestCase):
    def test_find_traces_and_root_evidence(self):
        payload = {
            "data": [
                {
                    "traceID": "4f1a",
                    "spans": [
                        {
                            "spanID": "root",
                            "operationName": "POST /checkout",
                            "startTime": 1700000000000000,
                            "duration": 812500,
                            "processID": "p1",
                            "tags": [
                                {"key": "otel.status_code", "value": "ERROR"},
                                {"key": "http.status_code", "value": 500},
                                {"key": "http.route", "value": "/checkout"},
                            ],
                            "references": [],
                        },
                        {
                            "spanID": "child",
                            "operationName": "charge",
                            "startTime": 1700000000100000,
                            "duration": 1000,
                            "processID": "p2",
                            "tags": [],
                            "references": [{"refType": "CHILD_OF", "spanID": "root"}],
                        },
                    ],
                    "processes": {
                        "p1": {"serviceName": "checkout"},
                        "p2": {"serviceName": "payment"},
                    },
                }
            ]
        }
        with patch("adapter.jaeger.get_json", return_value=payload) as fake:
            traces = JaegerClient("http://jaeger:16686").find_traces(
                "checkout", 1, 1700000000000000
            )
        self.assertEqual(fake.call_args.args[1], "/api/traces")
        self.assertEqual(traces[0].trace_id, "4f1a")

        evidence = jaeger_evidence(traces[0])
        self.assertEqual(evidence.service, "checkout")
        self.assertEqual(evidence.level, "ERROR")
        self.assertEqual(evidence.status, 500)
        self.assertEqual(evidence.route, "/checkout")
        self.assertAlmostEqual(evidence.duration_ms, 812.5)
        self.assertEqual(evidence.span_id, "root")

    def test_api_prefix_carries_the_jaeger_base_path(self):
        # Jaeger v2 registers its REST API under base_path, so the upstream
        # stack serves /jaeger/ui/api/traces, not /api/traces.
        with patch("adapter.jaeger.get_json", return_value={"data": []}) as fake:
            JaegerClient(
                "http://localhost:8080/jaeger/ui", api_prefix="/api"
            ).find_traces("checkout", 1, 2)
        self.assertEqual(fake.call_args.args[0], "http://localhost:8080/jaeger/ui")
        self.assertEqual(fake.call_args.args[1], "/api/traces")

        with patch("adapter.jaeger.get_json", return_value={"data": []}) as fake:
            JaegerClient(
                "http://localhost:16686", api_prefix="/jaeger/ui/api"
            ).find_traces("checkout", 1, 2)
        self.assertEqual(fake.call_args.args[1], "/jaeger/ui/api/traces")

    def test_semconv_attribute_names_are_read(self):
        # The demo emits OTel semantic conventions on spans, not the
        # underscored Prometheus spelling.
        trace = JaegerTrace(
            "4f1a",
            [
                {
                    "spanID": "root",
                    "operationName": "POST /checkout",
                    "startTime": 1700000000000000,
                    "duration": 812500,
                    "processID": "p1",
                    "tags": [
                        {"key": "http.response.status_code", "value": 503},
                        {"key": "http.route", "value": "/checkout"},
                        {"key": "http.request.method", "value": "POST"},
                    ],
                    "references": [],
                }
            ],
            {"p1": {"serviceName": "checkout"}},
        )
        evidence = jaeger_evidence(trace)
        self.assertEqual(evidence.status, 503)
        self.assertEqual(evidence.route, "POST /checkout")

    def test_route_without_a_method_is_left_alone(self):
        trace = JaegerTrace(
            "4f1a",
            [
                {
                    "spanID": "root",
                    "operationName": "GET /cart",
                    "startTime": 1700000000000000,
                    "duration": 1000,
                    "processID": "p1",
                    "tags": [{"key": "http.route", "value": "GET /cart"}],
                    "references": [],
                }
            ],
            {"p1": {"serviceName": "checkout"}},
        )
        self.assertEqual(jaeger_evidence(trace).route, "GET /cart")


class PrometheusAttributeTests(unittest.TestCase):
    def test_demo_prometheus_labels_are_read(self):
        # The Prometheus exporter underscores the semantic conventions.
        sample = Sample(
            "http_server_request_duration_seconds_count",
            {
                "service_name": "checkout",
                "http_response_status_code": "500",
                "http_route": "/checkout",
            },
            12.0,
            1700000000.0,
        )
        evidence = prom_evidence(sample)
        self.assertEqual(evidence.status, 500)
        self.assertEqual(evidence.route, "/checkout")

    def test_non_http_status_label_is_not_treated_as_a_status(self):
        # traces_span_metrics_calls_total carries status_code=STATUS_CODE_ERROR,
        # which is not an HTTP status and must not become an ingest status.
        sample = Sample(
            "traces_span_metrics_calls_total",
            {"service_name": "payment", "status_code": "STATUS_CODE_ERROR"},
            1.0,
            1700000000.0,
        )
        self.assertIsNone(prom_evidence(sample).status)


if __name__ == "__main__":
    unittest.main()
