#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

uv sync --project apps/api

export PYTHONPATH=apps/api
export DATABASE_URL="${DATABASE_URL:-sqlite+pysqlite:///./data/cmaa.db}"
export STORAGE_DIR="${STORAGE_DIR:-./data/uploads}"
export MOCK_LLM="${MOCK_LLM:-true}"
export RAG_ENABLED="${RAG_ENABLED:-true}"
export LOCAL_EMBEDDINGS="${LOCAL_EMBEDDINGS:-true}"
export NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
export NEO4J_USER="${NEO4J_USER:-neo4j}"
export NEO4J_PASSWORD="${NEO4J_PASSWORD:-cmaapassword}"
mkdir -p data/uploads data/embeddings
exec uv run --project apps/api uvicorn app.main:app --app-dir apps/api --reload --port 8000
