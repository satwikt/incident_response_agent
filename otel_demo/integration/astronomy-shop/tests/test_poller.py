import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).parents[1]))

from adapter.client import build_events
from adapter.events import Evidence
from adapter.poller import Poller, RetryPolicy, chunk_events, parse_ingest_keys
from adapter.sources import jaeger_source, loki_source, prometheus_source, tempo_source


def evidence(count):
    return [
        Evidence(service="checkout", level="ERROR", message=f"failure {index}")
        for index in range(count)
    ]


class BatchTests(unittest.TestCase):
    def test_chunking_respects_limit(self):
        batches = chunk_events(build_events(evidence(1201)), limit=500)
        self.assertEqual([len(batch) for batch in batches], [500, 500, 201])

    def test_chunking_rejects_oversized_limit(self):
        with self.assertRaises(ValueError):
            chunk_events(build_events(evidence(1)), limit=501)

    def test_chunking_respects_body_bytes(self):
        rows = build_events(
            [Evidence(service="checkout", level="ERROR", message="x" * 400) for _ in range(10)]
        )
        batches = chunk_events(rows, max_bytes=1000)
        self.assertGreater(len(batches), 1)
        for batch in batches:
            self.assertLessEqual(len(json.dumps(batch, separators=(",", ":"))), 1000)

    def test_chunking_keeps_an_oversized_single_event(self):
        rows = build_events([Evidence(service="checkout", level="ERROR", message="x" * 2000)])
        batches = chunk_events(rows, max_bytes=100)
        self.assertEqual([len(batch) for batch in batches], [1])


class PollerTests(unittest.TestCase):
    def make_poller(self, sender, *, attempts=3, sleep=None):
        return Poller(
            "http://copilot:8000/ingest/events",
            "secret",
            sender=sender,
            policy=RetryPolicy(attempts=attempts, base_delay=0.5, max_delay=4.0),
            sleep=sleep or (lambda _: None),
        )

    def test_sends_each_batch_and_parses_receipt(self):
        calls = []

        def sender(url, key, batch, *, timeout):
            calls.append((url, key, len(batch), timeout))
            return b'{"accepted": %d, "duplicates": 0}' % len(batch)

        poller = self.make_poller(sender, attempts=1)
        report = poller.run_once({"prom": lambda: evidence(3)})
        self.assertEqual(report.fetched, 3)
        self.assertEqual(report.accepted, 3)
        self.assertEqual(report.dropped, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "secret")
        self.assertEqual(calls[0][2], 3)
        self.assertEqual(poller.totals.accepted, 3)

    def test_retries_transient_failures_with_backoff(self):
        attempts = []
        sleeps = []

        def sender(url, key, batch, *, timeout):
            attempts.append(1)
            if len(attempts) < 3:
                raise URLError("connection refused")
            return b'{"accepted": 1, "duplicates": 0}'

        poller = self.make_poller(sender, attempts=3, sleep=sleeps.append)
        report = poller.run_once({"prom": lambda: evidence(1)})
        self.assertEqual(report.accepted, 1)
        self.assertEqual(report.retries, 2)
        self.assertEqual(sleeps, [0.5, 1.0])

    def test_non_retryable_error_drops_batch_without_sleep(self):
        sleeps = []

        def sender(url, key, batch, *, timeout):
            raise HTTPError(url, 401, "unauthorized", {}, None)

        poller = self.make_poller(sender, attempts=3, sleep=sleeps.append)
        report = poller.run_once({"prom": lambda: evidence(2)})
        self.assertEqual(report.dropped, 2)
        self.assertEqual(report.accepted, 0)
        self.assertEqual(report.failed_batches, 1)
        self.assertEqual(sleeps, [])
        self.assertIn("401", report.source_errors["prom:ingest"])

    def test_exhausted_retries_are_counted_as_dropped(self):
        attempts = []

        def sender(url, key, batch, *, timeout):
            attempts.append(1)
            raise HTTPError(url, 503, "unavailable", {}, None)

        poller = self.make_poller(sender, attempts=3)
        report = poller.run_once({"prom": lambda: evidence(2)})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(report.retries, 2)
        self.assertEqual(report.dropped, 2)

    def test_source_failure_is_isolated(self):
        def broken():
            raise RuntimeError("loki down")

        def healthy():
            return evidence(1)

        def sender(url, key, batch, *, timeout):
            return b'{"accepted": 1}'

        poller = self.make_poller(sender, attempts=1)
        report = poller.run_once({"loki": broken, "prom": healthy})
        self.assertEqual(report.fetched, 1)
        self.assertEqual(report.accepted, 1)
        self.assertIn("RuntimeError: loki down", report.source_errors["loki"])

    def test_run_forever_stops_at_max_cycles(self):
        def sender(url, key, batch, *, timeout):
            return b'{"accepted": 1}'

        sleeps = []
        poller = self.make_poller(sender, attempts=1, sleep=sleeps.append)
        reports = list(
            poller.run_forever({"prom": lambda: evidence(1)}, interval=30, max_cycles=2)
        )
        self.assertEqual(len(reports), 2)
        self.assertEqual(sleeps, [30])


class ServiceKeyTests(unittest.TestCase):
    """The Copilot binds each ingest key to one service and rejects a batch
    naming any other service, so batches must be per service."""

    def test_parse_ingest_keys_maps_service_to_key(self):
        self.assertEqual(
            parse_ingest_keys("checkout:k1,payment:k2"),
            {"checkout": "k1", "payment": "k2"},
        )

    def test_parse_ingest_keys_ignores_malformed_pairs(self):
        self.assertEqual(parse_ingest_keys("checkout:k1,broken,,:k2,"), {"checkout": "k1"})
        self.assertEqual(parse_ingest_keys(""), {})

    def test_each_service_is_sent_with_its_own_key(self):
        calls = []

        def sender(url, key, batch, *, timeout):
            calls.append((key, [event["service"] for event in batch]))
            return b'{"accepted": %d}' % len(batch)

        poller = Poller(
            "http://copilot:8000/ingest/events",
            "",
            sender=sender,
            sleep=lambda _: None,
            service_keys={"checkout": "k-checkout", "payment": "k-payment"},
        )
        report = poller.run_once(
            {
                "prom": lambda: [
                    Evidence(service="payment", level="ERROR", message="p"),
                    Evidence(service="checkout", level="ERROR", message="c"),
                    Evidence(service="payment", level="ERROR", message="p2"),
                ]
            }
        )
        self.assertEqual(report.accepted, 3)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], ("k-checkout", ["checkout"]))
        self.assertEqual(calls[1], ("k-payment", ["payment", "payment"]))

    def test_batch_never_mixes_services(self):
        calls = []

        def sender(url, key, batch, *, timeout):
            calls.append([event["service"] for event in batch])
            return b'{"accepted": %d}' % len(batch)

        poller = Poller(
            "http://copilot:8000/ingest/events",
            "",
            sender=sender,
            sleep=lambda _: None,
            service_keys={"checkout": "k-checkout", "payment": "k-payment"},
        )
        poller.run_once(
            {
                "prom": lambda: [
                    Evidence(service="checkout", level="ERROR", message="c"),
                    Evidence(service="payment", level="ERROR", message="p"),
                ]
            },
        )
        for services in calls:
            self.assertEqual(len(set(services)), 1)

    def test_service_without_a_key_is_dropped_and_reported(self):
        calls = []

        def sender(url, key, batch, *, timeout):
            calls.append(key)
            return b'{"accepted": %d}' % len(batch)

        poller = Poller(
            "http://copilot:8000/ingest/events",
            "default-key",
            sender=sender,
            sleep=lambda _: None,
            service_keys={"checkout": "k-checkout"},
        )
        report = poller.run_once(
            {
                "prom": lambda: [
                    Evidence(service="checkout", level="ERROR", message="c"),
                    Evidence(service="shipping", level="ERROR", message="s"),
                ]
            }
        )
        self.assertEqual(calls, ["k-checkout"])
        self.assertEqual(report.dropped, 1)
        self.assertIn("no ingest key", report.source_errors["prom:ingest-keys"])

    def test_without_service_keys_the_default_key_is_used(self):
        calls = []

        def sender(url, key, batch, *, timeout):
            calls.append(key)
            return b'{"accepted": %d}' % len(batch)

        poller = Poller(
            "http://copilot:8000/ingest/events",
            "secret",
            sender=sender,
            sleep=lambda _: None,
        )
        poller.run_once({"prom": lambda: evidence(2)})
        self.assertEqual(calls, ["secret"])

    def test_only_the_watched_service_is_sent(self):
        calls = []

        def sender(url, key, batch, *, timeout):
            calls.append([event["service"] for event in batch])
            return b'{"accepted": %d}' % len(batch)

        poller = Poller(
            "http://copilot:8000/ingest/events",
            "secret",
            sender=sender,
            sleep=lambda _: None,
            watch_service="checkout",
        )
        report = poller.run_once(
            {
                "prom": lambda: [
                    Evidence(service="checkout", level="ERROR", message="c"),
                    Evidence(service="payment", level="ERROR", message="p"),
                ]
            }
        )
        self.assertEqual(calls, [["checkout"]])
        self.assertEqual(report.accepted, 1)
        self.assertEqual(report.dropped, 1)
        self.assertIn(
            "not the watched service", report.source_errors["prom:ingest-keys"]
        )


class _FakePrometheus:
    def __init__(self):
        self.calls = []

    def query(self, expression, *, at=None):
        from adapter.prometheus import Sample

        self.calls.append(expression)
        return [
            Sample(
                "up",
                {"__name__": "up", "service_name": "checkout"},
                1.0,
                1700000000.0,
            )
        ]


class _FakeLoki:
    def __init__(self):
        self.calls = []

    def query_range(self, query, start_ns, end_ns, *, limit=100, direction="backward"):
        from adapter.loki import LogEntry

        self.calls.append((query, start_ns, end_ns, limit))
        return [LogEntry(1700000000000000000, "boom", {"service_name": "checkout"})]


class _FakeTempo:
    def __init__(self):
        self.calls = []

    def search(self, *, service, start_ns, end_ns, limit=20, tags=None):
        from adapter.tempo import TraceSummary

        self.calls.append((service, start_ns, end_ns, limit))
        return [TraceSummary("abc", "checkout", "POST /checkout", 1700000000000000000, 12.0)]


class _FakeJaeger:
    def __init__(self):
        self.calls = []

    def find_traces(self, service, start_us, end_us, *, limit=20, operation=None):
        from adapter.jaeger import JaegerTrace

        self.calls.append((service, start_us, end_us, limit))
        return [
            JaegerTrace(
                "abc",
                [
                    {
                        "spanID": "root",
                        "operationName": "POST /checkout",
                        "startTime": 1700000000000000,
                        "duration": 12000,
                        "processID": "p1",
                        "tags": [],
                        "references": [],
                    }
                ],
                {"p1": {"serviceName": "checkout"}},
            )
        ]


class SourceTests(unittest.TestCase):
    def test_prometheus_source_converts_samples(self):
        client = _FakePrometheus()
        items = prometheus_source(client, "up")()
        self.assertEqual(client.calls, ["up"])
        self.assertEqual(items[0].service, "checkout")
        self.assertEqual(items[0].source, "prometheus")

    def test_loki_source_uses_trailing_window(self):
        client = _FakeLoki()
        items = loki_source(client, '{service_name="checkout"}', window_seconds=300, now=lambda: 1000.0)()
        query, start_ns, end_ns, limit = client.calls[0]
        self.assertEqual(end_ns, 1_000_000_000_000)
        self.assertEqual(start_ns, 700_000_000_000)
        self.assertEqual(limit, 100)
        self.assertEqual(items[0].service, "checkout")

    def test_tempo_source_uses_nanosecond_window(self):
        client = _FakeTempo()
        items = tempo_source(client, "checkout", window_seconds=60, now=lambda: 1000.0)()
        _, start_ns, end_ns, _ = client.calls[0]
        self.assertEqual(end_ns, 1_000_000_000_000)
        self.assertEqual(start_ns, 940_000_000_000)
        self.assertEqual(items[0].trace_id, "abc")

    def test_jaeger_source_uses_microsecond_window(self):
        client = _FakeJaeger()
        items = jaeger_source(client, "checkout", window_seconds=60, now=lambda: 1000.0)()
        _, start_us, end_us, _ = client.calls[0]
        self.assertEqual(end_us, 1_000_000_000)
        self.assertEqual(start_us, 940_000_000)
        self.assertEqual(items[0].trace_id, "abc")


if __name__ == "__main__":
    unittest.main()
