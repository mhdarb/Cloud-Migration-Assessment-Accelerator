from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Chunk, Document
from app.schemas.api import ExtractionResult
from app.services.agent_extract import run_extract_agent
from app.services.citations import validate_citations
from app.services.extraction_merge import dedupe_extraction
from app.services.heuristic_extract import heuristic_extract
from app.services.ports import LlmExtractor, Retriever


class AssessmentClaimExtractor:
    def __init__(
        self,
        retriever: Retriever,
        llm: LlmExtractor,
        *,
        rag_enabled: bool,
        use_mock: bool,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._rag_enabled = rag_enabled
        self._use_mock = use_mock

    def extract_assessment(
        self, db: Session, assessment_id: str
    ) -> tuple[ExtractionResult, dict[str, Any]]:
        return extract_from_assessment(
            db,
            assessment_id,
            retriever=self._retriever,
            llm=self._llm,
            rag_enabled=self._rag_enabled,
            use_mock=self._use_mock,
        )


def extract_from_assessment(
    db: Session,
    assessment_id: str,
    *,
    retriever: Retriever,
    llm: LlmExtractor,
    rag_enabled: bool,
    use_mock: bool,
) -> tuple[ExtractionResult, dict[str, Any]]:
    """Run RAG extraction via LangGraph harness (or full-scan heuristic if RAG off)."""
    docs = {
        d.id: d for d in db.query(Document).filter(Document.assessment_id == assessment_id)
    }
    metrics: dict[str, Any] = {
        "rag_enabled": rag_enabled,
        "rag_queries": 0,
        "chunks_retrieved": 0,
        "retrieval_mode": "none",
    }

    if rag_enabled:
        return run_extract_agent(
            db,
            assessment_id,
            docs,
            retriever=retriever,
            llm=llm,
            use_mock=use_mock,
        )

    chunks = (
        db.query(Chunk)
        .filter(Chunk.assessment_id == assessment_id)
        .order_by(Chunk.document_id, Chunk.chunk_index)
        .all()
    )
    metrics["retrieval_mode"] = "full_scan_mock"
    result = heuristic_extract(chunks, docs)
    result = validate_citations(
        result, {c.id for c in chunks}, {c.id: c.text for c in chunks}
    )
    return (
        dedupe_extraction(
            result,
            default_assumption="Extracted via RAG pipeline; validate before migration planning",
        ),
        metrics,
    )
