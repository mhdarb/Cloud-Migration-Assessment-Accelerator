from __future__ import annotations

from pydantic import BaseModel


class RelevanceScore(BaseModel):
    index: int
    score: float


class RerankScores(BaseModel):
    """Structured-output target for `LlmReranker`: one relevance score per candidate
    passage, keyed by its position in the numbered prompt (not the chunk id — the model
    never sees real ids, just `[0]`, `[1]`, ... markers)."""

    scores: list[RelevanceScore]
