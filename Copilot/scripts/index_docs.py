#!/usr/bin/env python3
"""Standalone CLI documentation indexer for RAG with ChromaDB vectorization.

Scans Markdown documents and JSONL doc corpora, chunks text by headings and paragraphs,
and vectors them into ChromaDB (with automatic BM25 JSON fallback).

Usage:
    python3 Copilot/scripts/index_docs.py --docs-path otel_demo/docs --chroma-path Copilot/data/chroma_db
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

_WORD_RE = re.compile(r"[a-zA-Z0-9_\-\.]{2,}")
_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but",
    "by", "can", "did", "do", "does", "doing", "don", "down", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself",
    "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just",
    "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once",
    "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "s", "same", "she",
    "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves",
    "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "with", "you", "your", "yours", "yourself", "yourselves",
}


def tokenize(text: str) -> List[str]:
    """Tokenize and filter stop words."""
    words = _WORD_RE.findall((text or "").lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def chunk_markdown_file(file_path: Path) -> List[Dict[str, Any]]:
    """Chunk a markdown file by headers and paragraphs."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return []

    lines = content.splitlines()
    chunks: List[Dict[str, Any]] = []
    current_title = file_path.stem
    current_heading = "Overview"
    current_lines: List[str] = []

    def flush_chunk():
        text = "\n".join(current_lines).strip()
        if text and len(text) > 40:
            chunks.append({
                "id": f"{file_path.stem}:{len(chunks):03d}",
                "doc_title": current_title,
                "heading": current_heading,
                "service": file_path.stem.replace("component-", "").replace("service-", ""),
                "source_path": str(file_path),
                "text": text,
            })

    for line in lines:
        if line.startswith("# "):
            current_title = line.lstrip("# ").strip()
        elif line.startswith("## ") or line.startswith("### "):
            flush_chunk()
            current_heading = line.lstrip("# ").strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    flush_chunk()
    return chunks


def load_corpus_jsonl(corpus_path: Path) -> List[Dict[str, Any]]:
    """Load chunks from a pre-built JSONL corpus."""
    chunks = []
    if not corpus_path.is_file():
        return chunks
    with corpus_path.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                chunks.append({
                    "id": item.get("id") or f"corpus:{idx:03d}",
                    "doc_title": item.get("title") or item.get("heading") or "Docs",
                    "heading": item.get("heading") or "Section",
                    "service": item.get("service") or "platform",
                    "source_path": item.get("source_path") or str(corpus_path),
                    "text": item.get("text") or "",
                })
            except Exception:
                continue
    return chunks


# ─── ChromaDB Vectorization ──────────────────────────────────────────────────

def index_with_chroma(
    chunks: List[Dict[str, Any]],
    chroma_path: str = "Copilot/data/chroma_db",
    collection_name: str = "astronomy_shop_docs",
) -> Optional[int]:
    """Store and vectorize document chunks into persistent ChromaDB."""
    try:
        import chromadb  # type: ignore[import-untyped]
    except ImportError:
        return None

    Path(chroma_path).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=chroma_path)
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    ids = [c["id"] for c in chunks]
    documents = [c["text"] for c in chunks]
    metadatas = [
        {
            "doc_title": str(c.get("doc_title", "")),
            "heading": str(c.get("heading", "")),
            "service": str(c.get("service", "")),
            "source_path": str(c.get("source_path", "")),
        }
        for c in chunks
    ]

    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        end = i + batch_size
        collection.upsert(
            ids=ids[i:end],
            documents=documents[i:end],
            metadatas=metadatas[i:end],
        )

    return len(chunks)


def query_chroma(
    query: str,
    limit: int = 3,
    service: Optional[str] = None,
    chroma_path: str = "Copilot/data/chroma_db",
    collection_name: str = "astronomy_shop_docs",
) -> Optional[List[Dict[str, Any]]]:
    """Semantic vector search against ChromaDB."""
    try:
        import chromadb  # type: ignore[import-untyped]
    except ImportError:
        return None

    if not Path(chroma_path).exists():
        return None

    try:
        client = chromadb.PersistentClient(path=chroma_path)
        collection = client.get_collection(name=collection_name)
    except Exception:
        return None

    where_filter = None
    if service and service.lower() != "all":
        where_filter = {"service": service.lower()}

    try:
        results = collection.query(
            query_texts=[query],
            n_results=limit,
            where=where_filter,
        )
    except Exception:
        return None

    hits = []
    if results and "documents" in results and results["documents"]:
        docs = results["documents"][0]
        ids = results.get("ids", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0] if "distances" in results else []

        for idx, doc_text in enumerate(docs):
            meta = metas[idx] if idx < len(metas) else {}
            hit_id = ids[idx] if idx < len(ids) else f"hit_{idx}"
            dist = distances[idx] if idx < len(distances) else 0.0
            score = round(max(0.0, 1.0 - dist), 4) if dist is not None else 1.0

            hits.append({
                "id": hit_id,
                "title": meta.get("doc_title") or meta.get("heading") or "Docs",
                "heading": meta.get("heading") or "Section",
                "service": meta.get("service") or "platform",
                "source": meta.get("source_path") or "",
                "text": doc_text[:1500],
                "score": score,
            })

    return hits


# ─── BM25 Fallback Indexing ──────────────────────────────────────────────────

def build_index(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute inverted index and term statistics for BM25 search."""
    doc_freqs: Counter[str] = Counter()
    chunk_meta: List[Dict[str, Any]] = []
    total_tokens = 0

    for idx, c in enumerate(chunks):
        tokens = tokenize(c["text"] + " " + c["doc_title"] + " " + c["heading"])
        unique_tokens = set(tokens)
        doc_freqs.update(unique_tokens)
        total_tokens += len(tokens)
        chunk_meta.append({
            "idx": idx,
            "id": c["id"],
            "title": c["doc_title"],
            "heading": c["heading"],
            "service": c["service"],
            "source": c["source_path"],
            "text": c["text"],
            "token_count": len(tokens),
            "tokens": tokens,
        })

    num_docs = len(chunk_meta)
    avg_len = (total_tokens / num_docs) if num_docs > 0 else 1.0

    return {
        "version": "1.0",
        "num_docs": num_docs,
        "avg_len": avg_len,
        "doc_freqs": dict(doc_freqs),
        "chunks": chunk_meta,
    }


def search_index(
    index_data: Dict[str, Any],
    query: str,
    limit: int = 3,
    service: Optional[str] = None,
    k1: float = 1.5,
    b: float = 0.75,
) -> List[Dict[str, Any]]:
    """Rank chunks against query using BM25 scoring."""
    num_docs = index_data.get("num_docs", 0)
    avg_len = index_data.get("avg_len", 1.0)
    doc_freqs = index_data.get("doc_freqs", {})
    chunks = index_data.get("chunks", [])

    q_tokens = tokenize(query)
    if not q_tokens:
        return []

    scores: List[tuple[float, Dict[str, Any]]] = []
    svc_filter = (service or "").strip().lower()

    for c in chunks:
        if svc_filter and svc_filter != "all" and c.get("service", "").lower() != svc_filter:
            continue

        c_tokens = c.get("tokens", [])
        c_len = c.get("token_count", len(c_tokens))
        c_counts = Counter(c_tokens)

        score = 0.0
        for qt in q_tokens:
            tf = c_counts.get(qt, 0)
            if tf == 0:
                continue
            df = doc_freqs.get(qt, 0)
            idf = math.log(1.0 + (num_docs - df + 0.5) / (df + 0.5))
            numerator = tf * (k1 + 1.0)
            denominator = tf + k1 * (1.0 - b + b * (c_len / avg_len))
            score += idf * (numerator / denominator)

        if score > 0.0:
            scores.append((score, c))

    scores.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, item in scores[:limit]:
        results.append({
            "id": item["id"],
            "title": item["title"],
            "heading": item["heading"],
            "service": item["service"],
            "source": item["source"],
            "text": item["text"][:1500],
            "score": round(score, 4),
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Index documentation for RAG search.")
    parser.add_argument("--docs-path", type=str, default="otel_demo/docs", help="Directory of markdown docs.")
    parser.add_argument("--extra-docs-path", action="append", default=[], help="Additional markdown directory (repeatable), e.g. docs/runbooks.")
    parser.add_argument("--skip-chroma", action="store_true", help="Write only the BM25 JSON index (no embedding model download).")
    parser.add_argument("--merge", action="store_true", help="Keep existing indexed chunks; replace only those from the given directories.")
    parser.add_argument("--corpus-path", type=str, default="", help="Optional JSONL corpus file.")
    parser.add_argument("--chroma-path", type=str, default="Copilot/data/chroma_db", help="Destination path for ChromaDB vector store.")
    parser.add_argument("--collection", type=str, default="astronomy_shop_docs", help="ChromaDB collection name.")
    parser.add_argument("--index-path", type=str, default="Copilot/data/docs_index.json", help="Destination JSON path for BM25 fallback index.")
    args = parser.parse_args()

    chunks: List[Dict[str, Any]] = []

    # 1. Load from JSONL corpus if present
    if args.corpus_path:
        corpus_p = Path(args.corpus_path)
        if corpus_p.is_file():
            loaded = load_corpus_jsonl(corpus_p)
            chunks.extend(loaded)
            print(f"Loaded {len(loaded)} chunks from corpus {corpus_p}")

    # 2. Load markdown files from docs path (skipping internal dev_docs and legacy archives)
    for docs_p in [Path(args.docs_path)] + [Path(p) for p in args.extra_docs_path]:
        if docs_p.exists():
            md_count = 0
            for md_file in docs_p.rglob("*.md"):
                if "dev_docs" in md_file.parts or "legacy" in md_file.parts:
                    continue
                file_chunks = chunk_markdown_file(md_file)
                chunks.extend(file_chunks)
                md_count += len(file_chunks)
            print(f"Loaded {md_count} chunks from {docs_p} (excluding dev_docs/legacy)")

    if not chunks:
        print("No documents found to index.")
        return

    if args.merge and Path(args.index_path).is_file():
        try:
            old = json.loads(Path(args.index_path).read_text(encoding="utf-8")).get("chunks", [])
            roots = [str(Path(p)) for p in [args.docs_path] + list(args.extra_docs_path)]
            # stored chunks use the index field names; map them back to the raw chunk shape build_index consumes
            keep = [{"id": c["id"], "doc_title": c["title"], "heading": c["heading"], "service": c["service"],
                     "source_path": c["source"], "text": c["text"]}
                    for c in old if not any(str(c.get("source", "")).startswith(r) for r in roots)]
            print(f"Merging: keeping {len(keep)} existing chunks, replacing the rest")
            chunks = keep + chunks
        except Exception as exc:  # noqa: BLE001 - a bad old index just means a fresh one
            print(f"Could not merge with the existing index ({exc}); rebuilding it")

    # 3. Vectorize into ChromaDB
    chroma_count = None if args.skip_chroma else index_with_chroma(chunks, chroma_path=args.chroma_path, collection_name=args.collection)
    if chroma_count is not None:
        print(f"Successfully vectorized {chroma_count} chunks into ChromaDB at: {args.chroma_path}")
    else:
        print("ChromaDB library not available in environment; skipping vectorization.")

    # 4. Generate BM25 JSON index (as backup/fallback)
    index_obj = build_index(chunks)
    out_p = Path(args.index_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with out_p.open("w", encoding="utf-8") as f:
        json.dump(index_obj, f, indent=2)

    print(f"Successfully generated BM25 fallback index with {len(chunks)} chunks at: {out_p}")


if __name__ == "__main__":
    main()
