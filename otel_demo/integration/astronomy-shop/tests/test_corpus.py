import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.build_corpus import (
    MAX_CHARS,
    OUT_DIR,
    PINNED_COMMIT,
    VENDOR,
    build_payload,
    clean_markdown,
    load_corpus,
    main,
    search,
)

REQUIRED = {
    "id",
    "doc_id",
    "title",
    "heading",
    "text",
    "service",
    "source",
    "source_url",
    "source_path",
    "app_version",
    "tags",
    "sha256",
}


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus_text, cls.manifest_text, cls.manifest = build_payload()
        cls.records = [json.loads(line) for line in cls.corpus_text.splitlines()]
        cls.doc_ids = {doc["doc_id"] for doc in cls.manifest["documents"]}

    def test_metadata_is_complete_and_unique(self):
        ids = set()
        for record in self.records:
            self.assertTrue(REQUIRED.issubset(record), record["id"])
            self.assertNotIn(record["id"], ids)
            ids.add(record["id"])
            self.assertTrue(record["text"].strip())
            self.assertLessEqual(len(record["text"]), MAX_CHARS)
            self.assertEqual(
                hashlib.sha256(record["text"].encode()).hexdigest(), record["sha256"]
            )
            self.assertEqual(record["app_version"], PINNED_COMMIT[:12])
        self.assertEqual(self.manifest["totals"]["chunks"], len(self.records))

    def test_every_vendored_component_readme_is_included(self):
        for readme in sorted((VENDOR / "src").glob("*/README.md")):
            with self.subTest(component=readme.parent.name):
                self.assertIn(f"component-{readme.parent.name}", self.doc_ids)

    def test_root_and_local_docs_are_included(self):
        self.assertIn("upstream-readme", self.doc_ids)
        for doc_id in (
            "local-architecture",
            "local-requirements",
            "local-integration-contract",
            "local-implementation-plan",
            "local-decisions",
            "local-test-scenarios",
            "local-operations-runbook",
        ):
            with self.subTest(doc=doc_id):
                self.assertIn(doc_id, self.doc_ids)

    def test_feature_flag_catalog_covers_curriculum(self):
        rows = [row for row in self.records if row["doc_id"] == "feature-flags"]
        self.assertTrue(rows)
        text = "\n".join(row["text"] for row in rows)
        for flag in (
            "paymentFailure",
            "paymentUnreachable",
            "intlShippingSlowdown",
            "recommendationCacheFailure",
            "productCatalogLockContention",
            "kafkaQueueProblems",
        ):
            with self.subTest(flag=flag):
                self.assertIn(flag, text)

    def test_retrieval_finds_flag_and_scenario_context(self):
        top = search(self.records, "paymentFailure", limit=3)
        self.assertTrue(top)
        self.assertEqual(top[0]["doc_id"], "feature-flags")
        self.assertIn("paymentFailure", top[0]["text"])

        scenario = search(self.records, "payment unreachable checkout", limit=3)
        self.assertTrue(any("payment" in row["text"].lower() for row in scenario))

    def test_build_is_deterministic(self):
        corpus, manifest, _ = build_payload()
        self.assertEqual(corpus, self.corpus_text)
        self.assertEqual(manifest, self.manifest_text)

    def test_committed_corpus_matches_sources(self):
        self.assertTrue((OUT_DIR / "corpus.jsonl").is_file())
        self.assertTrue((OUT_DIR / "manifest.json").is_file())
        self.assertEqual(
            (OUT_DIR / "corpus.jsonl").read_text(encoding="utf-8"), self.corpus_text
        )
        self.assertEqual(
            (OUT_DIR / "manifest.json").read_text(encoding="utf-8"),
            self.manifest_text,
        )
        self.assertEqual(main(["--check"]), 0)

    def test_committed_corpus_loads(self):
        rows = load_corpus(OUT_DIR / "corpus.jsonl")
        self.assertEqual(len(rows), len(self.records))

    def test_pinned_commit_matches_vendor_head(self):
        if not (VENDOR / ".git").exists():
            self.skipTest("vendor checkout has no git metadata")
        result = subprocess.run(
            ["git", "-C", str(VENDOR), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.skipTest("git unavailable")
        self.assertEqual(result.stdout.strip(), PINNED_COMMIT)

    def test_clean_markdown_drops_badge_lines(self):
        text = "![logo](x)\n<img src=\"x\">\n<div>\nreal content"
        cleaned = clean_markdown(text)
        self.assertNotIn("<img", cleaned)
        self.assertIn("real content", cleaned)


if __name__ == "__main__":
    unittest.main()
