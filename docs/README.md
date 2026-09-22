# Docs index

This folder is the map of the Cloud Migration Assessment Accelerator. The product ingests documents and app ZIP snapshots, produces source-linked assessment answers, then sizes Azure VMs/disks with reviewable assumptions.

**No LLM API key is required for the default local demo** (`MOCK_LLM=true`, heuristic extract + FAISS). Optional chat: **Azure OpenAI** when `MOCK_LLM=false`.

| Document | Audience | Use it for |
|----------|----------|------------|
| **[SETUP.md](SETUP.md)** | Developers | Install, run, env flags, troubleshooting |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | Engineers | Request path, pipeline, RAG graphs, ChatCompleter vs rules, data model, UI map |
| **[DYNAMIC_ARCHITECTURE.md](DYNAMIC_ARCHITECTURE.md)** | Engineers | Model-backed extraction/planning/classification/questions/guardrails, as-built |
| **[DEMO.md](DEMO.md)** | Presenters | Contoso UI walkthrough |
| **[AI_LANDING_ZONE.md](AI_LANDING_ZONE.md)** | Platform / Azure | Spoke deploy, `APP_PROFILE=lz` |
| **[../infra/README.md](../infra/README.md)** | Infra | Bicep spoke skeleton |
| **[../sample-data/README.md](../sample-data/README.md)** | Demo data | Contoso file inventory |
| **[../README.md](../README.md)** | Everyone | Quick start, API table, tests |

Start here:

- Run the app → **[SETUP.md](SETUP.md)**
- Understand the code → **[ARCHITECTURE.md](ARCHITECTURE.md)** (start here, then *How to read the code*)
- Understand the model-backed layer → **[DYNAMIC_ARCHITECTURE.md](DYNAMIC_ARCHITECTURE.md)**
- Show stakeholders → **[DEMO.md](DEMO.md)**

## How to read the code (15 minutes)

1. Composition root: [`providers.py`](../apps/api/app/services/providers.py) (`build_pipeline_services`)
2. Ports + chat: [`ports.py`](../apps/api/app/services/ports.py), [`llm_clients.py`](../apps/api/app/services/llm_clients.py) (`ChatCompleter`)
3. Orchestration: [`pipeline.py`](../apps/api/app/services/pipeline.py) (`AssessmentPipeline` + `PipelineServices`), [`pipeline_lock.py`](../apps/api/app/services/pipeline_lock.py)
4. Ingest: [`ingest.py`](../apps/api/app/services/ingest.py), [`parsers.py`](../apps/api/app/services/parsers.py), [`chunkers.py`](../apps/api/app/services/chunkers.py), [`classify.py`](../apps/api/app/services/classify.py), [`code_manifests.py`](../apps/api/app/services/code_manifests.py)
5. RAG: [`embeddings.py`](../apps/api/app/services/embeddings.py), [`vector_indexes.py`](../apps/api/app/services/vector_indexes.py), [`search.py`](../apps/api/app/services/search.py) (hybrid BM25+vector, RRF)
6. Extract: [`agent_extract.py`](../apps/api/app/services/agent_extract.py) (two LangGraph paths, planner-seeded), [`skills.py`](../apps/api/app/services/skills.py) + heuristic or [`llm_extractors.py`](../apps/api/app/services/llm_extractors.py) (structured per-skill outputs)
7. Reconcile / outputs: [`reconciliation.py`](../apps/api/app/services/reconciliation.py), [`assessment_questions.py`](../apps/api/app/services/assessment_questions.py) + [`question_planning.py`](../apps/api/app/services/question_planning.py), [`sizing.py`](../apps/api/app/services/sizing.py), [`report.py`](../apps/api/app/services/report.py), [`evidence.py`](../apps/api/app/services/evidence.py)
8. HTTP: [`assessment_service.py`](../apps/api/app/services/assessment_service.py), [`routers/assessments.py`](../apps/api/app/routers/assessments.py)
9. UI: [`apps/web/src/app/assessments/[id]/page.tsx`](../apps/web/src/app/assessments/[id]/page.tsx)
