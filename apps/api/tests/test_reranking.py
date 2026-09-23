from __future__ import annotations

import json

from app.models.entities import Chunk
from app.services.rerankers import CrossEncoderReranker, LlmReranker, NoOpReranker
from app.services.search import RerankingRetriever


def _chunk(cid: str, text: str, assessment_id: str, document_id: str) -> Chunk:
    return Chunk(
        id=cid,
        assessment_id=assessment_id,
        document_id=document_id,
        chunk_index=0,
        page=1,
        offset_start=0,
        offset_end=len(text),
        text=text,
    )


class _StubCompleter:
    def __init__(self, content: str | None, *, enabled: bool = True) -> None:
        self._content = content
        self.enabled = enabled
        self.source = "stub"

    def complete(self, system, user, *, temperature=0.1, json_mode=False, response_schema=None):
        return self._content


class _StubInnerRetriever:
    """Fixed candidate pool, in a deliberately "wrong" order, so a reranker's reordering
    is observable independent of any real retrieval/fusion logic."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        self.requested_top_k: int | None = None

    def retrieve(self, db, assessment_id, query, top_k=None):
        self.requested_top_k = top_k
        return self._chunks[:top_k] if top_k else list(self._chunks)


def _make_chunks(n: int, assessment_id: str, document_id: str) -> list[Chunk]:
    return [_chunk(f"c{i}", f"passage number {i}", assessment_id, document_id) for i in range(n)]


def test_noop_reranker_truncates_without_reordering():
    chunks = _make_chunks(5, "a1", "d1")
    result = NoOpReranker().rerank("query", chunks, top_k=3)
    assert [c.id for c in result] == ["c0", "c1", "c2"]


def test_reranking_retriever_requests_a_larger_pool_than_top_k():
    chunks = _make_chunks(10, "a1", "d1")
    inner = _StubInnerRetriever(chunks)
    retriever = RerankingRetriever(inner, NoOpReranker(), candidate_pool=8)

    result = retriever.retrieve(None, "a1", "query", top_k=3)

    assert inner.requested_top_k == 8
    assert len(result) == 3


def test_reranking_retriever_uses_reranker_output_order():
    chunks = _make_chunks(4, "a1", "d1")
    inner = _StubInnerRetriever(chunks)

    class _ReverseReranker:
        def rerank(self, query, chunks, top_k):
            return list(reversed(chunks))[:top_k]

    retriever = RerankingRetriever(inner, _ReverseReranker(), candidate_pool=4)
    result = retriever.retrieve(None, "a1", "query", top_k=2)
    assert [c.id for c in result] == ["c3", "c2"]


def test_reranking_retriever_falls_back_to_inner_order_when_reranker_raises():
    chunks = _make_chunks(4, "a1", "d1")
    inner = _StubInnerRetriever(chunks)

    class _BrokenReranker:
        def rerank(self, query, chunks, top_k):
            raise RuntimeError("boom")

    retriever = RerankingRetriever(inner, _BrokenReranker(), candidate_pool=4)
    result = retriever.retrieve(None, "a1", "query", top_k=2)
    assert [c.id for c in result] == ["c0", "c1"]


def test_reranking_retriever_passes_through_empty_candidates():
    inner = _StubInnerRetriever([])
    retriever = RerankingRetriever(inner, NoOpReranker())
    assert retriever.retrieve(None, "a1", "query", top_k=3) == []


def test_cross_encoder_reranker_falls_back_when_model_unavailable(monkeypatch):
    from app.services import rerankers

    monkeypatch.setattr(rerankers, "_load_cross_encoder", lambda name: None)
    chunks = _make_chunks(3, "a1", "d1")
    result = CrossEncoderReranker("some/model").rerank("query", chunks, top_k=2)
    assert [c.id for c in result] == ["c0", "c1"]


def test_cross_encoder_reranker_reorders_by_predicted_score(monkeypatch):
    from app.services import rerankers

    class _FakeModel:
        def predict(self, pairs):
            # Score higher for later passages so the reorder is unambiguous.
            return [float(i) for i in range(len(pairs))]

    monkeypatch.setattr(rerankers, "_load_cross_encoder", lambda name: _FakeModel())
    chunks = _make_chunks(4, "a1", "d1")
    result = CrossEncoderReranker("some/model").rerank("query", chunks, top_k=2)
    assert [c.id for c in result] == ["c3", "c2"]


def test_cross_encoder_reranker_falls_back_when_scoring_raises(monkeypatch):
    from app.services import rerankers

    class _BrokenModel:
        def predict(self, pairs):
            raise RuntimeError("inference failed")

    monkeypatch.setattr(rerankers, "_load_cross_encoder", lambda name: _BrokenModel())
    chunks = _make_chunks(3, "a1", "d1")
    result = CrossEncoderReranker("some/model").rerank("query", chunks, top_k=2)
    assert [c.id for c in result] == ["c0", "c1"]


def test_llm_reranker_reorders_by_structured_scores():
    chunks = _make_chunks(3, "a1", "d1")
    # Deliberately score the last passage highest so a correct reorder is unambiguous.
    content = json.dumps({"scores": [{"index": 0, "score": 1}, {"index": 1, "score": 2}, {"index": 2, "score": 9}]})
    reranker = LlmReranker(_StubCompleter(content))
    result = reranker.rerank("query", chunks, top_k=2)
    assert [c.id for c in result] == ["c2", "c1"]


def test_llm_reranker_falls_back_when_completer_disabled():
    chunks = _make_chunks(3, "a1", "d1")
    reranker = LlmReranker(_StubCompleter(None, enabled=False))
    result = reranker.rerank("query", chunks, top_k=2)
    assert [c.id for c in result] == ["c0", "c1"]


def test_llm_reranker_falls_back_on_malformed_response():
    chunks = _make_chunks(3, "a1", "d1")
    reranker = LlmReranker(_StubCompleter("not json"))
    result = reranker.rerank("query", chunks, top_k=2)
    assert [c.id for c in result] == ["c0", "c1"]


def test_llm_reranker_falls_back_when_response_is_missing_a_candidate():
    chunks = _make_chunks(3, "a1", "d1")
    # Only scores index 0 and 1 -- index 2 is missing, so the response is incomplete.
    content = json.dumps({"scores": [{"index": 0, "score": 5}, {"index": 1, "score": 1}]})
    reranker = LlmReranker(_StubCompleter(content))
    result = reranker.rerank("query", chunks, top_k=2)
    assert [c.id for c in result] == ["c0", "c1"]


def test_llm_reranker_uses_custom_fallback():
    chunks = _make_chunks(3, "a1", "d1")

    class _ReverseFallback:
        def rerank(self, query, chunks, top_k):
            return list(reversed(chunks))[:top_k]

    reranker = LlmReranker(_StubCompleter(None, enabled=False), fallback=_ReverseFallback())
    result = reranker.rerank("query", chunks, top_k=2)
    assert [c.id for c in result] == ["c2", "c1"]


def test_get_retriever_returns_plain_merging_retriever_when_reranker_off(monkeypatch):
    from app.config import get_settings
    from app.services import providers
    from app.services.search import MergingRetriever

    monkeypatch.setenv("RERANKER", "off")
    get_settings.cache_clear()
    retriever = providers.get_retriever(embedder=object(), indexes=[])
    assert isinstance(retriever, MergingRetriever)


def test_get_retriever_wraps_in_reranking_retriever_when_enabled(monkeypatch):
    from app.config import get_settings
    from app.services import providers

    monkeypatch.setenv("RERANKER", "cross_encoder")
    get_settings.cache_clear()
    retriever = providers.get_retriever(embedder=object(), indexes=[])
    assert isinstance(retriever, RerankingRetriever)


def test_get_reranker_uses_cross_encoder_when_configured(monkeypatch):
    from app.config import get_settings
    from app.services import providers

    monkeypatch.setenv("RERANKER", "cross_encoder")
    get_settings.cache_clear()
    assert isinstance(providers.get_reranker(), CrossEncoderReranker)


def test_get_reranker_falls_back_to_cross_encoder_under_mock_llm(monkeypatch):
    """`RERANKER=llm` under MOCK_LLM=true (no chat completer configured) shouldn't try to
    use a disabled completer as the primary strategy -- it should resolve straight to the
    deterministic cross-encoder instead."""
    from app.config import get_settings
    from app.services import providers

    monkeypatch.setenv("RERANKER", "llm")
    monkeypatch.setenv("MOCK_LLM", "true")
    get_settings.cache_clear()
    assert isinstance(providers.get_reranker(), CrossEncoderReranker)


def test_get_reranker_off_returns_noop(monkeypatch):
    from app.config import get_settings
    from app.services import providers

    monkeypatch.setenv("RERANKER", "off")
    get_settings.cache_clear()
    assert isinstance(providers.get_reranker(), NoOpReranker)
