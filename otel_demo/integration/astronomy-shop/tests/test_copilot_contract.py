"""Exercise the adapter against the Copilot's real ingest contract.

The rules are transcribed from the Copilot repository at commit
``0ce98443db0b0b3c09c40a53849164c1f7160fe9``; see ``copilot_contract.py``.
These tests run the adapter's own HTTP client against a local server that
enforces them, so they cover the path the adapter actually takes in
production -- not a stub that accepts anything.
"""

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from adapter.events import Evidence
from adapter.poller import Poller, RetryPolicy, parse_ingest_keys
from tests.copilot_contract import ContractServer, CopilotIngest


KEYS = {"key-checkout": "checkout", "key-payment": "payment"}


def evidence(service, count=1, **kwargs):
    return [
        Evidence(service=service, level="ERROR", message=f"failure {index}", **kwargs)
        for index in range(count)
    ]


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.ingest = CopilotIngest(KEYS)
        self.server = ContractServer(self.ingest)
        self.addCleanup(self.server.close)

    def poller(self, *, keys=None, watch=None, attempts=1):
        return Poller(
            self.server.url,
            "key-checkout",
            policy=RetryPolicy(attempts=attempts),
            sleep=lambda _: None,
            service_keys=parse_ingest_keys(keys) if keys is not None else None,
            watch_service=watch,
        )

    def test_single_service_batch_is_accepted(self):
        report = self.poller().run_once({"prom": lambda: evidence("checkout", 3)})
        self.assertEqual(report.accepted, 3)
        self.assertEqual(report.dropped, 0)
        self.assertEqual(report.source_errors, {})

    def test_services_are_sent_with_their_own_key(self):
        poller = self.poller(keys="checkout:key-checkout,payment:key-payment")
        report = poller.run_once(
            {
                "prom": lambda: (
                    evidence("payment", 2) + evidence("checkout", 1)
                )
            }
        )
        self.assertEqual(report.accepted, 3)
        self.assertEqual(report.dropped, 0)
        self.assertEqual(report.source_errors, {})

    def test_a_mixed_batch_would_be_rejected_so_the_adapter_never_sends_one(self):
        # Proves the grouping matters: a raw mixed batch is refused by the
        # contract, so the adapter's per-service split is what makes it work.
        now = time.time()
        body = json.dumps(
            [
                {"id": "a", "ts": now, "level": "ERROR", "service": "checkout"},
                {"id": "b", "ts": now, "level": "ERROR", "service": "payment"},
            ]
        ).encode()
        status, _ = self.ingest.handle("key-checkout", body)
        self.assertEqual(status, 403)

    def test_service_without_a_key_is_dropped_not_sent(self):
        report = self.poller(keys="checkout:key-checkout").run_once(
            {"prom": lambda: evidence("checkout", 1) + evidence("shipping", 1)}
        )
        self.assertEqual(report.accepted, 1)
        self.assertEqual(report.dropped, 1)
        self.assertIn("no ingest key", report.source_errors["prom:ingest-keys"])

    def test_unwatched_service_is_dropped(self):
        report = self.poller(watch="checkout").run_once(
            {"prom": lambda: evidence("checkout", 1) + evidence("payment", 2)}
        )
        self.assertEqual(report.accepted, 1)
        self.assertEqual(report.dropped, 2)
        self.assertIn(
            "not the watched service", report.source_errors["prom:ingest-keys"]
        )

    def test_repeated_poll_dedupes(self):
        # The same evidence polled twice must produce the same event id, or
        # the Copilot's store fills with duplicates instead of deduping.
        stamp = time.time()

        def poll():
            return self.poller().run_once(
                {
                    "prom": lambda: [
                        Evidence(
                            service="checkout",
                            level="ERROR",
                            message=f"failure {index}",
                            timestamp=stamp,
                        )
                        for index in range(2)
                    ]
                }
            )

        first = poll()
        second = poll()
        self.assertEqual(first.accepted, 2)
        self.assertEqual(second.accepted, 0)
        self.assertEqual(second.duplicates, 2)

    def test_out_of_range_status_is_dropped_so_the_batch_survives(self):
        # The contract rejects a whole batch when one event is invalid, so the
        # adapter must not emit an out-of-range status in the first place.
        report = self.poller().run_once(
            {
                "prom": lambda: [
                    Evidence(
                        service="checkout",
                        level="ERROR",
                        message="bad status",
                        status=1,
                    ),
                    Evidence(
                        service="checkout",
                        level="ERROR",
                        message="good status",
                        status=500,
                    ),
                ]
            }
        )
        self.assertEqual(report.accepted, 2)
        self.assertEqual(report.dropped, 0)

    def test_oversized_route_is_truncated_by_the_adapter(self):
        # The contract caps the route length and rejects a longer one along
        # with the whole batch, so the adapter must truncate it first.
        report = self.poller().run_once(
            {
                "prom": lambda: [
                    Evidence(
                        service="checkout",
                        level="ERROR",
                        message="x" * 5000,
                        route="/" + "y" * 400,
                    )
                ]
            }
        )
        self.assertEqual(report.accepted, 1)
        self.assertEqual(report.dropped, 0)

    def test_a_batch_over_the_event_limit_is_rejected_by_the_contract(self):
        # The adapter must never send more than 500 events in one request.
        report = self.poller().run_once({"prom": lambda: evidence("checkout", 1200)})
        self.assertEqual(report.accepted, 1200)
        self.assertEqual(report.dropped, 0)

    def test_unauthorized_key_is_reported_not_retried(self):
        poller = Poller(
            self.server.url,
            "wrong-key",
            policy=RetryPolicy(attempts=3),
            sleep=lambda _: None,
        )
        report = poller.run_once({"prom": lambda: evidence("checkout", 1)})
        self.assertEqual(report.accepted, 0)
        self.assertEqual(report.failed_batches, 1)
        self.assertEqual(report.retries, 0)
        self.assertIn("401", report.source_errors["prom:ingest"])


if __name__ == "__main__":
    unittest.main()
