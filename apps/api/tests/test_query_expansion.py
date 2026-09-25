"""Query expansion: closing the abstraction gap hybrid search can't.

The integration test reproduces a reported miss: a query for "technical debt / blockers"
against documents that describe those things as "PCI re-certification", "manual failover"
and "coupling". Hybrid search (dense + BM25) was already on and still missed chunks —
BM25 shares no words with them, and the embedding model ranked them near the bottom.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.query_expansion import MAX_EXPANSIONS, expand_query, lexicon_expansions
from app.services.search import reciprocal_rank_fusion

STRESS = Path(__file__).resolve().parents[3] / "sample-data" / "stress"


# --------------------------------------------------------------------------- #
# Unit
# --------------------------------------------------------------------------- #
def test_abstract_concepts_expand_to_concrete_wording():
    expansions = " ".join(lexicon_expansions("technical debt / blockers"))
    for concrete in ("manual failover", "re-certification", "legacy"):
        assert concrete in expansions


def test_multiple_concepts_are_interleaved_not_starved():
    """A query naming two concepts gets expansions for both, within the budget."""
    out = lexicon_expansions("technical debt / blockers")
    assert len(out) == MAX_EXPANSIONS
    assert any("legacy" in e for e in out)  # technical debt
    assert any("cannot move before" in e for e in out)  # blockers


def test_concrete_queries_are_not_expanded():
    for q in ("server vCPU memory storage disk", "databases data stores SQL Oracle", "RTO RPO SLA"):
        assert expand_query(q) == [q]


def test_triggers_match_whole_words_only():
    assert lexicon_expansions("debtor ledger") == []  # 'debt' inside another word
    assert lexicon_expansions("tech-debt review") != []


def test_original_query_always_first_and_off_mode():
    assert expand_query("technical debt")[0] == "technical debt"
    assert expand_query("technical debt", mode="off") == ["technical debt"]


def test_llm_mode_without_a_chat_model_falls_back_to_lexicon(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "true")
    assert expand_query("technical debt", mode="llm") == expand_query("technical debt", mode="lexicon")


def test_weighted_rrf_lets_the_original_query_dominate():
    # 'x' is #1 for the original query; 'y' is #1 for a down-weighted expansion.
    fused = reciprocal_rank_fusion([["x", "y"], ["y", "x"]], weights=[1.0, 0.6])
    assert fused[0] == "x"
    # Unweighted behaviour is unchanged for existing callers.
    assert set(reciprocal_rank_fusion([["a", "b"], ["b", "a"]])) == {"a", "b"}


# --------------------------------------------------------------------------- #
# Integration: the reported miss, on real embeddings + BM25
# --------------------------------------------------------------------------- #
_TARGETS = ("pci re-certification", "manual failover", "coupling")
_QUERY = "technical debt / blockers"


@pytest.mark.eval
def test_expansion_recovers_chunks_hybrid_search_missed(db_session, assessment, monkeypatch):
    from app.config import get_settings
    from app.models.entities import Document, DocumentType
    from app.services.ingest import ingest_documents
    from app.services.providers import get_embedder, get_retriever, get_vector_indexes
    from app.services.search import ChunkIndexer

    monkeypatch.setenv("LOCAL_EMBEDDINGS", "true")
    get_settings.cache_clear()
    for fn in (
        "solution-architecture.docx", "migration-runbook.md", "requirements-nfr.docx",
        "discovery-questionnaire.docx", "cmdb-export.xlsx", "fleet-inventory.csv", "cloud-assets.json",
    ):
        path = STRESS / fn
        if not path.exists():
            pytest.skip("stress fixtures not generated (scripts/generate_stress_fixtures.py)")
        db_session.add(
            Document(assessment_id=assessment.id, filename=fn, content_type="",
                     storage_path=str(path), doc_type=DocumentType.unknown)
        )
    db_session.commit()
    ingest_documents(db_session, assessment.id)
    embedder, indexes = get_embedder(), get_vector_indexes()
    ChunkIndexer(embedder, indexes).embed_and_index(db_session, assessment.id)

    def found(mode: str) -> set[str]:
        get_settings().retrieval_query_expansion = mode
        hits = get_retriever(embedder, indexes).retrieve(db_session, assessment.id, _QUERY, top_k=8)
        text = " ".join(h.text.lower() for h in hits)
        return {t for t in _TARGETS if t in text}

    assert found("off") != set(_TARGETS), "hybrid-only unexpectedly found everything; the test no longer discriminates"
    assert found("lexicon") == set(_TARGETS)
