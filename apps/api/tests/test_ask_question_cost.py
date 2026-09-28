"""Asking a question must only answer that question.

Reported: with a real LLM, Ask took about two minutes. Each Ask regenerated the whole
report with LLM prose (every answer rewritten, plus the readiness summary; a timed-out
batch is retried) and then rebuilt every answer again for the Questions tab.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.models.entities import AssessmentOutput, Claim, PipelineStatus, ReviewDecision
from app.services import assessment_questions as aq
from app.services import assessment_service
from app.services.llm_reasoning import GroundedProse


class CountingCompleter:
    """Rewrites whatever it is sent and records which answers were sent."""

    enabled = True
    source = "test"

    def __init__(self) -> None:
        self.sent: list[list[str]] = []

    def complete(self, system, user, *, temperature=0.1, json_mode=False, response_schema=None):
        items = json.loads(user)
        self.sent.append([i["id"] for i in items])
        return json.dumps({"answers": [{"id": i["id"], "text": f"LLM: {i['question']}"} for i in items]})


@pytest.fixture
def completed(db_session, assessment, monkeypatch):
    completer = CountingCompleter()
    monkeypatch.setattr("app.services.llm_reasoning.get_chat_completer", lambda: completer)
    assessment.status = PipelineStatus.completed
    assessment.pipeline_started_at = datetime(2026, 9, 1, 12, 0, 0)
    assessment.pipeline_finished_at = datetime(2026, 9, 1, 12, 5, 0)
    for key, attr, value in [("app-01", "name", "app-01"), ("app-01", "os", "RHEL 9"), ("billing", "rto", "4 hours")]:
        db_session.add(
            Claim(
                assessment_id=assessment.id,
                entity_type="server",
                entity_key=key,
                attribute=attr,
                value=value,
                confidence=0.9,
                evidence_refs=[],
                is_selected=True,
                needs_human_review=False,
                unsupported=False,
            )
        )
    db_session.add(
        AssessmentOutput(
            assessment_id=assessment.id,
            readiness_summary="LLM summary",
            report_json={"readiness_summary": "LLM summary"},
        )
    )
    db_session.commit()
    aq.cached_assessment_answers(db_session, assessment)  # answers stored at completion
    completer.sent.clear()
    return completer


def test_ask_answers_only_the_new_question(db_session, assessment, completed, monkeypatch):
    monkeypatch.setattr(
        assessment_service, "generate_report", lambda *a, **k: pytest.fail("Ask must not regenerate the whole report")
    )
    before = json.loads(json.dumps(assessment.answers_cache["answers"]))
    result = assessment_service.ask_engagement_question(db_session, assessment, "Which operating systems are used?")

    assert len(completed.sent) == 1 and len(completed.sent[0]) == 1  # one small rewrite call
    asked = result["answers"][-1]
    assert asked["origin"] == "ad_hoc" and asked["answer"].startswith("LLM: Which operating systems")
    assert result["answers"][:-1] == before  # every existing answer returned exactly as it was

    # The Questions tab and the report see the same answers without another rebuild.
    rebuilt = []
    real = aq.build_assessment_answers
    monkeypatch.setattr(aq, "build_assessment_answers", lambda *a, **k: rebuilt.append(1) or real(*a, **k))
    assert aq.cached_assessment_answers(db_session, assessment) == result and not rebuilt
    report = db_session.query(AssessmentOutput).filter_by(assessment_id=assessment.id).one()
    assert report.report_json["assessment_questions"] == result
    assert report.report_json["readiness_summary"] == "LLM summary"  # rest of the report kept


def test_asking_the_same_question_again_costs_nothing(db_session, assessment, completed):
    assessment_service.ask_engagement_question(db_session, assessment, "Which operating systems are used?")
    calls = len(completed.sent)
    again = assessment_service.ask_engagement_question(db_session, assessment, "Which operating systems are used?")
    assert len(completed.sent) == calls
    assert sum(a["origin"] == "ad_hoc" for a in again["answers"]) == 1


def test_stale_stored_answers_fall_back_to_a_full_rebuild(db_session, assessment, completed):
    # A review decision the stored answers don't reflect yet: adding just one would be wrong.
    db_session.add(ReviewDecision(assessment_id=assessment.id, target_type="claim", target_key="k", action="accept"))
    db_session.commit()
    result = assessment_service.ask_engagement_question(db_session, assessment, "What is the RTO for billing?")
    assert sum(len(batch) for batch in completed.sent) > 1  # everything re-rewritten
    assert any(a["origin"] == "ad_hoc" for a in result["answers"])


def test_ask_during_a_run_stays_cheap(db_session, assessment, completed):
    assessment.status = PipelineStatus.extracting
    db_session.commit()
    assessment_service.ask_engagement_question(db_session, assessment, "What is the RTO for billing?")
    assert completed.sent == []  # drafts only while the claims are being rebuilt


def test_prose_for_the_new_answer_uses_the_configured_writer(db_session, assessment, completed):
    row = aq.persist_ad_hoc_question(db_session, assessment.id, "What is the RTO for billing?")
    writer = CountingCompleter()
    result = aq.answer_added_question(db_session, assessment, row, prose=GroundedProse(writer))
    assert writer.sent == [[row.id]] and result["answers"][-1]["id"] == row.id
