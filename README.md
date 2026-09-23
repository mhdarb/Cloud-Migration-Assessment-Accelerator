# Cloud Migration Assessment Accelerator

AI-powered **Migration Discovery & Readiness** accelerator that turns fragmented enterprise documents into standardized, source-linked, migration-ready outputs.

## Documentation


| Doc                                                    | Purpose                                                  |
| ------------------------------------------------------ | -------------------------------------------------------- |
| **[docs/SETUP.md](docs/SETUP.md)**                     | Install & run — **uv-first**                             |
| **[docs/README.md](docs/README.md)**                   | Docs index + how to read the code                        |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**       | Request path, pipeline, RAG, LLM vs rules, data model |
| **[docs/DYNAMIC_ARCHITECTURE.md](docs/DYNAMIC_ARCHITECTURE.md)** | Model-backed extraction/planning/classification/questions/guardrails, deep dive |
| **[docs/AI_LANDING_ZONE.md](docs/AI_LANDING_ZONE.md)** | Azure AI Landing Zone spoke alignment (`APP_PROFILE=lz`) |
| **[infra/README.md](infra/README.md)**                 | Bicep spoke skeleton                                     |
| **[docs/DEMO.md](docs/DEMO.md)**                       | Stakeholder UI walkthrough with Contoso samples          |
| [sample-data/README.md](sample-data/README.md)         | Sample file inventory                                    |


## What it does

1. **Ingest** PDF / DOCX / XLSX / CSV / JSON / **ZIP code snapshots** (architecture, CMDB, vendor and operational documents, questionnaires, requirements/NFR, manifests)
2. **Embed + index** chunks for retrieval (local FAISS or Azure AI Search)
3. **RAG extract** — query-driven retrieve → extract with mandatory citations (incl. NFR/SLA queries)
4. **Manifest extract** — `package.json` / `pom.xml` / Dockerfile / compose / appsettings → runtime & infra deps
5. **Reconcile** conflicts using document precedence + confidence scores
6. **Build the dependency graph** live from the reconciled data, ranked by PageRank centrality, plus a deterministic (or optional LLM) pass proposing additional relationships for review
7. **Report** readiness summary, gaps, NFRs/runtime, assumptions, and exportable JSON
8. **Human review** — Accept / Override / Reject, then Complete Review → Follow-up with learning log
9. **Infrastructure sizing** — auditable Azure VM/disk recommendations, estimates, alternatives, and assumptions
10. **Blast radius** — inspect migration impact neighborhood on the graph

## Stack


| Layer           | Tech                                                                                                 |
| --------------- | ---------------------------------------------------------------------------------------------------- |
| API             | Python FastAPI                                                                                       |
| UI              | Next.js (React) + React Flow                                                                         |
| Operational DB  | **SQLite (default)** or Postgres                                                                     |
| Knowledge graph | Built live from Postgres (`networkx` for centrality/PageRank) — no separate graph database             |
| AI              | Heuristics by default; optional **Azure OpenAI** for extract + prose |
| RAG             | Local sentence-transformers + **FAISS**, or Azure embeddings + **Azure AI Search**                   |


## Quick start (no Docker)

Full detail (including Windows): **[docs/SETUP.md](docs/SETUP.md)**.

```bash
# API (uv)
uv sync --project apps/api
uv run --project apps/api python scripts/generate_sample_data.py

# Terminal 1 — API (SQLite + mock LLM + local RAG)
./apps/api/run.sh

# Terminal 2 — UI
cd apps/web && cp .env.local.example .env.local && npm install && npm run dev
```

- UI: [http://localhost:3000](http://localhost:3000)  
- API health: [http://localhost:8000/health](http://localhost:8000/health) — shows `"embeddings": "sentence-transformers"` and `"vector_index": "faiss"` locally.  
- Demo script: [docs/DEMO.md](docs/DEMO.md)

### Unit tests (API)

```bash
uv run --project apps/api --directory apps/api pytest -q -m "not eval"
uv run --project apps/api --directory apps/api pytest -q -m eval    # golden-set pipeline regression (sample-data)
```

Covers citation grounding, precedence reconciliation, ZIP safety, manifests, hybrid (BM25+vector, RRF-fused) retrieve, structure-aware chunking, LLM completer retry/backoff, the agent budget breaker, review/follow-up workflow, assessment questions, sizing rules, and mocked chat completer (no live keys). CI (`.github/workflows/ci.yml`) runs `ruff`, an import smoke test, both pytest jobs above, and `mypy` (informational).

### Smoke tests

Smokes upload sample files and wait for the pipeline (it starts on upload).

```bash
uv run --project apps/api python scripts/smoke_rag_graph.py
uv run --project apps/api python scripts/smoke_code_nfr.py
uv run --project apps/api python scripts/smoke_core_capabilities.py
```

## Optional Docker (Postgres)

Only if Docker is available on the machine:

```bash
docker compose up -d postgres
```

## Code snapshot + requirements notes

- Upload a **ZIP** of an app repo snapshot (max 50MB). The pipeline safely extracts manifests/config only (`node_modules` / `.git` skipped).
- Supported signals: `package.json`, `pom.xml`, `requirements.txt`, `pyproject.toml`, `Dockerfile`*, `docker-compose*.yml`, `*.csproj`, `appsettings*.json`, `.env.example`, selected YAML.
- Secrets are not stored — connection string **keys** are recorded as present; values redacted.
- Requirements/BRD/NFR DOCX/PDF are classified as `requirements` and mined for SLA/RTO/RPO/compliance via RAG (+ offline heuristics).

## RAG matrix

The effective resolved mode is always inspectable at runtime via `GET /health/config`.

| Mode             | When                                                  | Behavior                                                                            |
| ---------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Local vector RAG | `RAG_ENABLED=true`, `LOCAL_EMBEDDINGS=true` (default) | LangGraph harness: retrieve-all → heuristic extract → cite validate                 |
| Chat LLM RAG     | `MOCK_LLM=false` + Azure OpenAI, or `LLM_PROVIDER=openai_compatible` + `OPENAI_BASE_URL` | LangGraph planner-seeded per-query loop: hybrid (BM25+vector, RRF-fused) retrieve → structured-output extract → cite validate → retry if ungrounded; gap-driven extra retrieve; bounded by `AGENT_MAX_LLM_CALLS`/`AGENT_WALL_CLOCK_SECONDS` |
| Azure Search     | `LOCAL_EMBEDDINGS=false` + `AZURE_SEARCH_`*           | Azure AI Search only (no local FAISS)                       |
| RAG off          | `RAG_ENABLED=false`                                   | Full-corpus heuristic scan (legacy demo path)                                       |

Chunking is structure-aware and routed by document type (row-group for CMDB/inventory, Q/A-pair for questionnaires, bounded token windows per manifest file for code snapshots, table-aware prose splitting for architecture/requirements docs) — see `app/services/chunkers.py`. Tables (whole-sheet or embedded in a DOCX, in original document order) get a shape guard against ragged rows/hidden headers, plus a computed summary chunk (sum/avg/min/max) for numeric columns so "what's the total X" queries don't depend on the LLM re-summing row-group chunks itself. `LLM_PROVIDER=openai_compatible` + `OPENAI_BASE_URL` routes chat completions through any OpenAI-API-compatible endpoint (self-hosted vLLM/Ollama, or a hosted gateway), independent of the embeddings provider.

`RERANKER` (`off` default, `cross_encoder`, `llm`) optionally re-scores the RRF-fused candidate pool before it's truncated to `top_k` — RRF optimizes recall across BM25/vector, a reranker then optimizes precision. `cross_encoder` is local/deterministic (no API key); `llm` asks the configured chat completer and falls back to `cross_encoder` without one. Off by default so the offline local profile never needs to download a new model at startup; see `app/services/rerankers.py`.

## Dynamic architecture (models decide *what documents say*; rules decide *what we do about it*)

Cognitive decisions (extraction, retrieval planning, document classification, supplementary questions) are model-backed with deterministic fallbacks; governance decisions (SKU selection, pricing, precedence, citation grounding) stay rule-based and unchanged. Every dynamic port is a `Protocol` in `app/services/ports.py` with an LLM implementation and an offline fallback, wired in `app/services/providers.py`:

| Capability | Setting | Default | Implementations |
| --- | --- | --- | --- |
| Extraction strategy | `EXTRACTION_STRATEGY` | `llm` (heuristic fallback) | `llm` \| `heuristic` \| `ensemble` — see `app/services/extraction_strategies.py` |
| RAG query planning | `RAG_PLANNER` | `heuristic` | `heuristic` (doc-type-gated skill filter) \| `off` (run all 11 skills, old behavior) — see `app/services/skills.py` |
| Doc classification | `DOC_CLASSIFIER` | `llm` (keyword fallback) | `llm` \| `keyword` — see `app/services/classify.py`; low-confidence classifications (`DOC_CLASSIFY_REVIEW_THRESHOLD`, default 0.6) surface as a report gap instead of silently mislabeling |
| Supplementary questions | `QUESTION_PLANNER_ENABLED` | `true` | Heuristic (signal-templated) or LLM-generated, persisted as `origin=dynamic` alongside the fixed base question set — see `app/services/question_planning.py` |
| Injection detection | `GUARDRAILS_SEMANTIC_CHECK_ENABLED` | `false` | Regex always runs; an optional LLM classifier layers on top via `CompositeInjectionDetector` — see `app/services/guardrails.py` |

Per-skill structured-output schemas (`app/schemas/extraction_targets.py`) constrain the model's output shape for sizing/NFR/dependency skills, then adapt back into the same `ExtractedClaim`/`ExtractedDependency` shapes `citations.py`/`reconciliation.py`/`sizing.py` have always consumed — those files are unchanged by any of this.


## Azure configuration (optional)

```
MOCK_LLM=false
LOCAL_EMBEDDINGS=false
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small

AZURE_SEARCH_ENDPOINT=https://<service>.search.windows.net
AZURE_SEARCH_API_KEY=...
AZURE_SEARCH_INDEX=cmaa-chunks
```

Index should include `content` + `content_vector` fields for vector search.

## API overview


| Method   | Path                                                | Description                                                    |
| -------- | --------------------------------------------------- | -------------------------------------------------------------- |
| GET      | `/health`                                           | Status + `rag` / `embeddings` / `vector_index` flags           |
| GET      | `/health/config`                                    | Every dynamic-architecture/retrieval/guardrail flag's resolved value (never secrets) |
| GET/POST | `/assessments`                                      | List / create (multipart: `name`, `files`)                     |
| GET      | `/assessments/{id}`                                 | Detail + pipeline metrics                                      |
| PATCH    | `/assessments/{id}`                                 | Rename (`{ "name" }`)                                          |
| DELETE   | `/assessments/{id}`                                 | Delete assessment, uploads, and FAISS index (409 if running)   |
| POST     | `/assessments/{id}/documents`                       | Add source files (starts pipeline)                             |
| DELETE   | `/assessments/{id}/documents/{document_id}`         | Remove a source file (re-runs if others remain; 409 if running) |
| POST     | `/assessments/{id}/run`                             | Re-run pipeline                                                |
| POST     | `/assessments/{id}/complete-review`                 | Advance after review queue is clear                            |
| POST     | `/assessments/{id}/follow-up`                       | Append a follow-up note                                        |
| GET      | `/assessments/{id}/claims`                          | Extracted claims                                               |
| POST     | `/assessments/{id}/claims/{claim_id}/review`        | accept / override / reject                                     |
| GET      | `/assessments/{id}/entities`                        | Reconciled inventory                                           |
| GET      | `/assessments/{id}/assessment-questions`            | Grounded standard migration-template answers                   |
| GET      | `/assessments/{id}/recommendations`                 | Azure VM/disk sizing and cost estimates                        |
| GET      | `/assessments/{id}/graph`                           | Dependency graph, built live from Postgres with PageRank centrality |
| GET      | `/assessments/{id}/graph/blast-radius?node=&depth=` | Neighborhood expansion                                         |
| GET      | `/assessments/{id}/report`                          | Assessment output JSON                                         |
| GET      | `/assessments/{id}/conflicts`                       | Conflicting facts                                              |


Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

## Repo layout

```
apps/api/          FastAPI + RAG + dependency graph
apps/web/          Next.js UI
docs/              Index, SETUP, ARCHITECTURE, DEMO, AI LZ
sample-data/       Contoso synthetic estate
scripts/           sample data + smoke tests
docker-compose.yml Optional Postgres
```

