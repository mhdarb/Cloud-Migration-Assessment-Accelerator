from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import (
    Assessment,
    Claim,
    Document,
    EngagementQuestion,
    PipelineStatus,
    WorkflowStage,
)
from app.schemas.api import AssessmentOut, DocumentOut, EntityOut
from app.services.assessment_questions import build_assessment_answers, persist_ad_hoc_question
from app.services.inventory import load_inventory
from app.services.ports import Retriever
from app.services.reconciliation import apply_claim_review, rematerialize_entities
from app.services.report import generate_report
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


def review_claim(
    db: Session,
    assessment: Assessment,
    claim_id: str,
    action: str,
    override_value: str | None,
    notes: str | None,
    retriever: Retriever | None = None,
) -> Claim:
    claim = (
        db.query(Claim)
        .filter(Claim.id == claim_id, Claim.assessment_id == assessment.id)
        .one_or_none()
    )
    if not claim:
        raise AssessmentNotFound(claim_id)
    updated = apply_claim_review(db, claim, action, override_value, notes)
    capture_review_learning(assessment, updated, action.lower())
    db.commit()
    rematerialize_entities(db, assessment.id)
    generate_recommendations(db, assessment.id)
    generate_report(db, assessment.id, retriever=retriever)
    return updated


def ask_engagement_question(
    db: Session,
    assessment: Assessment,
    question: str,
    retriever: Retriever | None = None,
) -> dict:
    persist_ad_hoc_question(db, assessment.id, question)
    generate_report(db, assessment.id, retriever=retriever)
    return build_assessment_answers(db, assessment.id, retriever=retriever)


def finish_review(db: Session, assessment: Assessment) -> Assessment:
    if assessment.status != PipelineStatus.completed:
        raise ValueError("Pipeline must be completed before finishing review")
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
