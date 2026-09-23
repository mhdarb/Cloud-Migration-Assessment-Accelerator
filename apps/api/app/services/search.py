from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import Chunk
from app.services.ports import Embedder, Reranker, Retriever, VectorIndex

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def chunk_search_text(chunk: Chunk) -> str:
    """Text actually handed to the embedder/BM25/keyword scorer for a chunk — its own
    text plus, if `chunkers._annotate_with_context` set one, a short tail snippet of the
    *previous* chunk (`embed_context`). A plain-prose chunk like "the above servers are
    production-critical" has nothing in its own text for a query naming the servers to
    match against; folding in a sliver of the preceding chunk gives it a fighting chance.
    Retrieval-only: `Chunk.text` (used for citation-quote validation and the LLM prompt)
    is never modified — only what's embedded/indexed changes."""
    meta = chunk.metadata_json or {}
    context = meta.get("embed_context")
    if not context:
        return chunk.text
    return f"{context}\n\n{chunk.text}"


def reciprocal_rank_fusion(ranked_id_lists: list[list[str]], k: int = 60) -> list[str]:
    """Fuse several ranked id lists via Reciprocal Rank Fusion, highest score first."""
    scores: dict[str, float] = {}
    for ranked in ranked_id_lists:
        for rank, item_id in enumerate(ranked):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda item_id: scores[item_id], reverse=True)


class ChunkIndexer:
    def __init__(self, embedder: Embedder, indexes: list[VectorIndex]) -> None:
        self._embedder = embedder
        self._indexes = indexes

    def embed_and_index(self, db: Session, assessment_id: str) -> dict:
        chunks = (
            db.query(Chunk)
            .filter(Chunk.assessment_id == assessment_id)
            .order_by(Chunk.document_id, Chunk.chunk_index)
            .all()
        )
        if not chunks:
            return {"indexed": 0, "backend": "none"}

        vectors = self._embedder.embed_texts([chunk_search_text(c) for c in chunks])
        named_counts: dict[str, int] = {}
        for index in self._indexes:
            try:
                count = index.upsert(assessment_id, chunks, vectors)
            except Exception:
                logger.exception("Vector index %s upsert failed", index.name)
                continue
            named_counts[index.name] = count

        for chunk in chunks:
            chunk.search_indexed = True
        db.commit()
        return {
            "indexed": len(chunks),
            "backend": "+".join(named_counts.keys()) or "none",
            "per_index": named_counts,
        }


class KeywordRetriever:
    """Naive substring-count keyword scorer. Used when `rank_bm25` isn't installed."""

    def __init__(self, top_k: int | None = None) -> None:
        self._top_k = top_k
        self._cached_assessment_id: str | None = None
        self._cached_chunks: list[Chunk] | None = None

    def _load_chunks(self, db: Session, assessment_id: str) -> list[Chunk]:
        live_count = (
            db.query(Chunk).filter(Chunk.assessment_id == assessment_id).count()
        )
        if (
            self._cached_assessment_id != assessment_id
            or self._cached_chunks is None
            or len(self._cached_chunks) != live_count
        ):
            self._cached_chunks = (
                db.query(Chunk).filter(Chunk.assessment_id == assessment_id).all()
            )
            self._cached_assessment_id = assessment_id
        return self._cached_chunks

    def retrieve(
        self,
        db: Session,
        assessment_id: str,
        query: str,
        top_k: int | None = None,
    ) -> list[Chunk]:
        k = top_k or self._top_k or get_settings().rag_top_k
        terms = [t.lower() for t in query.split() if len(t) > 2]
        chunks = self._load_chunks(db, assessment_id)
        scored: list[tuple[int, Chunk]] = []
        for chunk in chunks:
            text = chunk_search_text(chunk).lower()
            score = sum(1 for t in terms if t in text)
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:k]]


class BM25Retriever:
    """BM25 keyword retrieval via `rank_bm25`. Preferred over `KeywordRetriever` when available."""

    def __init__(self, top_k: int | None = None) -> None:
        self._top_k = top_k
        self._cached_assessment_id: str | None = None
        self._cached_chunks: list[Chunk] | None = None
        self._bm25 = None

    def _ensure_index(self, db: Session, assessment_id: str) -> None:
        from rank_bm25 import BM25Okapi

        live_count = (
            db.query(Chunk).filter(Chunk.assessment_id == assessment_id).count()
        )
        if (
            self._cached_assessment_id == assessment_id
            and self._cached_chunks is not None
            and len(self._cached_chunks) == live_count
        ):
            return
        self._cached_chunks = (
            db.query(Chunk).filter(Chunk.assessment_id == assessment_id).all()
        )
        self._cached_assessment_id = assessment_id
        tokenized = [_tokenize(chunk_search_text(c)) for c in self._cached_chunks]
        self._bm25 = BM25Okapi(tokenized) if tokenized else None

    def retrieve(
        self,
        db: Session,
        assessment_id: str,
        query: str,
        top_k: int | None = None,
    ) -> list[Chunk]:
        k = top_k or self._top_k or get_settings().rag_top_k
        self._ensure_index(db, assessment_id)
        if not self._cached_chunks or self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._cached_chunks[i] for i in order[:k] if scores[i] > 0]


class MergingRetriever:
    def __init__(
        self,
        embedder: Embedder,
        indexes: list[VectorIndex],
        keyword: KeywordRetriever | BM25Retriever | None = None,
        top_k: int | None = None,
        *,
        fusion_candidates: int | None = None,
    ) -> None:
        self._embedder = embedder
        self._indexes = indexes
        self._keyword = keyword or KeywordRetriever()
        self._top_k = top_k
        self._fusion_candidates = fusion_candidates

    def retrieve(
        self,
        db: Session,
        assessment_id: str,
        query: str,
        top_k: int | None = None,
    ) -> list[Chunk]:
        k = top_k or self._top_k or get_settings().rag_top_k
        settings = get_settings()
        if not settings.retrieval_hybrid_enabled:
            return self._retrieve_waterfall(db, assessment_id, query, k)

        candidates = self._fusion_candidates or max(k * 4, 20)
        query_vector = self._embedder.embed_query(query)
        by_id: dict[str, Chunk] = {}
        ranked_lists: list[list[str]] = []

        for index in self._indexes:
            try:
                hits = index.search(db, assessment_id, query, query_vector, candidates)
            except Exception:
                logger.exception("Vector index %s search failed", index.name)
                continue
            ids = []
            for chunk in hits:
                by_id[chunk.id] = chunk
                ids.append(chunk.id)
            if ids:
                ranked_lists.append(ids)

        try:
            kw_hits = self._keyword.retrieve(db, assessment_id, query, top_k=candidates)
        except Exception:
            logger.exception("Keyword/BM25 retriever failed")
            kw_hits = []
        kw_ids = []
        for chunk in kw_hits:
            by_id[chunk.id] = chunk
            kw_ids.append(chunk.id)
        if kw_ids:
            ranked_lists.append(kw_ids)

        if not ranked_lists:
            return []
        fused = reciprocal_rank_fusion(ranked_lists, k=settings.retrieval_rrf_k)
        return [by_id[cid] for cid in fused[:k]]

    def _retrieve_waterfall(
        self, db: Session, assessment_id: str, query: str, k: int
    ) -> list[Chunk]:
        """Pre-hybrid fallback: vector index(es) in order, keyword only backfills to k."""
        query_vector = self._embedder.embed_query(query)
        ordered: list[Chunk] = []
        seen: set[str] = set()
        for index in self._indexes:
            try:
                hits = index.search(db, assessment_id, query, query_vector, k)
            except Exception:
                logger.exception("Vector index %s search failed", index.name)
                continue
            for chunk in hits:
                if chunk.id in seen:
                    continue
                seen.add(chunk.id)
                ordered.append(chunk)
                if len(ordered) >= k:
                    return ordered
        if len(ordered) >= k:
            return ordered
        for chunk in self._keyword.retrieve(db, assessment_id, query, top_k=k):
            if chunk.id in seen:
                continue
            seen.add(chunk.id)
            ordered.append(chunk)
            if len(ordered) >= k:
                break
        return ordered


class RerankingRetriever:
    """Decorator (not a subclass — `Retriever` is a structural `Protocol`) around any
    `Retriever`: pulls a larger candidate pool from it, then reranks that pool down to
    `top_k`. Keeps reranking fully independent of the fusion algorithm — RRF still
    optimizes for recall across BM25/vector; the `Reranker` then optimizes precision on
    the resulting pool, and either half can be swapped without touching the other."""

    def __init__(
        self,
        inner: Retriever,
        reranker: Reranker,
        *,
        candidate_pool: int | None = None,
    ) -> None:
        self._inner = inner
        self._reranker = reranker
        self._candidate_pool = candidate_pool

    def retrieve(
        self,
        db: Session,
        assessment_id: str,
        query: str,
        top_k: int | None = None,
    ) -> list[Chunk]:
        settings = get_settings()
        k = top_k or settings.rag_top_k
        pool = max(self._candidate_pool or settings.reranker_candidate_pool, k)
        candidates = self._inner.retrieve(db, assessment_id, query, top_k=pool)
        if not candidates:
            return candidates
        try:
            return self._reranker.rerank(query, candidates, k)
        except Exception:
            logger.exception("Reranker failed; falling back to pre-rerank ranking")
            return candidates[:k]


def embed_and_index(db: Session, assessment_id: str) -> dict:
    from app.services.providers import get_embedder, get_vector_indexes

    return ChunkIndexer(get_embedder(), get_vector_indexes()).embed_and_index(
        db, assessment_id
    )


def index_chunks(db: Session, assessment_id: str) -> int:
    return int(embed_and_index(db, assessment_id).get("indexed", 0))


def retrieve(
    db: Session,
    assessment_id: str,
    query: str,
    top_k: int | None = None,
) -> list[Chunk]:
    from app.services.providers import get_retriever

    return get_retriever().retrieve(db, assessment_id, query, top_k=top_k)


def search_chunks(db: Session, assessment_id: str, query: str, top: int = 8) -> list[Chunk]:
    return retrieve(db, assessment_id, query, top_k=top)


def clear_local_index(assessment_id: str) -> None:
    from app.services.providers import get_vector_indexes

    for index in get_vector_indexes():
        index.clear(assessment_id)


def describe_vector_index() -> str:
    from app.services.providers import describe_vector_indexes

    return describe_vector_indexes()
