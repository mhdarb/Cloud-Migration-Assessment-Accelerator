from __future__ import annotations

from app.config import get_settings
from app.database import SessionLocal
from app.services.classify import KeywordClassifier, LlmClassifier
from app.services.embeddings import AzureEmbedder, FallbackEmbedder, SentenceTransformerEmbedder
from app.services.extraction import AssessmentClaimExtractor
from app.services.extraction_strategies import EnsembleExtractor
from app.services.graph_sinks import Neo4jGraphSink, NullGraphSink
from app.services.llm_clients import get_chat_completer
from app.services.llm_extractors import ChatLlmExtractor, HeuristicLlmExtractor
from app.services.neo4j_graph import neo4j_available
from app.services.pipeline import PipelineServices
from app.services.ports import (
    DocClassifier,
    Embedder,
    GraphSink,
    LlmExtractor,
    QuestionPlanner,
    Retriever,
    VectorIndex,
)
from app.services.question_planning import (
    HeuristicQuestionPlanner,
    LlmQuestionPlanner,
    NoOpQuestionPlanner,
)
from app.services.search import BM25Retriever, KeywordRetriever, MergingRetriever
from app.services.vector_indexes import AzureSearchIndex, FaissVectorIndex


def get_embedder() -> Embedder:
    settings = get_settings()
    local = SentenceTransformerEmbedder()
    if settings.use_azure_embeddings:
        return FallbackEmbedder(AzureEmbedder(), local, reraise=settings.is_lz_profile)
    return local


def get_vector_indexes() -> list[VectorIndex]:
    settings = get_settings()
    if settings.use_azure_embeddings or (
        settings.azure_search_configured and not settings.local_embeddings
    ):
        return [AzureSearchIndex()]
    try:
        import faiss  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("faiss-cpu is required for local vector search") from exc
    return [FaissVectorIndex()]


def describe_vector_indexes() -> str:
    names = [index.name for index in get_vector_indexes()]
    return "+".join(names) if names else "none"


def get_keyword_retriever(top_k: int) -> KeywordRetriever | BM25Retriever:
    try:
        import rank_bm25  # noqa: F401
    except ImportError:
        return KeywordRetriever(top_k=top_k)
    return BM25Retriever(top_k=top_k)


def get_retriever(
    embedder: Embedder | None = None,
    indexes: list[VectorIndex] | None = None,
) -> Retriever:
    settings = get_settings()
    return MergingRetriever(
        embedder or get_embedder(),
        indexes if indexes is not None else get_vector_indexes(),
        get_keyword_retriever(settings.rag_top_k),
        top_k=settings.rag_top_k,
    )


def get_llm_extractor(docs_by_id: dict | None = None) -> LlmExtractor:
    settings = get_settings()
    heuristic = HeuristicLlmExtractor(docs_by_id=docs_by_id)
    if settings.use_mock_llm:
        return heuristic
    if settings.extraction_strategy == "heuristic":
        return heuristic
    chat = ChatLlmExtractor(get_chat_completer())
    if settings.extraction_strategy == "ensemble":
        return EnsembleExtractor(heuristic, chat)
    return chat


def get_doc_classifier() -> DocClassifier:
    settings = get_settings()
    if settings.doc_classifier == "llm" and settings.chat_llm_configured:
        return LlmClassifier(get_chat_completer())
    return KeywordClassifier()


def get_question_planner() -> QuestionPlanner:
    settings = get_settings()
    if not settings.question_planner_enabled:
        return NoOpQuestionPlanner()
    if settings.chat_llm_configured and not settings.use_mock_llm:
        return LlmQuestionPlanner(get_chat_completer())
    return HeuristicQuestionPlanner()


def get_graph_sink() -> GraphSink:
    if neo4j_available():
        return Neo4jGraphSink()
    return NullGraphSink()


def build_pipeline_services() -> PipelineServices:
    settings = get_settings()
    embedder = get_embedder()
    indexes = get_vector_indexes()
    retriever = get_retriever(embedder, indexes)
    return PipelineServices(
        embedder=embedder,
        indexes=indexes,
        graph=get_graph_sink(),
        extractor=AssessmentClaimExtractor(
            retriever,
            get_llm_extractor(),
            rag_enabled=settings.rag_enabled,
            use_mock=settings.use_mock_llm,
        ),
        retriever=retriever,
        storage_dir=settings.storage_dir,
        session_factory=SessionLocal,
    )
