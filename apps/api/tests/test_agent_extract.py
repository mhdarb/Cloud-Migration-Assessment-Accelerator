from app.models.entities import Chunk, Document, DocumentType
from app.services.agent_extract import (
    RAG_QUERIES,
    build_llm_extract_graph,
    build_mock_extract_graph,
    run_extract_agent,
)


def _docs_covering_all_doc_types() -> dict[str, Document]:
    """With real planner gates live, every skill's `applies_to` needs its signal doc
    type present to run — this reproduces the pre-planner "run everything" baseline
    (doc §5.4) so tests about the query-count/gap-query mechanics aren't also
    (accidentally) testing planner filtering."""
    types = [
        DocumentType.architecture,
        DocumentType.inventory,
        DocumentType.requirements,
        DocumentType.questionnaire,
        DocumentType.code_snapshot,
    ]
    return {f"doc-{dt.value}": Document(id=f"doc-{dt.value}", doc_type=dt) for dt in types}


class _DummyRetriever:
    def retrieve(self, *args, **kwargs):
        return []


class _DummyLlm:
    def extract(self, query, chunk_payload):
        from app.schemas.api import ExtractionResult

        return ExtractionResult()


def test_mock_graph_compiles():
    class _Dummy:
        pass

    g = build_mock_extract_graph(_Dummy(), {}, _DummyRetriever())  # type: ignore[arg-type]
    assert g is not None


def test_llm_graph_compiles():
    class _Dummy:
        pass

    g = build_llm_extract_graph(_Dummy(), {}, _DummyRetriever(), _DummyLlm())  # type: ignore[arg-type]
    assert g is not None


def test_run_extract_agent_mock(db_session, assessment, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LOCAL_EMBEDDINGS", "true")
    monkeypatch.setenv("MOCK_LLM", "true")
    get_settings.cache_clear()

    doc = Document(
        assessment_id=assessment.id,
        filename="arch.txt",
        content_type="text/plain",
        storage_path=str(tmp_path / "arch.txt"),
        doc_type=DocumentType.architecture,
        precedence=80,
    )
    db_session.add(doc)
    db_session.flush()
    chunk = Chunk(
        assessment_id=assessment.id,
        document_id=doc.id,
        chunk_index=0,
        page=1,
        offset_start=0,
        offset_end=100,
        text=(
            "Application: Billing Service — customer invoices.\n"
            "Billing Service is hosted on server app-bill-01.\n"
            "Server: app-bill-01 runs Windows Server 2019.\n"
            "Database: FinanceDB (Oracle)\n"
        ),
    )
    db_session.add(chunk)
    db_session.commit()

    # Index for local retrieve
    from app.services.llm_extractors import HeuristicLlmExtractor
    from app.services.providers import get_retriever
    from app.services.search import embed_and_index

    embed_and_index(db_session, assessment.id)

    docs = {doc.id: doc}
    result, metrics = run_extract_agent(
        db_session,
        assessment.id,
        docs,
        retriever=get_retriever(),
        llm=HeuristicLlmExtractor(docs_by_id=docs),
        use_mock=True,
    )
    assert metrics["agent_harness"] == "langgraph"
    assert metrics["retrieval_mode"] == "langgraph_heuristic_rag"
    assert metrics["rag_queries"] == len(RAG_QUERIES)
    assert metrics["chunks_retrieved"] >= 1
    assert any(c.entity_type == "application" for c in result.claims)
    assert any(c.chunk_ids for c in result.claims)


def test_budget_breaker_short_circuits_llm_path(db_session, assessment, monkeypatch):
    monkeypatch.setenv("AGENT_MAX_LLM_CALLS", "2")
    from app.config import get_settings

    get_settings.cache_clear()

    class FakeChunk:
        def __init__(self):
            self.id = "chunk-budget"
            self.document_id = "doc-budget"
            self.page = 1
            self.text = "Some architecture excerpt."

    class OneChunkRetriever:
        def retrieve(self, *args, **kwargs):
            return [FakeChunk()]

    class CountingLlm:
        calls = 0

        def extract(self, query, chunk_payload):
            from app.schemas.api import ExtractionResult

            self.calls += 1
            return ExtractionResult(gaps=["no evidence"])

    llm = CountingLlm()
    result, metrics = run_extract_agent(
        db_session,
        assessment.id,
        _docs_covering_all_doc_types(),
        retriever=OneChunkRetriever(),
        llm=llm,
        use_mock=False,
    )
    assert metrics["budget_exceeded"] is True
    # Budget check runs after each query's accumulate step, so it stops well short
    # of the full RAG_QUERIES + gap-query sweep once the call cap is hit.
    assert llm.calls <= 3
    assert llm.calls < len(RAG_QUERIES)


def test_planner_skips_irrelevant_skills_for_narrow_upload(db_session, assessment):
    """A questionnaire-only upload has no signal for sizing/dependencies/databases —
    the heuristic planner should run noticeably fewer than all 11 skills."""

    class NoHitRetriever:
        def retrieve(self, *args, **kwargs):
            return []

    class NoOpLlm:
        def extract(self, query, chunk_payload, **kwargs):
            from app.schemas.api import ExtractionResult

            return ExtractionResult()

    docs = {"doc-q": Document(id="doc-q", doc_type=DocumentType.questionnaire)}
    _, metrics = run_extract_agent(
        db_session,
        assessment.id,
        docs,
        retriever=NoHitRetriever(),
        llm=NoOpLlm(),
        use_mock=False,
    )
    assert 0 < metrics["rag_queries"] < len(RAG_QUERIES)


def test_planner_off_reproduces_full_sweep(db_session, assessment, monkeypatch):
    monkeypatch.setenv("RAG_PLANNER", "off")
    from app.config import get_settings

    get_settings.cache_clear()

    class NoHitRetriever:
        def retrieve(self, *args, **kwargs):
            return []

    class NoOpLlm:
        def extract(self, query, chunk_payload, **kwargs):
            from app.schemas.api import ExtractionResult

            return ExtractionResult()

    docs = {"doc-q": Document(id="doc-q", doc_type=DocumentType.questionnaire)}
    _, metrics = run_extract_agent(
        db_session,
        assessment.id,
        docs,
        retriever=NoHitRetriever(),
        llm=NoOpLlm(),
        use_mock=False,
    )
    # Empty retrieval -> guardrails refuse each query -> gaps accumulate -> one extra
    # templated gap query, same mechanism `test_llm_path_adds_one_gap_query` exercises.
    assert metrics["rag_queries"] == len(RAG_QUERIES) + 1


def test_llm_path_adds_one_gap_query(db_session, assessment):
    class FakeChunk:
        def __init__(self):
            self.id = "chunk-gap"
            self.document_id = "doc-gap"
            self.page = 1
            self.text = "Architecture excerpt with no recovery objective."

    class OneChunkRetriever:
        def retrieve(self, *args, **kwargs):
            return [FakeChunk()]

    class GapLlm:
        calls = []

        def extract(self, query, chunk_payload):
            from app.schemas.api import ExtractionResult

            self.calls.append(query)
            return ExtractionResult(gaps=["RTO not stated"])

    llm = GapLlm()
    result, metrics = run_extract_agent(
        db_session,
        assessment.id,
        _docs_covering_all_doc_types(),
        retriever=OneChunkRetriever(),
        llm=llm,
        use_mock=False,
    )
    assert metrics["retrieval_mode"] == "langgraph_llm_rag"
    assert metrics["rag_queries"] == len(RAG_QUERIES) + 1
    assert len(llm.calls) == len(RAG_QUERIES) + 1
    assert llm.calls[-1].startswith("Find evidence for remaining assessment gaps:")
    assert "RTO not stated" in result.gaps
