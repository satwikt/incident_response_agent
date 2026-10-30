import copy
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.flagd_ui import FLAGD_SCHEMA, FlagdUiClient
from tools.record_scenario import load_catalog
from tools.run_scenario import main, run_scenario, set_variant

ROOT = Path(__file__).parents[1]
SCENARIOS = {row["id"]: row for row in load_catalog()["scenarios"]}

ORIGINAL = {
    "paymentFailure": {
        "defaultVariant": "off",
        "description": "Fail payment service charge requests n%",
        "state": "ENABLED",
        "variants": {"off": 0, "100%": 1},
    }
}


class FakeClient:
    def __init__(self, flags=None):
        self.flags = copy.deepcopy(flags or ORIGINAL)
        self.writes = []

    def read(self):
        return copy.deepcopy(self.flags)

    def write(self, flags):
        payload = copy.deepcopy(dict(flags))
        self.writes.append(payload)
        self.flags = payload


class Clock:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return datetime(2026, 10, 20, 10, self.calls, 0, tzinfo=timezone.utc)


class SetVariantTests(unittest.TestCase):
    def test_sets_variant_without_mutating_input(self):
        updated = set_variant(ORIGINAL, "paymentFailure", "100%")
        self.assertEqual(updated["paymentFailure"]["defaultVariant"], "100%")
        self.assertEqual(ORIGINAL["paymentFailure"]["defaultVariant"], "off")

    def test_rejects_unknown_flag_and_variant(self):
        with self.assertRaises(KeyError):
            set_variant(ORIGINAL, "nope", "off")
        with self.assertRaises(ValueError):
            set_variant(ORIGINAL, "paymentFailure", "bogus")


class RunnerTests(unittest.TestCase):
    def test_toggles_waits_restores_and_records(self):
        client = FakeClient()
        sleeps = []
        with tempfile.TemporaryDirectory() as tmp:
            path, record = run_scenario(
                client,
                SCENARIOS["T-02"],
                duration=12.0,
                out=Path(tmp),
                sleep=sleeps.append,
                now=Clock(),
                observed=["checkout 5xx"],
            )
            self.assertEqual(sleeps, [12.0])
            self.assertEqual(len(client.writes), 2)
            self.assertEqual(
                client.writes[0]["paymentFailure"]["defaultVariant"], "100%"
            )
            self.assertEqual(client.writes[1], ORIGINAL)
            self.assertTrue(record["flag_restored"])
            self.assertEqual(record["scenario_id"], "T-02")
            self.assertTrue(path.is_file())
            self.assertTrue((Path(tmp) / record["flag_snapshot"]).is_file())
            self.assertEqual(
                json.loads(path.read_text())["observed_evidence"], ["checkout 5xx"]
            )

    def test_baseline_scenario_does_not_write(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            _, record = run_scenario(
                client,
                SCENARIOS["T-01"],
                duration=0,
                out=Path(tmp),
                sleep=lambda _: None,
                now=Clock(),
            )
            self.assertEqual(client.writes, [])
            self.assertFalse(record["flag_restored"])
            self.assertIsNone(record["flag"])

    def test_restores_even_when_wait_is_interrupted(self):
        client = FakeClient()

        def interrupted(_):
            raise KeyboardInterrupt()

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyboardInterrupt):
                run_scenario(
                    client,
                    SCENARIOS["T-02"],
                    duration=5,
                    out=Path(tmp),
                    sleep=interrupted,
                    now=Clock(),
                )
            self.assertEqual(client.flags, ORIGINAL)
            self.assertEqual(client.writes[-1], ORIGINAL)
            files = list(Path(tmp).glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].name.endswith(".snapshot.json"))

    def test_invalid_variant_leaves_flags_untouched(self):
        client = FakeClient()
        scenario = dict(SCENARIOS["T-02"], variant="bogus")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_scenario(
                    client,
                    scenario,
                    duration=1,
                    out=Path(tmp),
                    sleep=lambda _: None,
                    now=Clock(),
                )
            self.assertEqual(client.writes, [])
            self.assertEqual(client.flags, ORIGINAL)

    def test_unreachable_flagd_ui_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                code = main(
                    [
                        "T-02",
                        "--flagd-ui-url",
                        "http://127.0.0.1:9/feature",
                        "--duration",
                        "0",
                        "--out",
                        tmp,
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(list(Path(tmp).glob("*")), [])

    def test_dry_run_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                code = main(["T-02", "--dry-run", "--out", tmp])
            self.assertEqual(code, 0)
            self.assertIn("dry run", output.getvalue())
            self.assertEqual(list(Path(tmp).glob("*")), [])


class _Handler(BaseHTTPRequestHandler):
    flags = copy.deepcopy(ORIGINAL)
    writes = []

    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == "/feature/api/read":
            self._send({"flags": _Handler.flags})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802 - http.server API
        if self.path != "/feature/api/write":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        _Handler.writes.append(payload)
        _Handler.flags = payload["data"]["flags"]
        self._send({})

    def log_message(self, *args):
        pass


class FlagdUiHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/feature"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        _Handler.flags = copy.deepcopy(ORIGINAL)
        _Handler.writes = []

    def test_read_and_write_round_trip(self):
        client = FlagdUiClient(self.base)
        flags = client.read()
        self.assertIn("paymentFailure", flags)
        updated = set_variant(flags, "paymentFailure", "100%")
        client.write(updated)
        self.assertEqual(_Handler.flags["paymentFailure"]["defaultVariant"], "100%")
        body = _Handler.writes[0]
        self.assertEqual(body["data"]["$schema"], FLAGD_SCHEMA)
        self.assertIn("flags", body["data"])

    def test_end_to_end_cli_records_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(StringIO()):
                code = main(
                    [
                        "T-02",
                        "--flagd-ui-url",
                        self.base,
                        "--duration",
                        "0",
                        "--out",
                        tmp,
                    ]
                )
            self.assertEqual(code, 0)
            records = [
                path
                for path in Path(tmp).glob("*-T-02.json")
                if ".snapshot." not in path.name
            ]
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text())
            self.assertEqual(record["scenario_id"], "T-02")
            self.assertEqual(_Handler.flags["paymentFailure"]["defaultVariant"], "off")


if __name__ == "__main__":
    unittest.main()
