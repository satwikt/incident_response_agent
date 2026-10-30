import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

DOCS = Path(__file__).resolve().parents[3] / "docs"
SCHEMA_PATH = DOCS / "native-tools.schema.json"
SPEC_PATH = DOCS / "native-tools-spec.md"

EXPECTED_TOOLS = {
    "query_prometheus",
    "query_loki",
    "query_tempo",
    "query_jaeger",
    "get_trace",
    "get_flag_state",
    "search_application_docs",
}
MUTATING = re.compile(r"(write|set_|toggle|delete|exec|shell|remediate|apply)", re.IGNORECASE)


class NativeToolSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.spec = SPEC_PATH.read_text(encoding="utf-8")
        cls.tools = {tool["name"]: tool for tool in cls.schema["tools"]}

    def test_expected_read_only_tool_set(self):
        self.assertEqual(set(self.tools), EXPECTED_TOOLS)
        for name in self.tools:
            with self.subTest(tool=name):
                self.assertIsNone(MUTATING.search(name))

    def test_parameters_are_closed_and_required_fields_exist(self):
        for name, tool in self.tools.items():
            with self.subTest(tool=name):
                params = tool["parameters"]
                self.assertEqual(params["type"], "object")
                self.assertIs(params["additionalProperties"], False)
                properties = params["properties"]
                self.assertTrue(set(params["required"]).issubset(properties))
                for property_name in properties:
                    self.assertNotIn(
                        property_name.lower(), {"url", "endpoint", "host", "base_url"}
                    )

    def test_row_limits_are_bounded(self):
        for name, tool in self.tools.items():
            limit = tool["parameters"]["properties"].get("limit")
            if limit is None:
                continue
            with self.subTest(tool=name):
                self.assertLessEqual(limit["maximum"], 200)
                self.assertGreaterEqual(limit["minimum"], 1)

    def test_error_codes_and_limits_are_declared(self):
        self.assertEqual(
            set(self.schema["error_codes"]),
            {
                "invalid_argument",
                "backend_unavailable",
                "timeout",
                "not_found",
                "too_large",
                "internal",
            },
        )
        limits = self.schema["limits"]
        self.assertEqual(limits["timeout_seconds"], 5)
        self.assertEqual(limits["max_window_hours"], 24)
        self.assertEqual(limits["max_query_chars"], 2000)
        self.assertLessEqual(limits["max_response_bytes"], 262144)

    def test_envelope_has_success_and_error_shapes(self):
        success = self.schema["envelope"]["success"]
        failure = self.schema["envelope"]["error"]
        self.assertIs(success["ok"], True)
        self.assertEqual(success["data"], [])
        self.assertIsNone(success["error"])
        self.assertIs(failure["ok"], False)
        self.assertIn("code", failure["error"])
        self.assertIn("message", failure["error"])

    def test_spec_documents_every_tool_and_safety_rule(self):
        for name in EXPECTED_TOOLS:
            with self.subTest(tool=name):
                self.assertIn(f"`{name}`", self.spec)
        self.assertIn("never raises", self.spec)
        self.assertIn("read-only", self.spec)
        self.assertIn("docs/native-tools.schema.json", self.spec)
        self.assertIn("No flag writes", self.spec)


if __name__ == "__main__":
    unittest.main()
