# Setup guide (uv-first, no Docker required)

This runs on a locked-down laptop with **uv + Node**. Docker, Neo4j, Postgres, and Azure are optional.

Pipeline internals (HTTP → extract graphs → what the LLM may change): **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Prerequisites


| Tool                     | Version         | Required?                                       |
| ------------------------ | --------------- | ----------------------------------------------- |
| [uv](https://docs.astral.sh/uv/) | latest          | Yes (API env + deps)                            |
| Python                   | 3.11+ (uv can install it) | Yes (API)                             |
| Node.js                  | 18+             | Yes (UI)                                        |
| npm                      | comes with Node | Yes                                             |
| Docker                   | —               | No                                              |
| Azure OpenAI / AI Search | —               | No (demo uses mock LLM + local embeddings)      |
| Neo4j                    | —               | No (graph falls back to SQLite/Postgres tables) |

Install uv (macOS / Linux): `curl -LsSf https://astral.sh/uv/install.sh | sh`  
Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`


## Recommended path: SQLite + mock LLM (no Docker)

### 1. Clone / open the repo

```bash
cd "cloud migration assessment accelerator"
```

### 2. Sync API dependencies with uv

From the repo root (creates `apps/api/.venv` via uv):

```bash
uv sync --project apps/api
```

### 3. Generate sample documents

```bash
uv run --project apps/api python scripts/generate_sample_data.py
```

Creates Contoso files under `sample-data/` (architecture, inventory, questionnaire, NFR pack, `sample-app.zip`).

### 4. Configure environment

```bash
cp .env.example .env
```

ensure `.env` contains (or keep `run.sh` defaults):

```env
APP_PROFILE=local
DATABASE_URL=sqlite+pysqlite:///./data/cmaa.db
STORAGE_DIR=./data/uploads
MOCK_LLM=true
RAG_ENABLED=true
LOCAL_EMBEDDINGS=true
LOCAL_EMBEDDING_MODEL=all-MiniLM-L6-v2
CONFIDENCE_REVIEW_THRESHOLD=0.7
API_CORS_ORIGINS=http://localhost:3000
NEXT_PUBLIC_API_URL=http://localhost:8000
ENFORCE_REVIEW=true
SIZING_REGION=eastus
PRICING_CURRENCY=USD
AZURE_PRICING_LIVE=false
```

`MOCK_LLM=true` means extraction uses heuristics (no chat LLM required). Local RAG is **sentence-transformers + FAISS** (the MiniLM model downloads on first run). Sizing SKUs use the local catalog.

Set `MOCK_LLM=false` and configure **Azure OpenAI** to enable LLM extraction, grounded question answers, conflict notes, and the readiness summary (citation checks and SKU pick stay rule-based). If Azure OpenAI is not configured, the app stays on heuristics even when `MOCK_LLM=false`. Optional SKU explanation uses Azure OpenAI when configured, otherwise a rule-based template. Set `AZURE_PRICING_LIVE=true` only to refresh VM list prices from Azure Retail Prices.

You can leave `NEO4J_`* set; if Neo4j is not running the API still works (`neo4j: false` in `/health`).

### 5. Start the API

**macOS / Linux**

```bash
./apps/api/run.sh
```

Or manually from repo root:

```bash
uv sync --project apps/api
mkdir -p data/uploads data/embeddings
PYTHONPATH=apps/api uv run --project apps/api uvicorn app.main:app --app-dir apps/api --reload --port 8000
```

**Windows (PowerShell)**

```powershell
uv sync --project apps/api
$env:PYTHONPATH = "apps\api"
$env:DATABASE_URL = "sqlite+pysqlite:///./data/cmaa.db"
$env:STORAGE_DIR = "./data/uploads"
$env:MOCK_LLM = "true"
$env:RAG_ENABLED = "true"
$env:LOCAL_EMBEDDINGS = "true"
New-Item -ItemType Directory -Force -Path data\uploads, data\embeddings | Out-Null
uv run --project apps/api uvicorn app.main:app --app-dir apps/api --reload --port 8000
```

Verify: [http://localhost:8000/health](http://localhost:8000/health)

Expected for local uv:

```json
{
  "status": "ok",
  "mock_llm": true,
  "rag": true,
  "embeddings": "sentence-transformers",
  "vector_index": "faiss",
  "neo4j": false,
  "database": "sqlite+pysqlite"
}
```

`GET /health/config` echoes every dynamic-architecture/retrieval/guardrail flag's resolved value (extraction strategy, RAG planner, doc classifier, question planner, PII redaction mode, etc.) — useful when a setting doesn't seem to be taking effect. Never returns secrets.

Interactive API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

### 6. Start the web UI

New terminal:

```bash
cd apps/web
cp .env.local.example .env.local
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### 7. Run a demo assessment

1. Create assessment name (e.g. Contoso Wave-1).
2. Upload from `sample-data/`:
  - `architecture-overview.docx`
  - `cmdb-inventory.xlsx`
  - `assessment-questionnaire.docx`
  - `requirements-nfr.docx`
  - `sample-app.zip`
3. Wait until status is **completed** (processing starts on upload).
4. Explore **Questions**, **Sizing**, **Findings**, **Graph** (blast radius), **Report**, **Review**.

Blast radius will show `Source: postgres` when Neo4j is down — that is expected.

### 8. Unit tests

```bash
uv run --project apps/api --directory apps/api pytest -q -m "not eval"
uv run --project apps/api --directory apps/api pytest -q -m eval    # golden-set pipeline regression (sample-data)
```

Lint/type-check (also run in CI, `.github/workflows/ci.yml`):

```bash
uv run --project apps/api ruff check .
uv run --project apps/api mypy app   # informational — not a CI gate yet
```

### 9. Smoke tests (optional)

```bash
uv run --project apps/api python scripts/smoke_rag_neo4j.py
uv run --project apps/api python scripts/smoke_code_nfr.py
uv run --project apps/api python scripts/smoke_core_capabilities.py
```

Smoke scripts tolerate `neo4j: false`. `smoke_core_capabilities.py` asserts grounded questions plus Azure recommendations.

---

## Optional: Neo4j / Postgres (if Docker is present)

```bash
docker compose up -d neo4j
# optional operational DB:
docker compose up -d postgres
```

Then in `.env`:

```env
# Postgres instead of SQLite
DATABASE_URL=postgresql+psycopg://cmaa:cmaa@localhost:5432/cmaa

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=cmaapassword
```

Neo4j Browser: [http://localhost:7474](http://localhost:7474)  
After a successful pipeline run, `/health` shows `"neo4j": true` and assessment metrics include `neo4j_synced: true`.

---

## Optional: Azure OpenAI or AI Search

Use this when you want generative extraction, question prose, conflict notes, and a readiness summary instead of heuristics/templates.

See root [README.md](../README.md#azure-configuration-optional). Azure OpenAI (and typically AI Search for embeddings):

```env
MOCK_LLM=false
LOCAL_EMBEDDINGS=false
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
```

---

## Troubleshooting


| Symptom                                | Likely cause                     | Fix                                                                                  |
| -------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------------ |
| `ModuleNotFoundError: app`             | Wrong cwd / PYTHONPATH           | Run uvicorn from **repo root** with `PYTHONPATH=apps/api` or use `./apps/api/run.sh` |
| Port 8000 in use                       | Prior API still running          | Stop the other process or use `--port 8001` and update `NEXT_PUBLIC_API_URL`         |
| UI loads but no assessments            | API down or CORS                 | Check `/health`; ensure `API_CORS_ORIGINS` includes `http://localhost:3000`          |
| `neo4j: false` / `neo4j_synced: false` | No Neo4j process                 | Normal without Docker; graph still works via SQLite fallback                         |
| Empty findings after upload            | Pipeline still running or failed | Poll assessment status; check `error_message` on the assessment                      |
| `409` pipeline already running         | Double run                       | Wait until status is completed/failed, then re-run                                   |
| Sample files missing                   | Not generated                    | `uv run --project apps/api python scripts/generate_sample_data.py`                   |
| ZIP rejected                           | Size / type                      | Max 50MB; use `.zip` with manifests (not only binaries)                              |
| `uv: command not found`                | uv not installed                 | https://docs.astral.sh/uv/getting-started/installation/                              |
| Corporate proxy / SSL                  | Enterprise network               | Set proxy env vars or a PyPI index URL per IT policy                                 |


---

## Related docs

- [README.md](README.md) — docs index and how to read the code
- [ARCHITECTURE.md](ARCHITECTURE.md) — pipeline, data model, RAG, questions, sizing
- [DYNAMIC_ARCHITECTURE.md](DYNAMIC_ARCHITECTURE.md) — extraction strategy, RAG planner, doc classifier, question planner, guardrail settings
- [DEMO.md](DEMO.md) — UI walkthrough for stakeholders
- [../sample-data/README.md](../sample-data/README.md) — Contoso sample pack

