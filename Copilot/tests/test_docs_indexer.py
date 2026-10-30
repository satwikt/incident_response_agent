"""Unit tests for the standalone documentation indexer script."""

import json
from pathlib import Path
import pytest

from scripts.index_docs import (
    build_index,
    chunk_markdown_file,
    load_corpus_jsonl,
    search_index,
    tokenize,
)


def test_tokenize():
    tokens = tokenize("The checkout-service failed with HTTP 500 error!")
    assert "checkout-service" in tokens
    assert "failed" in tokens
    assert "500" in tokens
    assert "error" in tokens
    assert "the" not in tokens  # Stop word removed


def test_chunk_markdown_file(tmp_path):
    md_file = tmp_path / "checkout.md"
    md_file.write_text(
        "# Checkout Service Architecture\n\n"
        "The checkout service coordinates cart retrieval and payment validation.\n\n"
        "## Failure Modes\n\n"
        "If payment fails, checkout logs an error and returns HTTP 500 to the client.\n\n"
        "### Runbook Remediation\n\n"
        "Check payment service connectivity and review downstream latency metrics.\n",
        encoding="utf-8",
    )

    chunks = chunk_markdown_file(md_file)
    assert len(chunks) >= 2
    headings = [c["heading"] for c in chunks]
    assert any("Failure Modes" in h for h in headings)
    assert any("Runbook" in h for h in headings)


def test_build_and_search_index(tmp_path):
    chunks = [
        {
            "id": "c1",
            "doc_title": "Checkout Guide",
            "heading": "Payment Flow",
            "service": "checkout",
            "source_path": "docs/checkout.md",
            "text": "Checkout handles customer orders and contacts the payment service.",
        },
        {
            "id": "c2",
            "doc_title": "Shipping Guide",
            "heading": "Carrier Dispatch",
            "service": "shipping",
            "source_path": "docs/shipping.md",
            "text": "Shipping service packages items and generates tracking numbers.",
        },
    ]

    index_data = build_index(chunks)
    assert index_data["num_docs"] == 2
    assert "checkout" in index_data["doc_freqs"]
    assert "shipping" in index_data["doc_freqs"]

    # Search for checkout
    res = search_index(index_data, query="orders payment", limit=1)
    assert len(res) == 1
    assert res[0]["id"] == "c1"
    assert res[0]["service"] == "checkout"

    # Search with service filter
    res_filtered = search_index(index_data, query="service", limit=5, service="shipping")
    assert len(res_filtered) == 1
    assert res_filtered[0]["service"] == "shipping"


def test_chroma_indexer_and_query(monkeypatch, tmp_path):
    from unittest.mock import MagicMock
    import sys

    # Mock chromadb module
    mock_chroma = MagicMock()
    mock_client = MagicMock()
    mock_collection = MagicMock()

    mock_chroma.PersistentClient.return_value = mock_client
    mock_client.get_or_create_collection.return_value = mock_collection
    mock_client.get_collection.return_value = mock_collection

    mock_collection.query.return_value = {
        "documents": [["Checkout coordinates orders with payment service."]],
        "ids": [["c1"]],
        "metadatas": [[{"doc_title": "Checkout Guide", "heading": "Flow", "service": "checkout", "source_path": "docs/checkout.md"}]],
        "distances": [[0.15]],
    }

    monkeypatch.setitem(sys.modules, "chromadb", mock_chroma)

    from scripts.index_docs import index_with_chroma, query_chroma

    chunks = [
        {
            "id": "c1",
            "doc_title": "Checkout Guide",
            "heading": "Flow",
            "service": "checkout",
            "source_path": "docs/checkout.md",
            "text": "Checkout coordinates orders with payment service.",
        }
    ]

    chroma_dir = str(tmp_path / "test_chroma")
    count = index_with_chroma(chunks, chroma_path=chroma_dir)
    assert count == 1
    assert mock_collection.upsert.called

    # Query
    hits = query_chroma("orders", limit=1, chroma_path=chroma_dir)
    assert hits is not None
    assert len(hits) == 1
    assert hits[0]["service"] == "checkout"
    assert hits[0]["score"] == 0.85
