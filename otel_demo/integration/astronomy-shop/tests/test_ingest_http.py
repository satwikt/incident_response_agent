import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from adapter.events import Evidence
from adapter.poller import Poller, RetryPolicy


class _Handler(BaseHTTPRequestHandler):
    captured = {}

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        _Handler.captured = {
            "path": self.path,
            "api_key": self.headers.get("X-Api-Key"),
            "body": body,
        }
        payload = json.dumps(
            {"accepted": len(json.loads(body)), "duplicates": 0}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class IngestHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_poller_posts_valid_batch_with_api_key(self):
        port = self.server.server_address[1]
        poller = Poller(
            f"http://127.0.0.1:{port}/ingest/events",
            "test-key",
            policy=RetryPolicy(attempts=1),
        )
        report = poller.run_once(
            {
                "checkout": lambda: [
                    Evidence(
                        service="checkout",
                        level="ERROR",
                        message="payment call failed",
                        trace_id="abc123",
                    )
                ]
            }
        )
        self.assertEqual(report.accepted, 1)
        self.assertEqual(report.dropped, 0)
        self.assertEqual(_Handler.captured["path"], "/ingest/events")
        self.assertEqual(_Handler.captured["api_key"], "test-key")
        body = json.loads(_Handler.captured["body"])
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["service"], "checkout")
        self.assertEqual(body[0]["level"], "ERROR")
        self.assertIn("trace_id=abc123", body[0]["message"])


if __name__ == "__main__":
    unittest.main()
