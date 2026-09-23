from __future__ import annotations

from app.config import get_settings
from app.models.entities import Chunk, Document, DocumentType
from app.services.vector_indexes import FaissVectorIndex


def _seed(db_session, assessment, tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_DIR", str(tmp_path / "embeddings"))
    get_settings.cache_clear()
    get_settings().ensure_dirs()
    doc = Document(
        assessment_id=assessment.id,
        filename="a.txt",
        content_type="text/plain",
        storage_path=str(tmp_path / "a.txt"),
        doc_type=DocumentType.architecture,
    )
    db_session.add(doc)
    db_session.flush()
    chunk = Chunk(
        assessment_id=assessment.id,
        document_id=doc.id,
        chunk_index=0,
        page=1,
        offset_start=0,
        offset_end=10,
        text="Billing Service runs on app-bill-01.",
    )
    db_session.add(chunk)
    db_session.commit()
    return chunk


def test_faiss_search_reuses_cached_index_across_calls(db_session, assessment, tmp_path, monkeypatch):
    chunk = _seed(db_session, assessment, tmp_path, monkeypatch)
    index = FaissVectorIndex()
    index.upsert(assessment.id, [chunk], [[1.0, 0.0]])

    import faiss

    calls = {"count": 0}
    original_read_index = faiss.read_index

    def counting_read_index(path):
        calls["count"] += 1
        return original_read_index(path)

    monkeypatch.setattr(faiss, "read_index", counting_read_index)

    index.search(db_session, assessment.id, "query one", [1.0, 0.0], top_k=5)
    index.search(db_session, assessment.id, "query two", [1.0, 0.0], top_k=5)
    index.search(db_session, assessment.id, "query three", [1.0, 0.0], top_k=5)

    assert calls["count"] == 1, "the index file should only be read from disk once"
    assert assessment.id in index._cache


def test_faiss_search_reloads_after_upsert_replaces_the_index(
    db_session, assessment, tmp_path, monkeypatch
):
    chunk = _seed(db_session, assessment, tmp_path, monkeypatch)
    index = FaissVectorIndex()
    index.upsert(assessment.id, [chunk], [[1.0, 0.0]])
    index.search(db_session, assessment.id, "query", [1.0, 0.0], top_k=5)
    assert assessment.id in index._cache

    # A re-upsert (e.g. re-running the pipeline after documents changed) must invalidate
    # the cached copy -- otherwise search() would keep serving results from the old index.
    index.upsert(assessment.id, [chunk], [[0.0, 1.0]])
    assert assessment.id not in index._cache

    results = index.search(db_session, assessment.id, "query", [0.0, 1.0], top_k=5)
    assert len(results) == 1
    assert assessment.id in index._cache


def test_faiss_clear_evicts_the_cache(db_session, assessment, tmp_path, monkeypatch):
    chunk = _seed(db_session, assessment, tmp_path, monkeypatch)
    index = FaissVectorIndex()
    index.upsert(assessment.id, [chunk], [[1.0, 0.0]])
    index.search(db_session, assessment.id, "query", [1.0, 0.0], top_k=5)
    assert assessment.id in index._cache

    index.clear(assessment.id)
    assert assessment.id not in index._cache
    assert index.search(db_session, assessment.id, "query", [1.0, 0.0], top_k=5) == []


def test_faiss_cache_is_scoped_per_assessment(db_session, assessment, tmp_path, monkeypatch):
    chunk = _seed(db_session, assessment, tmp_path, monkeypatch)
    index = FaissVectorIndex()
    index.upsert(assessment.id, [chunk], [[1.0, 0.0]])
    index.search(db_session, assessment.id, "query", [1.0, 0.0], top_k=5)

    other_id = "some-other-assessment"
    assert index.search(db_session, other_id, "query", [1.0, 0.0], top_k=5) == []
    assert other_id not in index._cache
    assert assessment.id in index._cache
