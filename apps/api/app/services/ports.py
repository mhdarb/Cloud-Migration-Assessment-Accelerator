from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.entities import Chunk, DocumentType
from app.schemas.api import ExtractionResult
from app.schemas.chunking import ChunkPayload
from app.schemas.classification import Classification
from app.schemas.guardrail_signals import Signal
from app.schemas.planning import CorpusProfile, QueryPlan
from app.schemas.questions import InventorySummary, PlannedQuestion
from app.services.parsers import ChunkPiece, ParsedPage


@runtime_checkable
class Embedder(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...


@runtime_checkable
class VectorIndex(Protocol):
    """Persist and search embedding vectors for an assessment."""

    name: str

    def upsert(
        self, assessment_id: str, chunks: list[Chunk], vectors: list[list[float]]
    ) -> int: ...

    def search(
        self,
        db: Session,
        assessment_id: str,
        query: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[Chunk]: ...

    def clear(self, assessment_id: str) -> None: ...


@runtime_checkable
class Retriever(Protocol):
    def retrieve(
        self,
        db: Session,
        assessment_id: str,
        query: str,
        top_k: int | None = None,
    ) -> list[Chunk]: ...


@runtime_checkable
class Chunker(Protocol):
    """Split parsed document pages into persistable chunk pieces, per doc type."""

    def chunk(
        self,
        pages: list[ParsedPage],
        *,
        doc_type: DocumentType,
        filename: str = "",
    ) -> list[ChunkPiece]: ...


@runtime_checkable
class ChatCompleter(Protocol):
    """OpenAI-compatible chat completion. Infrastructure implements this; domain does not."""

    enabled: bool
    source: str

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.1,
        json_mode: bool = False,
        response_schema: type[BaseModel] | None = None,
    ) -> str | None: ...


@runtime_checkable
class LlmExtractor(Protocol):
    """Extract structured claims from a retrieval focus query + chunk payload.

    `target_schema`/`system_prompt_suffix` let a caller (the LangGraph harness) request
    a narrow per-skill structured-output shape (see `schemas/extraction_targets.py`);
    implementations that don't support them may ignore the kwargs (see `compat.py`).
    """

    def extract(
        self,
        query: str,
        chunk_payload: list[ChunkPayload],
        *,
        target_schema: type[BaseModel] | None = None,
        system_prompt_suffix: str | None = None,
    ) -> ExtractionResult: ...


@runtime_checkable
class ClaimExtractor(Protocol):
    def extract_assessment(
        self, db: Session, assessment_id: str
    ) -> tuple[ExtractionResult, dict[str, Any]]: ...


@runtime_checkable
class GraphSink(Protocol):
    def available(self) -> bool: ...

    def sync(self, db: Session, assessment_id: str) -> dict[str, Any]: ...


@runtime_checkable
class DocClassifier(Protocol):
    """Classify a document's type from its filename + a text sample."""

    def classify(self, filename: str, text_sample: str) -> Classification: ...


@runtime_checkable
class RagPlanner(Protocol):
    """Decide which skills' queries to run for a given corpus."""

    def plan(self, profile: CorpusProfile) -> QueryPlan: ...


@runtime_checkable
class QuestionPlanner(Protocol):
    """Propose estate-specific supplementary assessment questions."""

    def plan(self, summary: InventorySummary) -> list[PlannedQuestion]: ...


@runtime_checkable
class InjectionDetector(Protocol):
    def scan(self, text: str) -> list[Signal]: ...
