"""LLM + embedding usage: calls, tokens and cost, attributed per assessment run and stage."""

from __future__ import annotations

import operator
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, TypedDict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models.entities import Assessment, Claim, LlmUsage, PipelineStatus
from app.schemas.api import ExtractionResult
from app.services import usage
from app.services.embeddings import AzureEmbedder
from app.services.llm_clients import OpenAICompatibleCompleter
from app.services.pipeline import AssessmentPipeline, PipelineServices


# --------------------------------------------------------------------------- #
# Fake provider clients shaped like the OpenAI SDK's responses
# --------------------------------------------------------------------------- #
class FakeChatClient:
    def __init__(self, *, with_usage: bool = True) -> None:
        self.with_usage = with_usage
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        usage_obj = SimpleNamespace(prompt_tokens=1200, completion_tokens=300) if self.with_usage else None
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=usage_obj)


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, model, input):
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.1, 0.2]) for i in range(len(input))],
            usage=SimpleNamespace(prompt_tokens=50 * len(input), total_tokens=50 * len(input)),
        )


def _completer(**kw) -> OpenAICompatibleCompleter:
    return OpenAICompatibleCompleter(FakeChatClient(**kw), "gpt-4o", "azure-openai")


# --------------------------------------------------------------------------- #
# Recording + pricing
# --------------------------------------------------------------------------- #
def test_chat_and_embedding_calls_are_counted_with_provider_tokens(monkeypatch):
    monkeypatch.setattr("app.azure_clients.get_azure_openai_client", lambda: FakeEmbeddingClient())
    with usage.metered("a1", "pipeline") as meter:
        usage.set_stage("extracting")
        _completer().complete("sys", "user")
        _completer().complete("sys", "user")
        usage.set_stage("indexing")
        AzureEmbedder().embed_texts(["one", "two", "three"])
    chat = meter.counters[("extracting", "chat", "azure-openai", "gpt-4o")]
    emb = meter.counters[("indexing", "embedding", "azure-openai", "text-embedding-3-small")]
    assert (chat.calls, chat.input_tokens, chat.output_tokens, chat.estimated_calls) == (2, 2400, 600, 0)
    assert (emb.calls, emb.input_tokens) == (1, 150)


def test_missing_provider_usage_is_estimated_and_flagged():
    with usage.metered("a1", "pipeline") as meter:
        _completer(with_usage=False).complete("You are a helpful analyst.", "Summarise the estate.")
    (counter,) = meter.counters.values()
    assert counter.estimated_calls == 1 and counter.input_tokens > 0


def test_calls_outside_any_meter_are_ignored():
    _completer().complete("sys", "user")  # no active meter -> nothing to attribute, no error


def test_pricing_uses_the_configured_rates_and_local_is_free():
    # defaults: 2.50 / 10.00 per 1M chat tokens in/out, 0.02 per 1M embedding tokens
    assert usage.price("chat", "azure-openai", 1_000_000, 100_000) == pytest.approx(3.50)
    assert usage.price("embedding", "azure-openai", 2_000_000, 0) == pytest.approx(0.04)
    assert usage.price("embedding", "local", 10_000_000, 0) == 0.0


def test_meter_propagates_into_langgraph_worker_threads():
    from langgraph.graph import END, START, StateGraph

    class S(TypedDict):
        n: Annotated[list, operator.add]

    def node(_s):
        _completer().complete("sys", "user")
        return {"n": [1]}

    graph = StateGraph(S)
    graph.add_node("a", node)
    graph.add_node("b", node)
    graph.add_edge(START, "a")
    graph.add_edge(START, "b")
    graph.add_edge("a", END)
    graph.add_edge("b", END)
    with usage.metered("a1", "pipeline") as meter:
        graph.compile().invoke({"n": []})
    assert sum(c.calls for c in meter.counters.values()) == 2


# --------------------------------------------------------------------------- #
# Pipeline runs
# --------------------------------------------------------------------------- #
class _LlmExtractor:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def extract_assessment(self, db, assessment_id):
        for _ in range(3):
            _completer().complete("extract", "chunk text")
        if self.fail:
            raise RuntimeError("model budget exceeded")
        return ExtractionResult(), {}


class _Noop:
    def embed_texts(self, texts):
        return [[0.0] for _ in texts]

    def embed_query(self, text):
        return [0.0]

    def retrieve(self, *a, **k):
        return []


def _pipeline(db_session, extractor, tmp_path) -> AssessmentPipeline:
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    return AssessmentPipeline(
        PipelineServices(
            embedder=_Noop(),
            indexes=[],
            extractor=extractor,
            retriever=_Noop(),
            storage_dir=str(tmp_path),
            session_factory=factory,
        )
    )


def test_a_run_records_its_usage_by_stage(db_session, assessment, tmp_path):
    _pipeline(db_session, _LlmExtractor(), tmp_path).run(assessment.id)
    db_session.expire_all()
    a = db_session.get(Assessment, assessment.id)
    rows = db_session.query(LlmUsage).filter(LlmUsage.assessment_id == assessment.id).all()
    assert [(r.scope, r.stage, r.kind, r.calls) for r in rows] == [("pipeline", "extracting", "chat", 3)]
    assert rows[0].run_started_at == a.pipeline_started_at.isoformat()
    assert rows[0].cost == pytest.approx(3 * (1200 * 2.5 + 300 * 10) / 1e6)

    summary = usage.usage_summary(db_session, a)
    assert summary["latest_run"]["chat"]["calls"] == 3
    assert summary["latest_run"]["by_stage"][0]["label"] == "Extract facts"


def test_a_failed_run_still_records_what_it_consumed(db_session, assessment, tmp_path):
    _pipeline(db_session, _LlmExtractor(fail=True), tmp_path).run(assessment.id)
    rows = db_session.query(LlmUsage).filter(LlmUsage.assessment_id == assessment.id).all()
    assert sum(r.calls for r in rows) == 3


def test_summary_separates_latest_run_after_run_usage_and_history(db_session, assessment):
    old, new = "2026-09-01T10:00:00", "2026-09-10T10:00:00"
    assessment.pipeline_started_at = datetime.fromisoformat(new)
    for scope, run, calls, cost in [
        ("pipeline", old, 40, 0.30),
        ("pipeline", new, 25, 0.20),
        ("interactive", new, 4, 0.02),
    ]:
        db_session.add(
            LlmUsage(
                assessment_id=assessment.id,
                scope=scope,
                run_started_at=run,
                stage="extracting",
                kind="chat",
                provider="azure-openai",
                model="gpt-4o",
                calls=calls,
                input_tokens=calls * 1000,
                output_tokens=calls * 100,
                cost=cost,
            )
        )
    db_session.commit()
    summary = usage.usage_summary(db_session, assessment)
    assert summary["latest_run"]["chat"]["calls"] == 25 and summary["latest_run"]["total_cost"] == 0.2
    assert summary["since_run"]["chat"]["calls"] == 4
    assert [r["run_started_at"] for r in summary["runs"]] == [new, old]
    assert summary["all_time"]["total_cost"] == pytest.approx(0.52)


# --------------------------------------------------------------------------- #
# Requests after a run (review clicks, questions): the HTTP path
# --------------------------------------------------------------------------- #
@pytest.fixture
def shared(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr("app.database.SessionLocal", factory)  # where usage rows are flushed
    session = factory()
    yield session
    session.close()
    engine.dispose()


def test_requests_after_a_run_are_metered_and_reported(shared, monkeypatch):
    from app.main import app

    started = datetime(2026, 9, 10, 10, 0, 0)
    a = Assessment(
        name="usage", status=PipelineStatus.completed, pipeline_started_at=started, pipeline_finished_at=started
    )
    shared.add(a)
    shared.commit()
    shared.add(
        Claim(
            assessment_id=a.id,
            entity_type="server",
            entity_key="app-01",
            attribute="name",
            value="app-01",
            confidence=0.9,
            evidence_refs=[],
            is_selected=True,
            needs_human_review=False,
            unsupported=False,
        )
    )
    shared.commit()
    monkeypatch.setattr("app.services.llm_reasoning.get_chat_completer", lambda: _completer())
    app.dependency_overrides[get_db] = lambda: shared
    try:
        client = TestClient(app)
        assert client.get(f"/assessments/{a.id}/assessment-questions").status_code == 200
        body = client.get(f"/assessments/{a.id}/usage").json()
    finally:
        app.dependency_overrides.clear()
    assert body["since_run"]["chat"]["calls"] >= 1  # the answers' prose rewrite
    assert body["since_run"]["chat"]["input_tokens"] >= 1200
    assert body["latest_run"]["chat"]["calls"] == 0
    assert body["rates"]["llm_input_per_million"] == 2.5 and body["currency"] == "USD"
