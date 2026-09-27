"""The LLM-written answers must be ready the moment a run shows Completed.

Reported: after an assessment completed, the Questions tab showed the draft (template)
answers and the LLM prose only appeared later. The run's report step had already written
the final answers, but they weren't stored as the Questions tab's answers, so the first
load after completion rebuilt them (retrieval + LLM rewrite) while the drafts stayed up.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import sessionmaker

from app.models.entities import Assessment, PipelineStatus
from app.schemas.api import ExtractedClaim, ExtractionResult
from app.services import assessment_questions as aq
from app.services.pipeline import AssessmentPipeline, PipelineServices


class _ProseCompleter:
    """Rewrites every answer; no cache_key, so nothing is served from the rewrite cache."""

    enabled = True
    source = "test"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system, user, *, temperature=0.1, json_mode=False, response_schema=None):
        self.calls += 1
        if not json_mode:
            return "Prose summary."
        items = json.loads(user)
        return json.dumps({"answers": [{"id": i["id"], "text": f"LLM: {i['question']}"} for i in items]})


class _Extractor:
    def extract_assessment(self, db, assessment_id):
        claim = ExtractedClaim(
            entity_type="server",
            entity_key="app-01",
            attribute="name",
            value="app-01",
            confidence=0.9,
            evidence_quote="app-01",
            chunk_ids=[],
        )
        return ExtractionResult(claims=[claim]), {}


class _Noop:
    def embed_texts(self, texts):
        return [[0.0] for _ in texts]

    def embed_query(self, text):
        return [0.0]

    def retrieve(self, *a, **k):
        return []


def test_first_load_after_completion_serves_the_final_llm_answers(db_session, assessment, tmp_path, monkeypatch):
    completer = _ProseCompleter()
    monkeypatch.setattr("app.services.llm_reasoning.get_chat_completer", lambda: completer)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    AssessmentPipeline(
        PipelineServices(
            embedder=_Noop(),
            indexes=[],
            extractor=_Extractor(),
            retriever=_Noop(),
            storage_dir=str(tmp_path),
            session_factory=factory,
        )
    ).run(assessment.id)

    db_session.expire_all()
    done = db_session.get(Assessment, assessment.id)
    assert done.status == PipelineStatus.completed
    assert done.answers_cache and done.answers_fingerprint

    calls_after_run = completer.calls
    monkeypatch.setattr(
        aq, "build_assessment_answers", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rebuilt"))
    )
    result = aq.cached_assessment_answers(db_session, done)

    inventory = next(a for a in result["answers"] if a["id"] == "estate_inventory")
    assert inventory["answer_source"] == "llm" and inventory["answer"].startswith("LLM: ")
    assert completer.calls == calls_after_run  # no second LLM round after completion


def test_a_review_decision_after_the_run_still_refreshes_the_answers(db_session, assessment, tmp_path, monkeypatch):
    from app.models.entities import ReviewDecision

    monkeypatch.setattr("app.services.llm_reasoning.get_chat_completer", lambda: _ProseCompleter())
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    AssessmentPipeline(
        PipelineServices(
            embedder=_Noop(),
            indexes=[],
            extractor=_Extractor(),
            retriever=_Noop(),
            storage_dir=str(tmp_path),
            session_factory=factory,
        )
    ).run(assessment.id)
    db_session.expire_all()
    done = db_session.get(Assessment, assessment.id)
    seeded = done.answers_fingerprint

    db_session.add(ReviewDecision(assessment_id=done.id, target_type="claim", target_key="k", action="accept"))
    db_session.commit()
    rebuilt = []
    real = aq.build_assessment_answers
    monkeypatch.setattr(aq, "build_assessment_answers", lambda *a, **k: rebuilt.append(1) or real(*a, **k))
    aq.cached_assessment_answers(db_session, done)
    assert rebuilt and done.answers_fingerprint != seeded
