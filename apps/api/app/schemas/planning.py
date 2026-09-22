from __future__ import annotations

from pydantic import BaseModel

from app.models.entities import DocumentType


class CorpusProfile(BaseModel):
    """Cheap, deterministic summary of what's in an assessment's corpus — no LLM."""

    doc_types: set[DocumentType] = set()
    document_count: int = 0


class PlannedQuery(BaseModel):
    skill_id: str
    query: str


class QueryPlan(BaseModel):
    queries: list[PlannedQuery] = []

    def as_strings(self) -> list[str]:
        return [q.query for q in self.queries]
