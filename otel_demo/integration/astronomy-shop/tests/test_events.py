import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from adapter.events import Evidence, from_mapping, to_event


class EventContractTests(unittest.TestCase):
    def test_event_preserves_correlation_context(self):
        event = to_event(
            Evidence(
                service="checkout",
                level="error",
                route="/checkout",
                status=500,
                duration_ms=42.5,
                message="payment call failed",
                exception="PaymentUnavailable",
                trace_id="abc123",
                span_id="def456",
                source="tempo",
            )
        )
        self.assertEqual(event["service"], "checkout")
        self.assertEqual(event["level"], "ERROR")
        self.assertEqual(event["status"], 500)
        self.assertIn("trace_id=abc123", event["message"])
        self.assertIn("span_id=def456", event["message"])

    def test_mapping_defaults_to_safe_info_event(self):
        evidence = from_mapping(
            {"service": "payment", "message": "healthy"}, source="prometheus"
        )
        event = to_event(evidence)
        self.assertEqual(event["level"], "INFO")
        self.assertIsNone(event["exception"])
        self.assertIn("source=prometheus", event["message"])

    def test_required_fields_are_rejected(self):
        for field in ("service", "message"):
            values = {"service": "checkout", "message": "failure"}
            values[field] = ""
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    to_event(from_mapping(values))

    def test_invalid_level_is_rejected(self):
        with self.assertRaises(ValueError):
            to_event(Evidence(service="checkout", level="notice", message="x"))

    def test_explicit_id_is_preserved_and_validated(self):
        event = to_event(
            Evidence(service="checkout", level="ERROR", message="x", id="trace-tempo:abc")
        )
        self.assertEqual(event["id"], "trace-tempo:abc")
        with self.assertRaises(ValueError):
            to_event(Evidence(service="checkout", level="ERROR", message="x", id="bad id!"))

    def test_out_of_range_status_is_dropped_not_forwarded(self):
        # The Copilot validates a batch atomically, so one bad status would
        # reject every other event in the batch.
        for status in (0, 99, 600, 1000):
            with self.subTest(status=status):
                event = to_event(
                    Evidence(service="checkout", level="ERROR", message="x", status=status)
                )
                self.assertIsNone(event["status"])

    def test_out_of_range_duration_is_dropped_not_forwarded(self):
        for duration in (-1.0, 3_600_001.0, float("inf"), float("nan")):
            with self.subTest(duration=duration):
                event = to_event(
                    Evidence(
                        service="checkout", level="ERROR", message="x", duration_ms=duration
                    )
                )
                self.assertIsNone(event["duration_ms"])

    def test_valid_status_and_duration_are_preserved(self):
        event = to_event(
            Evidence(
                service="checkout", level="ERROR", message="x", status=200, duration_ms=12.5
            )
        )
        self.assertEqual(event["status"], 200)
        self.assertEqual(event["duration_ms"], 12.5)

    def test_oversized_route_is_truncated(self):
        # The Copilot caps the route length and rejects a longer one along
        # with the whole batch it arrives in.
        event = to_event(
            Evidence(service="checkout", level="ERROR", message="x", route="/" + "y" * 400)
        )
        self.assertEqual(len(event["route"]), 200)

    def test_oversized_exception_is_truncated(self):
        event = to_event(
            Evidence(
                service="checkout", level="ERROR", message="x", exception="e" * 9000
            )
        )
        self.assertEqual(len(event["exception"]), 8192)

    def test_derived_id_is_stable_for_the_same_evidence(self):
        # The Copilot deduplicates on the id, so the same evidence polled
        # twice must produce the same one.
        evidence = Evidence(
            service="checkout",
            level="ERROR",
            message="failure",
            timestamp=1700000000.0,
        )
        first = to_event(evidence)["id"]
        second = to_event(evidence)["id"]
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("ev-"))

    def test_derived_id_differs_for_different_evidence(self):
        one = to_event(
            Evidence(service="checkout", level="ERROR", message="a", timestamp=1.0)
        )["id"]
        two = to_event(
            Evidence(service="checkout", level="ERROR", message="b", timestamp=1.0)
        )["id"]
        self.assertNotEqual(one, two)


if __name__ == "__main__":
    unittest.main()
