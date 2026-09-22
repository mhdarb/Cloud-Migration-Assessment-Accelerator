from app.models.entities import Chunk, Document, DocumentType
from app.services.search import (
    ChunkIndexer,
    KeywordRetriever,
    MergingRetriever,
    describe_vector_index,
    embed_and_index,
    retrieve,
)
from app.services.vector_indexes import FaissVectorIndex, faiss_index_path


class _StubEmbedder:
    """Test-only token vectors. Not used in production."""

    _vocab = (
        "billing",
        "service",
        "hosted",
        "server",
        "cafeteria",
        "menu",
        "soup",
        "salad",
        "oracledb",
        "financedb",
    )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        tokens = set(query.lower().replace("-", " ").split())
        vec = [1.0 if term in tokens or term in query.lower() else 0.0 for term in self._vocab]
        if "finance" in query.lower() or "oracledb" in query.lower().replace(" ", ""):
            vec[self._vocab.index("financedb")] = 1.0
        return vec


def test_faiss_indexes_and_retrieves(db_session, assessment, tmp_path, monkeypatch):
    from app.config import get_settings
    from app.services import providers

    monkeypatch.setenv("EMBEDDINGS_DIR", str(tmp_path / "embeddings"))
    get_settings.cache_clear()
    get_settings().ensure_dirs()
    monkeypatch.setattr(providers, "get_embedder", lambda: _StubEmbedder())
    monkeypatch.setattr(providers, "get_vector_indexes", lambda: [FaissVectorIndex()])

    doc = Document(
        assessment_id=assessment.id,
        filename="estate.txt",
        content_type="text/plain",
        storage_path=str(tmp_path / "estate.txt"),
        doc_type=DocumentType.architecture,
        precedence=80,
    )
    db_session.add(doc)
    db_session.flush()
    relevant = Chunk(
        assessment_id=assessment.id,
        document_id=doc.id,
        chunk_index=0,
        page=1,
        offset_start=0,
        offset_end=80,
        text="Billing Service is hosted on server app-bill-01 running Windows Server.",
    )
    distractor = Chunk(
        assessment_id=assessment.id,
        document_id=doc.id,
        chunk_index=1,
        page=1,
        offset_start=80,
        offset_end=160,
        text="The cafeteria menu includes soup, salad, and seasonal desserts.",
    )
    db_session.add_all([relevant, distractor])
    db_session.commit()

    result = embed_and_index(db_session, assessment.id)
    assert result["indexed"] == 2
    assert result["backend"] == "faiss"
    assert describe_vector_index() == "faiss"
    assert faiss_index_path(assessment.id).exists()

    hits = retrieve(db_session, assessment.id, "Where is Billing Service hosted?", top_k=1)
    assert hits
    assert hits[0].id == relevant.id


class _FakeIndex:
    name = "fake"

    def __init__(self) -> None:
        self.stored: list[str] = []

    def upsert(self, assessment_id, chunks, vectors):
        self.stored = [c.id for c in chunks]
        return len(chunks)

    def search(self, db, assessment_id, query, query_vector, top_k):
        from app.services.vector_indexes import get_chunks_by_ids

        return get_chunks_by_ids(db, self.stored[:top_k])

    def clear(self, assessment_id) -> None:
        self.stored = []


def test_indexer_and_retriever_use_injected_ports(db_session, assessment, tmp_path):
    doc = Document(
        assessment_id=assessment.id,
        filename="n.txt",
        content_type="text/plain",
        storage_path=str(tmp_path / "n.txt"),
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
        offset_end=20,
        text="Oracle FinanceDB is the system of record.",
    )
    db_session.add(chunk)
    db_session.commit()

    fake = _FakeIndex()
    meta = ChunkIndexer(_StubEmbedder(), [fake]).embed_and_index(db_session, assessment.id)
    assert meta["backend"] == "fake"
    assert fake.stored == [chunk.id]

    hits = MergingRetriever(_StubEmbedder(), [fake], KeywordRetriever()).retrieve(
        db_session, assessment.id, "FinanceDB", top_k=1
    )
    assert hits[0].id == chunk.id
