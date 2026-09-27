"""A review click must not redo work its decision can't change.

Reported: with a real LLM + embeddings, every Accept in the Review tab took a long time.
Each click rebuilt the report, which re-answered every question — re-running retrieval
(an embedding + search call per expanded sub-query, per question) and, on the page reload
that followed, the LLM rewrite of every answer.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.models.entities import Chunk, Claim, Document, PipelineStatus
from app.services.assessment_questions import persist_ad_hoc_question
from app.services.assessment_service import ClaimReview, review_claims
from app.services.llm_reasoning import GroundedProse
from app.services.search import CachingRetriever, KeywordRetriever


class CountingRetriever:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._inner = KeywordRetriever(top_k=4)

    def retrieve(self, db, assessment_id, query, top_k=None):
        self.calls.append(query)
        return self._inner.retrieve(db, assessment_id, query, top_k=top_k)


class CountingCompleter:
    enabled = True
    source = "test"
    cache_key = "test:model-a"

    def __init__(self) -> None:
        self.sent: list[list[str]] = []

    def complete(self, system, user, *, temperature=0.1, json_mode=False):
        items = json.loads(user)
        self.sent.append([i["id"] for i in items])
        return json.dumps({"answers": [{"id": i["id"], "text": f"Prose for {i['question']}"} for i in items]})


def _completed_run(db, assessment) -> None:
    assessment.status = PipelineStatus.completed
    assessment.pipeline_started_at = datetime(2026, 9, 1, 12, 0, 0)
    assessment.pipeline_finished_at = assessment.pipeline_started_at + timedelta(minutes=3)
    doc = Document(assessment_id=assessment.id, filename="runbook.docx", storage_path="/tmp/r.docx")
    db.add(doc)
    db.flush()
    db.add(
        Chunk(
            assessment_id=assessment.id,
            document_id=doc.id,
            chunk_index=0,
            text="Known gaps: firewall rules between DC-North and DC-South are not documented.",
        )
    )
    for i, (attr, value) in enumerate([("os", "RHEL 9"), ("os", "Windows Server 2022")]):
        db.add(
            Claim(
                assessment_id=assessment.id,
                entity_type="server",
                entity_key=f"srv-{i}",
                attribute=attr,
                value=value,
                confidence=0.6,
                evidence_refs=[],
                is_selected=True,
                needs_human_review=True,
                unsupported=False,
            )
        )
    db.commit()
    for q in ("What known gaps remain?", "Which firewall rules exist between sites?"):
        persist_ad_hoc_question(db, assessment.id, q)


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #
def test_review_clicks_reuse_retrieval_from_the_same_run(db_session, assessment):
    _completed_run(db_session, assessment)
    counting = CountingRetriever()
    retriever = CachingRetriever(counting)
    claims = db_session.query(Claim).filter(Claim.assessment_id == assessment.id).all()

    review_claims(db_session, assessment, [ClaimReview(claims[0].id, "accept")], retriever=retriever)
    first = len(counting.calls)
    assert first >= 2  # each custom question retrieved once
    review_claims(db_session, assessment, [ClaimReview(claims[1].id, "accept")], retriever=retriever)
    assert len(counting.calls) == first  # second click: no retrieval at all


def test_a_new_run_is_never_served_stale_results(db_session, assessment):
    _completed_run(db_session, assessment)
    counting = CountingRetriever()
    retriever = CachingRetriever(counting)
    retriever.retrieve(db_session, assessment.id, "known gaps", top_k=4)
    retriever.retrieve(db_session, assessment.id, "known gaps", top_k=4)
    assert len(counting.calls) == 1
    assessment.pipeline_started_at = datetime(2026, 9, 5, 9, 0, 0)  # re-run
    db_session.commit()
    retriever.retrieve(db_session, assessment.id, "known gaps", top_k=4)
    assert len(counting.calls) == 2


def test_runs_without_a_start_time_are_not_cached(db_session, assessment):
    counting = CountingRetriever()
    retriever = CachingRetriever(counting)
    for _ in range(2):
        retriever.retrieve(db_session, assessment.id, "anything", top_k=4)
    assert len(counting.calls) == 2


def test_deleted_chunks_force_a_fresh_retrieval(db_session, assessment):
    _completed_run(db_session, assessment)
    counting = CountingRetriever()
    retriever = CachingRetriever(counting)
    assert retriever.retrieve(db_session, assessment.id, "firewall rules", top_k=4)
    db_session.query(Chunk).filter(Chunk.assessment_id == assessment.id).delete()
    db_session.commit()
    assert retriever.retrieve(db_session, assessment.id, "firewall rules", top_k=4) == []
    assert len(counting.calls) == 2


# --------------------------------------------------------------------------- #
# LLM rewrite of answers
# --------------------------------------------------------------------------- #
def _answers() -> list[dict]:
    return [
        {"id": "a1", "question": "Which OS?", "facts": [{"value": "RHEL 9"}], "evidence": []},
        {"id": "a2", "question": "Which DB?", "facts": [{"value": "PostgreSQL"}], "evidence": []},
    ]


def test_unchanged_answers_are_not_sent_to_the_llm_again():
    completer = CountingCompleter()
    prose = GroundedProse(completer)
    first = _answers()
    prose.rewrite_question_answers(first)
    assert completer.sent == [["a1", "a2"]] and first[0]["answer_source"] == "llm"

    again = _answers()
    prose.rewrite_question_answers(again)
    assert completer.sent == [["a1", "a2"]]  # nothing new sent
    assert again[1]["answer"] == "Prose for Which DB?" and again[1]["answer_source"] == "llm"

    changed = _answers()
    changed[0]["facts"] = [{"value": "Windows Server 2022"}]  # a review changed one answer's facts
    fresh = CountingCompleter()  # a new client for the same model shares the cache
    GroundedProse(fresh).rewrite_question_answers(changed)
    assert fresh.sent == [["a1"]]  # only the changed answer goes to the LLM
    assert completer.sent == [["a1", "a2"]]


def test_a_different_model_does_not_reuse_another_models_prose():
    GroundedProse(CountingCompleter()).rewrite_question_answers(_answers())
    other = CountingCompleter()
    other.cache_key = "test:model-b"
    GroundedProse(other).rewrite_question_answers(_answers())
    assert other.sent == [["a1", "a2"]]


def test_completers_without_a_model_identity_are_never_cached():
    completer = CountingCompleter()
    completer.cache_key = None
    for _ in range(2):
        GroundedProse(completer).rewrite_question_answers(_answers())
    assert completer.sent == [["a1", "a2"], ["a1", "a2"]]
