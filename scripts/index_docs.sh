#!/usr/bin/env bash
# ==============================================================================
# index_docs.sh - Vectorize documentation into ChromaDB with relative paths
# ==============================================================================
set -euo pipefail

# Resolve script directory and repository root dynamically
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

# Relative paths
DOCS_PATH="otel_demo/docs"
CHROMA_PATH="Copilot/data/chroma_db"
COLLECTION_NAME="astronomy_shop_docs"
INDEX_PATH="Copilot/data/docs_index.json"

echo "====================================================================="
echo " Vectorizing documentation into ChromaDB"
echo " Docs source : ${DOCS_PATH}"
echo " ChromaDB dir: ${CHROMA_PATH}"
echo " Collection  : ${COLLECTION_NAME}"
echo " BM25 index  : ${INDEX_PATH}"
echo "====================================================================="

python3 Copilot/scripts/index_docs.py \
  --docs-path "${DOCS_PATH}" \
  --extra-docs-path "docs/runbooks" \
  --chroma-path "${CHROMA_PATH}" \
  --collection "${COLLECTION_NAME}" \
  --index-path "${INDEX_PATH}"

echo ""
echo "Documentation vectorization complete."
