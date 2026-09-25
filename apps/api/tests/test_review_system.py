"""Human review system: conflict lifecycle, gate coverage, validation, busy-guard,
audit fields, and — most importantly — decisions surviving a pipeline re-run."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.entities import (
    Assessment,
    Claim,
    Conflict,
    ConflictStatus,
    DependencyEdge,
    InfrastructureRecommendation,
    PipelineStatus,
    ReviewDecision,
    ReviewStatus,
    Server,
)
from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services import assessment_service as svc
from app.services.inventory import load_inventory
from app.services.pipeline import AssessmentPipeline, PipelineServices
from app.services.reconciliation import persist_extraction
from app.services.review import review_queue_status

REVIEWER = "a.reviewer@example.com"


def _extraction() -> ExtractionResult:
    """A server whose OS two sources disagree on, plus a low-confidence inferred edge."""

    def claim(attr: str, value: str, chunk: str, conf: float) -> ExtractedClaim:
        return ExtractedClaim(
            entity_type="server", entity_key="app-01", attribute=attr, value=value,
            confidence=conf, evidence_quote=value, chunk_ids=[chunk],
        )

    return ExtractionResult(
        claims=[
            claim("name", "app-01", "c1", 0.9),
            claim("os", "RHEL 9", "c1", 0.9),  # auto-winner (higher confidence)
            claim("os", "RHEL 8", "c2", 0.8),  # loser -> flagged for review
            claim("vcpus", "8", "c1", 0.9),
            claim("memory_gb", "32", "c1", 0.9),
        ],
        dependencies=[
            ExtractedDependency(
                source_type="application", source_key="billing", target_type="server",
                target_key="app-01", relationship="possible_dependency", confidence=0.3,
                evidence_quote="", chunk_ids=[],
            )
        ],
    )


@pytest.fixture()
def reviewed(db_session, assessment):
    """An assessment whose run has completed, with a conflict, an edge and a recommendation."""
    persist_extraction(db_session, assessment.id, _extraction())
    from app.services.sizing import generate_recommendations

    generate_recommendations(db_session, assessment.id, llm_explanations=False)
    assessment.status = PipelineStatus.completed
    db_session.commit()
    return assessment


def _os_claims(db) -> dict[str, Claim]:
    return {c.value: c for c in db.query(Claim).filter(Claim.attribute == "os")}


def _conflict(db) -> Conflict:
    return db.query(Conflict).one()


def _server_os(db):
    return (db.query(Server).one().attributes or {}).get("os")


# --------------------------------------------------------------------------- #
# Conflict lifecycle
# --------------------------------------------------------------------------- #
def test_accepting_a_winner_supersedes_the_loser(db_session, reviewed):
    """Previously the loser stayed in the queue, forcing a second, redundant decision."""
    svc.review_claim(db_session, reviewed, _os_claims(db_session)["RHEL 9"].id, "accept", None, None)

    claims = _os_claims(db_session)
    assert _conflict(db_session).status == ConflictStatus.resolved
    assert claims["RHEL 8"].review_status == ReviewStatus.superseded
    assert claims["RHEL 8"].needs_human_review is False
    assert review_queue_status(db_session, reviewed.id).pending_claims == 0


def test_rejecting_the_selected_value_reopens_the_conflict(db_session, reviewed):
    """Previously: conflict stayed 'resolved' with nothing selected, the OS silently
    vanished, and the assessment could be marked migration-ready."""
    winner = _os_claims(db_session)["RHEL 9"].id
    svc.review_claim(db_session, reviewed, winner, "accept", None, None)
    svc.review_claim(db_session, reviewed, winner, "reject", None, None)

    conflict = _conflict(db_session)
    assert conflict.status == ConflictStatus.open
    assert conflict.selected_claim_id is None
    loser = _os_claims(db_session)["RHEL 8"]
    assert loser.needs_human_review is True  # back in the queue
    assert loser.review_status == ReviewStatus.pending
    with pytest.raises(ValueError, match="open conflict"):
        svc.finish_review(db_session, reviewed)


def test_dismissing_a_conflict_records_the_attribute_as_unknown(db_session, reviewed):
    conflict = _conflict(db_session)
    svc.dismiss_conflict(db_session, reviewed, conflict.id, "Both are stale", reviewer=REVIEWER)

    db_session.refresh(conflict)
    assert conflict.status == ConflictStatus.dismissed
    assert all(c.review_status == ReviewStatus.dismissed for c in _os_claims(db_session).values())
    assert _server_os(db_session) is None  # deliberately unknown, not silently lost
    queue = review_queue_status(db_session, reviewed.id)
    assert queue.open_conflicts == 0 and queue.pending_claims == 0


def test_accepting_after_dismissal_resolves_the_conflict(db_session, reviewed):
    svc.dismiss_conflict(db_session, reviewed, _conflict(db_session).id, None)
    svc.review_claim(db_session, reviewed, _os_claims(db_session)["RHEL 8"].id, "accept", None, None)

    assert _conflict(db_session).status == ConflictStatus.resolved
    assert _server_os(db_session) == "RHEL 8"


# --------------------------------------------------------------------------- #
# Gate coverage: edges and recommendations
# --------------------------------------------------------------------------- #
def test_gate_covers_edges_and_recommendations(db_session, reviewed):
    svc.review_claim(db_session, reviewed, _os_claims(db_session)["RHEL 9"].id, "accept", None, None)
    queue = review_queue_status(db_session, reviewed.id)
    assert queue.pending_edges == 1  # low-confidence inferred edge
    assert queue.pending_recommendations >= 1  # sized on assumed utilization/disk
    with pytest.raises(ValueError, match="dependency edge"):
        svc.finish_review(db_session, reviewed)


def test_rejected_edge_drops_out_of_graph_and_report_inputs(db_session, reviewed):
    edge = db_session.query(DependencyEdge).one()
    svc.review_edge(db_session, reviewed, edge.id, "reject", "Not a real dependency", reviewer=REVIEWER)

    assert load_inventory(db_session, reviewed.id).edges == []
    db_session.refresh(edge)
    assert edge.review_status == ReviewStatus.rejected
    assert edge.reviewed_by == REVIEWER and edge.reviewed_at is not None


def test_full_review_then_complete(db_session, reviewed):
    svc.review_claim(db_session, reviewed, _os_claims(db_session)["RHEL 9"].id, "accept", None, None)
    svc.review_edge(db_session, reviewed, db_session.query(DependencyEdge).one().id, "accept", None)
    for rec in db_session.query(InfrastructureRecommendation).filter_by(needs_human_review=True).all():
        svc.review_recommendation(db_session, reviewed, rec.id, "accept", "Sizing OK")

    done = svc.finish_review(db_session, reviewed)
    assert done.workflow_stage.value == "follow_up"
    assert review_queue_status(db_session, reviewed.id).clear


# --------------------------------------------------------------------------- #
# Validation, batch, busy-guard, audit
# --------------------------------------------------------------------------- #
def test_override_on_a_measured_field_is_validated(db_session, reviewed):
    vcpus = db_session.query(Claim).filter(Claim.attribute == "vcpus").one()
    with pytest.raises(ValueError, match="not a number"):
        svc.review_claim(db_session, reviewed, vcpus.id, "override", "eight", None)
    with pytest.raises(ValueError, match="plausible"):
        svc.review_claim(db_session, reviewed, vcpus.id, "override", "999999", None)
    memory = db_session.query(Claim).filter(Claim.attribute == "memory_gb").one()
    updated = svc.review_claim(db_session, reviewed, memory.id, "override", "65536 MB", None)
    assert updated.override_value == "65536 MB"


def test_batch_is_all_or_nothing(db_session, reviewed):
    claims = _os_claims(db_session)
    vcpus = db_session.query(Claim).filter(Claim.attribute == "vcpus").one()
    with pytest.raises(ValueError):
        svc.review_claims(
            db_session,
            reviewed,
            [
                svc.ClaimReview(claims["RHEL 9"].id, "accept"),
                svc.ClaimReview(vcpus.id, "override", "not-a-number"),
            ],
        )
    db_session.expire_all()
    assert _conflict(db_session).status == ConflictStatus.open  # first item was NOT applied
    assert db_session.query(ReviewDecision).count() == 0


def test_batch_applies_everything_with_one_rebuild(db_session, reviewed):
    claims = _os_claims(db_session)
    out = svc.review_claims(
        db_session, reviewed,
        [svc.ClaimReview(claims["RHEL 9"].id, "accept"), svc.ClaimReview(claims["RHEL 8"].id, "reject")],
        reviewer=REVIEWER,
    )
    assert [c.review_status for c in out] == [ReviewStatus.accepted, ReviewStatus.rejected]
    assert all(c.reviewed_by == REVIEWER for c in out)


@pytest.mark.parametrize(
    "status,error",
    [
        (PipelineStatus.extracting, svc.PipelineBusy),
        (PipelineStatus.pending, svc.PipelineBusy),  # a run is queued
        (PipelineStatus.failed, ValueError),
    ],
)
def test_reviews_are_refused_unless_the_run_completed(db_session, reviewed, status, error):
    reviewed.status = status
    db_session.commit()
    with pytest.raises(error):
        svc.review_claim(db_session, reviewed, _os_claims(db_session)["RHEL 9"].id, "accept", None, None)


def test_router_maps_busy_to_409(db_session, reviewed):
    from fastapi import HTTPException

    from app.routers.assessments import review_claim
    from app.schemas.api import ClaimReviewRequest

    reviewed.status = PipelineStatus.extracting
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        review_claim(reviewed.id, _os_claims(db_session)["RHEL 9"].id, ClaimReviewRequest(action="accept"), db_session)
    assert exc.value.status_code == 409


def test_every_decision_is_recorded_for_audit(db_session, reviewed):
    svc.review_claim(
        db_session, reviewed, _os_claims(db_session)["RHEL 9"].id, "accept", None, "CMDB is current",
        reviewer=REVIEWER,
    )
    decision = db_session.query(ReviewDecision).one()
    assert (decision.target_type, decision.action, decision.reviewed_by) == ("claim", "accept", REVIEWER)
    assert decision.target_key == "server|app-01|os|rhel 9"
    events = [e["event"] for e in (reviewed.metrics or {}).get("follow_up_log", [])]
    assert "claim_accept" in events  # accepts are logged now, not only rejects/overrides


def test_deleting_an_assessment_removes_its_decisions(db_session, reviewed):
    svc.review_claim(db_session, reviewed, _os_claims(db_session)["RHEL 9"].id, "accept", None, None)
    svc.delete_assessment(db_session, reviewed)
    assert db_session.query(ReviewDecision).count() == 0


def test_lz_profile_requires_enforce_review():
    from app.config import Settings

    settings = Settings(app_profile="lz", enforce_review=False)
    with pytest.raises(RuntimeError, match="ENFORCE_REVIEW"):
        settings.validate_profile()


# --------------------------------------------------------------------------- #
# Durability: decisions survive a real pipeline re-run
# --------------------------------------------------------------------------- #
class _Embedder:
    def embed_texts(self, texts):
        return [[0.0] for _ in texts]

    def embed_query(self, text):
        return [0.0]


class _Retriever:
    def retrieve(self, *args, **kwargs):
        return []


class _SameExtraction:
    """Returns the same findings every run — exactly what a re-run after an unrelated
    change (e.g. uploading one more document) looks like for the existing facts."""

    def extract_assessment(self, db, assessment_id):
        return _extraction(), {}


def _pipeline(db_session, tmp_path) -> AssessmentPipeline:
    return AssessmentPipeline(
        PipelineServices(
            embedder=_Embedder(),
            indexes=[],
            extractor=_SameExtraction(),
            retriever=_Retriever(),
            storage_dir=str(tmp_path),
            session_factory=sessionmaker(bind=db_session.get_bind(), autoflush=False),
        )
    )


def test_review_decisions_survive_a_pipeline_rerun(db_session, assessment, tmp_path):
    pipeline = _pipeline(db_session, tmp_path)
    pipeline.run(assessment.id)
    db_session.expire_all()
    a = db_session.get(Assessment, assessment.id)
    assert a.status == PipelineStatus.completed

    # Review everything, then sign off.
    svc.review_claim(db_session, a, _os_claims(db_session)["RHEL 8"].id, "accept", None, "Arch doc is newer",
                     reviewer=REVIEWER)
    edge = db_session.query(DependencyEdge).filter_by(source_key="billing").one()
    svc.review_edge(db_session, a, edge.id, "reject", "Not real", reviewer=REVIEWER)
    for rec in db_session.query(InfrastructureRecommendation).filter_by(needs_human_review=True).all():
        svc.review_recommendation(db_session, a, rec.id, "accept", None, reviewer=REVIEWER)
    for c in db_session.query(Claim).filter_by(needs_human_review=True).all():
        svc.review_claim(db_session, a, c.id, "accept", None, None)
    for e in db_session.query(DependencyEdge).filter_by(needs_human_review=True).all():
        svc.review_edge(db_session, a, e.id, "accept", None)
    svc.finish_review(db_session, a)
    assert (a.metrics or {}).get("review_completed_at")

    # Re-run: every claim, conflict, edge and recommendation is deleted and rebuilt.
    pipeline.run(assessment.id)
    db_session.expire_all()
    a = db_session.get(Assessment, assessment.id)

    claims = _os_claims(db_session)
    assert claims["RHEL 8"].review_status == ReviewStatus.accepted  # human choice, not auto-winner
    assert claims["RHEL 8"].reviewed_by == REVIEWER
    assert claims["RHEL 9"].review_status == ReviewStatus.superseded
    assert _conflict(db_session).status == ConflictStatus.resolved
    assert _server_os(db_session) == "RHEL 8"
    rejected = db_session.query(DependencyEdge).filter_by(source_key="billing").one()
    assert rejected.review_status == ReviewStatus.rejected
    assert all(e.source_key != "billing" for e in load_inventory(db_session, a.id).edges)
    assert review_queue_status(db_session, a.id).clear, review_queue_status(db_session, a.id)
    assert (a.metrics or {})["review_decisions_reapplied"] >= 3
    # A new run means the old sign-off no longer stands — the reviewer re-confirms.
    assert "review_completed_at" not in (a.metrics or {})
    assert a.workflow_stage.value == "review"
