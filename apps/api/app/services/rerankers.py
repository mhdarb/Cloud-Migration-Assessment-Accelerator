"""Reranker implementations (Strategy pattern): `NoOpReranker` is the Null Object default
(pass the RRF-fused ranking through unchanged); `CrossEncoderReranker` is a local,
deterministic, provider-agnostic upgrade (no chat LLM or API key needed); `LlmReranker`
uses the configured chat completer for semantic relevance scoring and falls back to a
cheaper strategy when the completer is disabled, fails, or returns something unusable.

A reranker outage must never break retrieval — every implementation here degrades to
returning the incoming order (already RRF-ranked) rather than raising, mirroring how
`FallbackEmbedder` degrades to the local embedder instead of failing the pipeline.
"""

from __future__ import annotations

import json
import logging

from app.models.entities import Chunk
from app.schemas.reranking import RerankScores
from app.services.compat import call_if_supported
from app.services.ports import ChatCompleter, Reranker

logger = logging.getLogger(__name__)

_cross_encoder_model = None
_cross_encoder_model_name: str | None = None
_cross_encoder_failed = False


class NoOpReranker:
    """Null Object: keep the fused ranking as-is, just truncate to `top_k`."""

    def rerank(self, query: str, chunks: list[Chunk], top_k: int) -> list[Chunk]:
        return chunks[:top_k]


def _load_cross_encoder(model_name: str):
    """Lazy singleton, mirroring `embeddings.sentence_model()`. Caches a load failure
    (e.g. no network to fetch the model) so every query doesn't re-attempt and re-block
    on the same timeout — one warning, then a silent pass-through for the rest of the
    process lifetime."""
    global _cross_encoder_model, _cross_encoder_model_name, _cross_encoder_failed
    if _cross_encoder_model is not None and _cross_encoder_model_name == model_name:
        return _cross_encoder_model
    if _cross_encoder_failed:
        return None
    try:
        from sentence_transformers import CrossEncoder

        logger.info("Loading cross-encoder reranker model %s", model_name)
        _cross_encoder_model = CrossEncoder(model_name)
        _cross_encoder_model_name = model_name
        return _cross_encoder_model
    except Exception:
        logger.exception(
            "Cross-encoder reranker model %s unavailable; falling back to fused ranking",
            model_name,
        )
        _cross_encoder_failed = True
        return None


class CrossEncoderReranker:
    """Local (query, chunk) relevance scoring via a `sentence-transformers` CrossEncoder.
    Deterministic, no API key, works in the fully-offline local profile. Falls back to a
    pass-through if the model can't be loaded or scoring errors."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    def rerank(self, query: str, chunks: list[Chunk], top_k: int) -> list[Chunk]:
        if not chunks:
            return []
        model = _load_cross_encoder(self._model_name)
        if model is None:
            return chunks[:top_k]
        try:
            pairs = [(query, c.text) for c in chunks]
            scores = model.predict(pairs)
        except Exception:
            logger.exception("Cross-encoder scoring failed; falling back to fused ranking")
            return chunks[:top_k]
        ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)
        return [c for c, _ in ranked[:top_k]]


class LlmReranker:
    """One structured-output call scores every candidate's relevance (0-10) to the query.
    The model only ever sees `[0]`, `[1]`, ... position markers, never chunk ids, so a
    malformed/incomplete response can't corrupt anything beyond this call's own ranking.
    Falls back to `fallback` (default `NoOpReranker`) when the completer is disabled, the
    call fails, or the response doesn't score every candidate."""

    def __init__(self, completer: ChatCompleter, fallback: Reranker | None = None) -> None:
        self._completer = completer
        self._fallback = fallback or NoOpReranker()

    def rerank(self, query: str, chunks: list[Chunk], top_k: int) -> list[Chunk]:
        if not chunks:
            return []
        if not self._completer.enabled:
            return self._fallback.rerank(query, chunks, top_k)
        system = (
            "Score how relevant each numbered passage is to the query, from 0 (irrelevant) "
            "to 10 (directly answers it). Score every passage listed, even if irrelevant."
        )
        numbered = "\n\n".join(f"[{i}] {c.text[:600]}" for i, c in enumerate(chunks))
        user = f"Query: {query}\n\nPassages:\n{numbered}"
        content = call_if_supported(
            self._completer.complete,
            system,
            user,
            temperature=0.0,
            json_mode=True,
            response_schema=RerankScores,
        )
        if not content:
            return self._fallback.rerank(query, chunks, top_k)
        try:
            parsed = RerankScores.model_validate(json.loads(content))
            by_index = {s.index: s.score for s in parsed.scores}
            if not all(i in by_index for i in range(len(chunks))):
                raise ValueError("response did not score every candidate passage")
            scored = [(chunks[i], by_index[i]) for i in range(len(chunks))]
        except Exception:
            logger.exception("LLM reranker returned an unusable response; falling back")
            return self._fallback.rerank(query, chunks, top_k)
        ranked = sorted(scored, key=lambda pair: pair[1], reverse=True)
        return [c for c, _ in ranked[:top_k]]
