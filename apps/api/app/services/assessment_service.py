from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import (
    Assessment,
    Claim,
    Conflict,
    DependencyEdge,
    Document,
    EngagementQuestion,
    InfrastructureRecommendation,
    PipelineStatus,
    ReviewDecision,
    WorkflowStage,
)
from app.schemas.api import AssessmentOut, DocumentOut, EntityOut
from app.services.assessment_questions import build_assessment_answers, persist_ad_hoc_question
from app.services.inventory import load_inventory
from app.services.llm_clients import DisabledChatCompleter
from app.services.llm_reasoning import GroundedProse
from app.services.ports import Retriever
from app.services.questionnaires import delete_assessment_questionnaires
from app.services.reconciliation import rematerialize_entities
from app.services.report import generate_report
from app.services.review import (
    apply_claim_decision,
    apply_conflict_dismissal,
    apply_item_decision,
    claim_key,
    conflict_key,
    edge_key,
    reapply_recommendation_decisions,
    recommendation_key,
    record_decision,
    review_queue_status,
    validate_claim_action,
    validate_decision_action,
)
from app.services.sizing import generate_recommendations
from app.services.storage import save_upload
from app.services.workflow import append_follow_up, capture_review_learning, complete_review


class AssessmentNotFound(Exception):
    pass


class PipelineBusy(Exception):
    pass


class DocumentNotFound(Exception):
    pass


def get_assessment(db: Session, assessment_id: str) -> Assessment:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).one_or_none()
    if not assessment:
        raise AssessmentNotFound(assessment_id)
    return assessment


def assessment_out(assessment: Assessment) -> AssessmentOut:
    return AssessmentOut(
        id=assessment.id,
        name=assessment.name,
        status=assessment.status.value,
        workflow_stage=assessment.workflow_stage.value,
        error_message=assessment.error_message,
        metrics=assessment.metrics,
        created_at=assessment.created_at,
        updated_at=assessment.updated_at,
        pipeline_started_at=assessment.pipeline_started_at,
        pipeline_finished_at=assessment.pipeline_finished_at,
        runtime_seconds=assessment.runtime_seconds,
        documents=[
            DocumentOut(
                id=d.id,
                filename=d.filename,
                content_type=d.content_type,
                doc_type=d.doc_type.value,
                precedence=d.precedence,
                page_count=d.page_count,
                created_at=d.created_at,
            )
            for d in assessment.documents
        ],
    )


def rename_assessment(db: Session, assessment: Assessment, name: str) -> Assessment:
    stripped = name.strip()
    if not stripped:
        raise ValueError("name is required")
    assessment.name = stripped[:255]
    db.commit()
    db.refresh(assessment)
    return assessment


def delete_assessment(db: Session, assessment: Assessment) -> None:
    from app.services.ingest import clear_derived
    from app.services.pipeline_lock import is_in_flight
    from app.services.providers import get_vector_indexes

    if is_in_flight(assessment):
        raise PipelineBusy()
    assessment_id = assessment.id
    storage_dir = get_settings().storage_dir
    clear_derived(db, assessment_id, get_vector_indexes(), storage_dir)
    db.query(EngagementQuestion).filter(
        EngagementQuestion.assessment_id == assessment_id
    ).delete()
    # Review decisions survive re-runs by design, so they must be removed explicitly here.
    db.query(ReviewDecision).filter(ReviewDecision.assessment_id == assessment_id).delete()
    delete_assessment_questionnaires(db, assessment_id)
    db.query(Document).filter(Document.assessment_id == assessment_id).delete()
    db.delete(assessment)
    db.commit()
    shutil.rmtree(Path(storage_dir) / assessment_id, ignore_errors=True)


def remove_document(
    db: Session, assessment: Assessment, document_id: str
) -> tuple[Assessment, bool]:
    from app.services.ingest import clear_derived
    from app.services.pipeline_lock import is_in_flight
    from app.services.providers import get_vector_indexes

    if is_in_flight(assessment):
        raise PipelineBusy()
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.assessment_id == assessment.id)
        .one_or_none()
    )
    if not document:
        raise DocumentNotFound(document_id)
    storage_path = Path(document.storage_path)
    db.delete(document)
    db.flush()
    remaining = (
        db.query(Document).filter(Document.assessment_id == assessment.id).count()
    )
    clear_derived(db, assessment.id, get_vector_indexes(), get_settings().storage_dir)
    if remaining == 0:
        assessment.status = PipelineStatus.pending
        assessment.error_message = None
    db.commit()
    if storage_path.exists():
        storage_path.unlink(missing_ok=True)
    db.refresh(assessment)
    return assessment, remaining > 0


def create_assessment(db: Session, name: str) -> Assessment:
    assessment = Assessment(
        name=name.strip(),
        status=PipelineStatus.pending,
        workflow_stage=WorkflowStage.research,
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment


async def add_uploads(db: Session, assessment: Assessment, files: list[UploadFile]) -> None:
    for upload in files:
        path, filename = await save_upload(assessment.id, upload)
        db.add(
            Document(
                assessment_id=assessment.id,
                filename=filename,
                content_type=upload.content_type or "application/octet-stream",
                storage_path=path,
            )
        )
    db.commit()
    db.refresh(assessment)


@dataclass(frozen=True)
class ClaimReview:
    claim_id: str
    action: str
    override_value: str | None = None
    notes: str | None = None


def _ensure_reviewable(assessment: Assessment) -> None:
    """Reviews only apply to a finished run. While a run is queued or in progress,
    `clear_derived` is about to delete the very rows being reviewed."""
    if assessment.status == PipelineStatus.completed:
        return
    if assessment.status == PipelineStatus.failed:
        raise ValueError("The last pipeline run failed; re-run it before reviewing")
    raise PipelineBusy()


def _refresh_after_review(
    db: Session,
    assessment_id: str,
    retriever: Retriever | None,
    *,
    rebuild_entities: bool = True,
    polish: bool = False,
) -> None:
    """Bring derived views in line with the latest decisions.

    Per-click rebuilds (`polish=False`) are deterministic: no LLM-written sizing
    explanations or report prose, so reviewing 50 items doesn't cost 50 rounds of LLM
    calls. `finish_review` does one polished rebuild when the reviewer signs off.
    """
    if rebuild_entities:
        rematerialize_entities(db, assessment_id)
        generate_recommendations(db, assessment_id, llm_explanations=polish)
        reapply_recommendation_decisions(db, assessment_id)
        db.commit()
    prose = None if polish else GroundedProse(DisabledChatCompleter())
    generate_report(db, assessment_id, retriever=retriever, prose=prose)


def review_claims(
    db: Session,
    assessment: Assessment,
    reviews: list[ClaimReview],
    *,
    reviewer: str | None = None,
    retriever: Retriever | None = None,
) -> list[Claim]:
    """Apply one or many claim decisions, all-or-nothing, with a single rebuild."""
    _ensure_reviewable(assessment)
    if not reviews:
        raise ValueError("No reviews provided")
    ids = [r.claim_id for r in reviews]
    claims = {
        c.id: c
        for c in db.query(Claim)
        .filter(Claim.assessment_id == assessment.id, Claim.id.in_(ids))
        .all()
    }
    missing = [i for i in ids if i not in claims]
    if missing:
        raise AssessmentNotFound(missing[0])
    # Validate every item before touching any of them.
    actions = [
        validate_claim_action(claims[r.claim_id], r.action, r.override_value) for r in reviews
    ]
    now = datetime.utcnow()
    for review, action in zip(reviews, actions, strict=True):
        claim = claims[review.claim_id]
        override = review.override_value.strip() if action == "override" and review.override_value else None
        apply_claim_decision(
            db, claim, action, override_value=override, notes=review.notes, reviewer=reviewer, at=now
        )
        record_decision(
            db, assessment.id, "claim", claim_key(claim), action,
            override_value=override, notes=review.notes, reviewer=reviewer,
        )
        capture_review_learning(assessment, claim, action)
        db.flush()
    db.commit()
    _refresh_after_review(db, assessment.id, retriever)
    return [claims[i] for i in ids]


def review_claim(
    db: Session,
    assessment: Assessment,
    claim_id: str,
    action: str,
    override_value: str | None,
    notes: str | None,
    retriever: Retriever | None = None,
    reviewer: str | None = None,
) -> Claim:
    return review_claims(
        db,
        assessment,
        [ClaimReview(claim_id, action, override_value, notes)],
        reviewer=reviewer,
        retriever=retriever,
    )[0]


def dismiss_conflict(
    db: Session,
    assessment: Assessment,
    conflict_id: str,
    notes: str | None,
    *,
    reviewer: str | None = None,
    retriever: Retriever | None = None,
) -> Conflict:
    """'None of these values is right': the attribute is recorded as unknown."""
    _ensure_reviewable(assessment)
    conflict = (
        db.query(Conflict)
        .filter(Conflict.id == conflict_id, Conflict.assessment_id == assessment.id)
        .one_or_none()
    )
    if not conflict:
        raise AssessmentNotFound(conflict_id)
    apply_conflict_dismissal(db, conflict, notes=notes, reviewer=reviewer)
    record_decision(
        db, assessment.id, "conflict", conflict_key(conflict), "dismiss", notes=notes, reviewer=reviewer
    )
    append_follow_up(
        assessment,
        event="conflict_dismissed",
        detail={
            "conflict_id": conflict.id,
            "entity": f"{conflict.entity_type}:{conflict.entity_key}.{conflict.attribute}",
            "notes": notes,
            "reviewed_by": reviewer,
        },
    )
    db.commit()
    _refresh_after_review(db, assessment.id, retriever)
    return conflict


def review_edge(
    db: Session,
    assessment: Assessment,
    edge_id: str,
    action: str,
    notes: str | None,
    *,
    reviewer: str | None = None,
    retriever: Retriever | None = None,
) -> DependencyEdge:
    _ensure_reviewable(assessment)
    action = validate_decision_action(action)
    edge = (
        db.query(DependencyEdge)
        .filter(DependencyEdge.id == edge_id, DependencyEdge.assessment_id == assessment.id)
        .one_or_none()
    )
    if not edge:
        raise AssessmentNotFound(edge_id)
    apply_item_decision(edge, action, notes=notes, reviewer=reviewer)
    record_decision(db, assessment.id, "edge", edge_key(edge), action, notes=notes, reviewer=reviewer)
    append_follow_up(
        assessment,
        event=f"edge_{action}",
        detail={"edge_id": edge.id, "edge": edge_key(edge), "notes": notes, "reviewed_by": reviewer},
    )
    db.commit()
    # An edge doesn't change entities or sizing — only the report needs rebuilding.
    _refresh_after_review(db, assessment.id, retriever, rebuild_entities=False)
    return edge


def review_recommendation(
    db: Session,
    assessment: Assessment,
    recommendation_id: str,
    action: str,
    notes: str | None,
    *,
    reviewer: str | None = None,
    retriever: Retriever | None = None,
) -> InfrastructureRecommendation:
    _ensure_reviewable(assessment)
    action = validate_decision_action(action)
    rec = (
        db.query(InfrastructureRecommendation)
        .filter(
            InfrastructureRecommendation.id == recommendation_id,
            InfrastructureRecommendation.assessment_id == assessment.id,
        )
        .one_or_none()
    )
    if not rec:
        raise AssessmentNotFound(recommendation_id)
    apply_item_decision(rec, action, notes=notes, reviewer=reviewer)
    record_decision(
        db, assessment.id, "recommendation", recommendation_key(rec), action, notes=notes, reviewer=reviewer
    )
    append_follow_up(
        assessment,
        event=f"recommendation_{action}",
        detail={
            "recommendation_id": rec.id,
            "server_key": rec.server_key,
            "sku": rec.recommended_sku,
            "notes": notes,
            "reviewed_by": reviewer,
        },
    )
    db.commit()
    _refresh_after_review(db, assessment.id, retriever, rebuild_entities=False)
    return rec


def ask_engagement_question(
    db: Session,
    assessment: Assessment,
    question: str,
    retriever: Retriever | None = None,
) -> dict:
    persist_ad_hoc_question(db, assessment.id, question)
    generate_report(db, assessment.id, retriever=retriever)
    return build_assessment_answers(db, assessment.id, retriever=retriever)


def finish_review(
    db: Session, assessment: Assessment, retriever: Retriever | None = None
) -> Assessment:
    if assessment.status != PipelineStatus.completed:
        raise ValueError("Pipeline must be completed before finishing review")
    # Cheap gate check first, so an incomplete review doesn't pay for the polished rebuild.
    if get_settings().enforce_review:
        queue = review_queue_status(db, assessment.id)
        if not queue.clear:
            raise ValueError(
                f"Review incomplete: {queue.describe()}. Resolve them before marking ready."
            )
    # One polished rebuild (LLM sizing explanations + report prose) on sign-off, then
    # re-check the gate against the rebuilt rows.
    _refresh_after_review(db, assessment.id, retriever, polish=True)
    complete_review(db, assessment)
    append_follow_up(
        assessment,
        event="review_completed",
        detail={"note": "Assessment marked migration-ready after review"},
    )
    db.commit()
    db.refresh(assessment)
    return assessment


def add_follow_up_note(
    db: Session, assessment: Assessment, note: str, tags: list[str] | None
) -> Assessment:
    append_follow_up(
        assessment,
        event="manual_note",
        detail={"note": note, "tags": tags or []},
    )
    if assessment.workflow_stage != WorkflowStage.follow_up:
        assessment.workflow_stage = WorkflowStage.follow_up
    db.commit()
    db.refresh(assessment)
    return assessment


def list_entities(db: Session, assessment_id: str) -> list[EntityOut]:
    snap = load_inventory(db, assessment_id)
    out: list[EntityOut] = []
    for entity_type, rows in (
        ("application", snap.applications),
        ("server", snap.servers),
        ("database", snap.databases),
        ("interface", snap.interfaces),
    ):
        for row in rows:
            out.append(
                EntityOut(
                    id=row.id,
                    name=row.name,
                    normalized_key=row.normalized_key,
                    attributes=row.attributes,
                    confidence=row.confidence,
                    entity_type=entity_type,
                )
            )
    return out
