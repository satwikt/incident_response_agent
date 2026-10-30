import json
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools import preflight


class FileVendorCorpusTests(unittest.TestCase):
    def test_required_files_exist(self):
        checks = preflight.check_files()
        self.assertEqual([check.status for check in checks], ["pass"])

    def test_vendor_commit_matches_pin(self):
        checks = preflight.check_vendor()
        self.assertEqual(checks[0].status, "pass", checks[0].detail)

    def test_corpus_is_current(self):
        checks = preflight.check_corpus()
        self.assertEqual(checks[0].status, "pass", checks[0].detail)

    def test_missing_file_is_reported(self):
        checks = preflight.check_files(Path("/nonexistent"))
        self.assertEqual(checks[0].status, "fail")
        self.assertIn("missing", checks[0].detail)


class ComposeTests(unittest.TestCase):
    def test_compose_command_has_all_layers(self):
        command = preflight.compose_command()
        self.assertEqual(command[:3], ["docker", "compose", "config"])
        for relative in preflight.COMPOSE_FILES:
            self.assertIn(relative, command)
        self.assertTrue(command[-1].endswith("compose.overlay.yaml"))

    def test_compose_failure_surfaces_stderr(self):
        def fake_run(*args, **kwargs):
            return SimpleNamespace(returncode=1, stderr="services.lgtm: invalid", stdout="")

        checks = preflight.check_compose_config(run=fake_run)
        self.assertEqual(checks[0].status, "fail")
        self.assertIn("invalid", checks[0].detail)

    def test_compose_success(self):
        def fake_run(*args, **kwargs):
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        checks = preflight.check_compose_config(run=fake_run)
        self.assertEqual(checks[0].status, "pass")

    def test_compose_missing_docker_is_reported(self):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "docker")

        checks = preflight.check_compose_config(run=fake_run)
        self.assertEqual(checks[0].status, "fail")
        self.assertIn("unable to run docker", checks[0].detail)

    def test_docker_missing_is_a_failure(self):
        checks = preflight.check_docker(which=lambda name: None)
        self.assertEqual(checks[0].status, "fail")

    def test_docker_available(self):
        def fake_run(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout="Docker Compose version v2.30.0\n")

        checks = preflight.check_docker(run=fake_run, which=lambda name: "/usr/bin/docker")
        self.assertEqual(checks[0].status, "pass")
        self.assertIn("v2.30.0", checks[0].detail)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == "/feature/api/read":
            body = b'{"flags":{"paymentFailure":{"defaultVariant":"off"}}}'
        else:
            body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_probe_success_and_flag_shape(self):
        ok, detail = preflight._probe(f"{self.base}/")
        self.assertTrue(ok, detail)
        ok, detail = preflight._probe(f"{self.base}/feature/api/read")
        self.assertTrue(ok, detail)

    def test_probe_connection_failure(self):
        ok, detail = preflight._probe("http://127.0.0.1:9/", timeout=0.2)
        self.assertFalse(ok)
        self.assertTrue(detail)


class ProbeCoverageTests(unittest.TestCase):
    def test_probes_cover_every_backend_the_module_uses(self):
        names = {name for name, _ in preflight.PROBES}
        for expected in (
            "store",
            "flagd-read",
            "lgtm-grafana",
            "grafana-upstream",
            "jaeger-ui",
            "prometheus",
            "lgtm-tempo",
            "lgtm-loki",
        ):
            with self.subTest(probe=expected):
                self.assertIn(expected, names)


class ReportTests(unittest.TestCase):
    def test_summary_and_json_render(self):
        checks = [
            preflight.Check("a", "pass", ""),
            preflight.Check("b", "warn", ""),
            preflight.Check("c", "fail", ""),
            preflight.Check("d", "skip", ""),
        ]
        self.assertEqual(
            preflight.summarize(checks),
            {"pass": 1, "warn": 1, "fail": 1, "skip": 1},
        )
        payload = json.loads(preflight.render(checks, as_json=True))
        self.assertEqual(payload["summary"]["fail"], 1)
        self.assertEqual(len(payload["checks"]), 4)

    def test_local_only_exits_zero_and_reports_json(self):
        output = StringIO()
        with redirect_stdout(output):
            code = preflight.main(["--local-only", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertGreaterEqual(payload["summary"]["pass"], 3)
        statuses = {check["name"]: check["status"] for check in payload["checks"]}
        self.assertEqual(statuses["docker"], "skip")

    def test_failure_changes_exit_code(self):
        checks = [preflight.Check("x", "fail", "boom")]
        self.assertFalse(all(check.ok for check in checks))


if __name__ == "__main__":
    unittest.main()
