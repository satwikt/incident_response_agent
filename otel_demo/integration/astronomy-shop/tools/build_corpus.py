"""Build the Astronomy Shop documentation corpus as chunked JSONL.

Sources are the pinned vendor READMEs, the vendored feature-flag definition,
and the Copilot-owned design/runbook docs. No network access is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

PINNED_COMMIT = "85c2cb253ae3519ef2b3a02984c32614106de5ea"
APP_VERSION = PINNED_COMMIT[:12]
ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
VENDOR = WORKSPACE / "vendor" / "opentelemetry-demo"
OUT_DIR = ROOT / "docs-corpus"

MAX_CHARS = 1800
_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*$")
_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_HTML_LINE = re.compile(r"^\s*(?:\[!\[|<img\b|<div\b|</div>|<p\b|</p>|<!--)", re.IGNORECASE)

LOCAL_DOCS = (
    ("local-architecture", "architecture.md", ("architecture", "design")),
    ("local-requirements", "requirements.md", ("requirements",)),
    ("local-integration-contract", "integration-contract.md", ("contract",)),
    ("local-implementation-plan", "implementation-plan.md", ("plan",)),
    ("local-decisions", "decisions.md", ("decisions",)),
    ("local-test-scenarios", "test-scenarios.md", ("scenarios",)),
    ("local-operations-runbook", "operations-runbook.md", ("runbook",)),
    ("local-code-structure", "CODE-STRUCTURE.md", ("code", "structure")),
    ("local-team-guide", "TEAM-GUIDE.md", ("guide", "howto")),
    ("local-team-handoff", "TEAM-HANDOFF.md", ("handoff", "integration")),
)


@dataclass(frozen=True)
class DocSource:
    doc_id: str
    path: Path
    service: str
    source: str
    source_url: Optional[str]
    tags: tuple[str, ...]


def _github_url(relative: str) -> str:
    return (
        "https://github.com/open-telemetry/opentelemetry-demo/blob/"
        f"{PINNED_COMMIT}/{relative}"
    )


def _service_dirs() -> list[Path]:
    src = VENDOR / "src"
    if not src.is_dir():
        return []
    return sorted(
        path for path in src.iterdir() if path.is_dir() and (path / "README.md").is_file()
    )


def doc_sources() -> list[DocSource]:
    docs: list[DocSource] = []
    root_readme = VENDOR / "README.md"
    if root_readme.is_file():
        docs.append(
            DocSource(
                "upstream-readme",
                root_readme,
                "platform",
                "vendor",
                _github_url("README.md"),
                ("overview", "architecture", "deployment"),
            )
        )
    for directory in _service_dirs():
        relative = f"src/{directory.name}/README.md"
        docs.append(
            DocSource(
                f"component-{directory.name}",
                directory / "README.md",
                directory.name,
                "vendor",
                _github_url(relative),
                ("component", "service"),
            )
        )
    for doc_id, relative, tags in (
        ("telemetry-schema", "telemetry-schema/README.md", ("telemetry", "schema")),
        ("telemetry-docs", "src/telemetry-docs/README.md", ("telemetry", "docs")),
    ):
        path = VENDOR / relative
        if path.is_file():
            docs.append(
                DocSource(doc_id, path, "telemetry", "vendor", _github_url(relative), tags)
            )
    for doc_id, filename, tags in LOCAL_DOCS:
        path = WORKSPACE / "docs" / filename
        if path.is_file():
            docs.append(
                DocSource(
                    doc_id,
                    path,
                    "copilot-integration",
                    "local",
                    None,
                    ("copilot",) + tags,
                )
            )
    return docs


def clean_markdown(text: str) -> str:
    """Drop badge/HTML-only lines that add noise to retrieval chunks."""
    lines = [line for line in text.splitlines() if not _HTML_LINE.match(line)]
    return "\n".join(lines)


def _title(text: str, fallback: str) -> str:
    match = _TITLE.search(text)
    return match.group(1).strip() if match else fallback


def sections(markdown: str) -> list[tuple[str, str]]:
    """Split markdown on H1/H2 headings, keeping the preamble."""
    rows: list[tuple[str, str]] = []
    heading = ""
    body: list[str] = []
    for line in markdown.splitlines():
        match = _HEADING.match(line)
        if match:
            if heading or "\n".join(body).strip():
                rows.append((heading, "\n".join(body).strip()))
            heading = match.group(2).strip()
            body = []
        else:
            body.append(line)
    if heading or "\n".join(body).strip():
        rows.append((heading, "\n".join(body).strip()))
    return [(name, text) for name, text in rows if text]


def split_body(text: str, *, max_chars: int = MAX_CHARS) -> list[str]:
    """Split long sections by paragraph, hard-splitting oversized paragraphs."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n{2,}", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        if not current:
            current = paragraph
        elif len(current) + 2 + len(paragraph) <= max_chars:
            current = f"{current}\n\n{paragraph}"
        else:
            chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def flags_markdown(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    flags = payload.get("flags") or {}
    lines = [
        "Runtime fault toggles exposed by flagd. The operator UI is",
        "`http://localhost:8080/feature`; return every flag to `off` after a test.",
        "",
        "| Flag | Default | Variants | Description |",
        "|---|---|---|---|",
    ]
    for name in sorted(flags):
        spec = flags[name]
        variants = ", ".join(sorted((spec.get("variants") or {}).keys()))
        description = str(spec.get("description", "")).replace("|", "\\|")
        lines.append(
            f"| `{name}` | `{spec.get('defaultVariant')}` | {variants} | {description} |"
        )
    return "\n".join(lines) + "\n"


def build_payload(*, max_chars: int = MAX_CHARS) -> tuple[str, str, dict]:
    """Build corpus JSONL, manifest JSON, and the manifest object in memory."""
    records: list[dict] = []
    manifest_docs: list[dict] = []

    for doc in doc_sources():
        text = clean_markdown(doc.path.read_text(encoding="utf-8", errors="replace"))
        title = _title(text, doc.doc_id)
        ordinal = 0
        for heading, body in sections(text):
            for piece in split_body(body, max_chars=max_chars):
                digest = hashlib.sha256(piece.encode("utf-8")).hexdigest()
                records.append(
                    {
                        "id": f"{doc.doc_id}:{ordinal:03d}:{digest[:8]}",
                        "doc_id": doc.doc_id,
                        "title": f"{title} — {heading}" if heading else title,
                        "heading": heading,
                        "text": piece,
                        "service": doc.service,
                        "source": doc.source,
                        "source_url": doc.source_url,
                        "source_path": doc.path.relative_to(WORKSPACE).as_posix(),
                        "app_version": APP_VERSION,
                        "tags": list(doc.tags),
                        "sha256": digest,
                    }
                )
                ordinal += 1
        manifest_docs.append(
            {
                "doc_id": doc.doc_id,
                "title": title,
                "service": doc.service,
                "source": doc.source,
                "source_url": doc.source_url,
                "source_path": doc.path.relative_to(WORKSPACE).as_posix(),
                "tags": list(doc.tags),
                "chunks": ordinal,
            }
        )

    flag_text = flags_markdown(VENDOR / "src" / "flagd" / "demo.flagd.json")
    if flag_text:
        ordinal = 0
        for heading, body in sections(flag_text):
            for piece in split_body(body, max_chars=max_chars):
                digest = hashlib.sha256(piece.encode("utf-8")).hexdigest()
                records.append(
                    {
                        "id": f"feature-flags:{ordinal:03d}:{digest[:8]}",
                        "doc_id": "feature-flags",
                        "title": f"Astronomy Shop feature flags — {heading}"
                        if heading
                        else "Astronomy Shop feature flags",
                        "heading": heading,
                        "text": piece,
                        "service": "flagd",
                        "source": "generated",
                        "source_url": _github_url("src/flagd/demo.flagd.json"),
                        "source_path": "vendor/opentelemetry-demo/src/flagd/demo.flagd.json",
                        "app_version": APP_VERSION,
                        "tags": ["feature-flags", "fault-injection", "scenarios"],
                        "sha256": digest,
                    }
                )
                ordinal += 1
        manifest_docs.append(
            {
                "doc_id": "feature-flags",
                "title": "Astronomy Shop feature flags",
                "service": "flagd",
                "source": "generated",
                "source_url": _github_url("src/flagd/demo.flagd.json"),
                "source_path": "vendor/opentelemetry-demo/src/flagd/demo.flagd.json",
                "tags": ["feature-flags", "fault-injection", "scenarios"],
                "chunks": ordinal,
            }
        )

    if not records:
        raise RuntimeError("no documentation sources found; is the vendor checkout present?")
    corpus = "".join(json.dumps(row, sort_keys=True) + "\n" for row in records)
    manifest = {
        "app_version": APP_VERSION,
        "vendor_commit": PINNED_COMMIT,
        "vendor_path": "vendor/opentelemetry-demo",
        "generated_by": "integration/astronomy-shop/tools/build_corpus.py",
        "totals": {"documents": len(manifest_docs), "chunks": len(records)},
        "documents": manifest_docs,
    }
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    return corpus, manifest_text, manifest


def write_corpus(out_dir: Path = OUT_DIR, *, max_chars: int = MAX_CHARS) -> dict:
    corpus, manifest_text, manifest = build_payload(max_chars=max_chars)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "corpus.jsonl").write_text(corpus, encoding="utf-8")
    (out_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")
    return manifest


def search(records: Iterable[dict], query: str, *, limit: int = 5) -> list[dict]:
    """Tiny deterministic retrieval helper used to validate the corpus."""
    terms = [term for term in re.findall(r"[a-z0-9]+", query.lower()) if len(term) > 2]
    scored: list[tuple[int, dict]] = []
    for record in records:
        haystack = f"{record.get('title', '')} {record.get('text', '')}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [record for _, record in scored[:limit]]


def load_corpus(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Astronomy Shop docs corpus.")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed corpus is stale instead of writing it",
    )
    args = parser.parse_args(argv)

    if args.check:
        corpus, manifest_text, _ = build_payload(max_chars=args.max_chars)
        current = args.out / "corpus.jsonl"
        current_manifest = args.out / "manifest.json"
        stale = (
            not current.is_file()
            or not current_manifest.is_file()
            or current.read_text(encoding="utf-8") != corpus
            or current_manifest.read_text(encoding="utf-8") != manifest_text
        )
        if stale:
            print("corpus is stale; rerun without --check", file=sys.stderr)
            return 1
        print("corpus is up to date")
        return 0

    manifest = write_corpus(args.out, max_chars=args.max_chars)
    totals = manifest["totals"]
    print(
        f"wrote {totals['chunks']} chunks from {totals['documents']} documents "
        f"to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
