from __future__ import annotations

import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_api_auth
from app.azure_clients import identity_mode
from app.config import get_settings
from app.database import init_db
from app.observability import configure_observability
from app.routers import assessments
from app.schemas.api import HealthOut
from app.services.embeddings import describe_embedder
from app.services.neo4j_graph import neo4j_available
from app.services.search import describe_vector_index

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
app = FastAPI(
    title="Cloud Migration Assessment Accelerator",
    description=(
        "AI-powered migration discovery & readiness accelerator. "
        "Deploy as an Azure AI Landing Zone spoke workload (APP_PROFILE=lz)."
    ),
    version="0.3.0",
    dependencies=[Depends(require_api_auth)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins + ["http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(assessments.router)


@app.on_event("startup")
def on_startup() -> None:
    configure_observability()
    settings.ensure_dirs()
    try:
        settings.validate_profile()
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise
    init_db()
    logger.info(
        "Started app_profile=%s identity=%s mock_llm=%s embeddings=%s vector_index=%s",
        settings.app_profile,
        identity_mode(),
        settings.use_mock_llm,
        describe_embedder() if settings.embeddings_mode == "local" else settings.embeddings_mode,
        describe_vector_index() if settings.rag_enabled else "none",
    )


@app.get("/health/config")
def health_config() -> dict:
    """Effective resolved flags/modes — the RAG-matrix truth table, debuggable at runtime.
    Never returns secrets/keys, only booleans/modes/derived settings."""
    return {
        "app_profile": settings.app_profile,
        "mock_llm": settings.use_mock_llm,
        "chat_llm_configured": settings.chat_llm_configured,
        "llm_provider": settings.llm_provider or ("azure_openai" if settings.azure_openai_configured else "none"),
        "embeddings_mode": settings.embeddings_mode,
        "vector_index": describe_vector_index() if settings.rag_enabled else "none",
        "rag_enabled": settings.rag_enabled,
        "retrieval_hybrid_enabled": settings.retrieval_hybrid_enabled,
        "reranker_enabled": settings.reranker_enabled,
        "rag_planner": settings.rag_planner,
        "extraction_strategy": settings.extraction_strategy,
        "doc_classifier": settings.doc_classifier,
        "question_planner_enabled": settings.question_planner_enabled,
        "guardrails_enabled": settings.guardrails_enabled,
        "guardrails_semantic_check_enabled": settings.guardrails_semantic_check_enabled,
        "pii_redaction_mode": settings.pii_redaction_mode,
        "content_safety_configured": settings.content_safety_configured,
        "content_safety_fail_open": settings.content_safety_fail_open,
        "pipeline_lock_backend": settings.pipeline_lock_backend,
        "otel_enabled": settings.otel_enabled,
        "agent_max_llm_calls": settings.agent_max_llm_calls,
        "agent_wall_clock_seconds": settings.agent_wall_clock_seconds,
        "chunk_size_tokens": settings.chunk_size_tokens,
        "chunk_overlap_tokens": settings.chunk_overlap_tokens,
    }


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(
        status="ok",
        mock_llm=settings.use_mock_llm,
        azure_openai=settings.azure_openai_configured,
        azure_search=settings.azure_search_configured,
        database=settings.database_url.split("://")[0],
        neo4j=neo4j_available(),
        rag=settings.rag_enabled,
        embeddings=describe_embedder() if settings.embeddings_mode == "local" else settings.embeddings_mode,
        vector_index=describe_vector_index() if settings.rag_enabled else "none",
        guardrails=settings.guardrails_enabled,
        content_safety=settings.content_safety_configured,
        app_profile=settings.app_profile,
        identity_mode=identity_mode(),
    )
