from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.entities import Claim, Conflict, InfrastructureRecommendation, PipelineStatus
from app.schemas.api import (
    AskQuestionRequest,
    AssessmentAnswersOut,
    AssessmentCreate,
    AssessmentListOut,
    AssessmentOut,
    AssessmentUpdate,
    BlastRadiusOut,
    ClaimOut,
    ClaimReviewRequest,
    ConflictOut,
    EntityOut,
    EvidenceOut,
    FollowUpNoteRequest,
    GraphOut,
    InfrastructureRecommendationOut,
    ReportOut,
)
from app.services import assessment_service as assessments
from app.services.assessment_questions import build_assessment_answers
from app.services.evidence import build_evidence_list, resolve_evidence, resolve_evidence_map
from app.services.graph import build_graph, get_blast_radius
from app.services.pipeline import run_pipeline
from app.services.pipeline_lock import is_in_flight
from app.services.providers import get_retriever
from app.services.report import report_to_schema

router = APIRouter(prefix="/assessments", tags=["assessments"])


def _assessment(db: Session, assessment_id: str):
    try:
        return assessments.get_assessment(db, assessment_id)
    except assessments.AssessmentNotFound as exc:
        raise HTTPException(404, "Assessment not found") from exc


def _claim_out(
    db: Session,
    claim: Claim,
    evidence_by_chunk: dict[str, dict] | None = None,
) -> ClaimOut:
    output = ClaimOut.model_validate(claim)
    if evidence_by_chunk is not None:
        resolved = build_evidence_list(
            evidence_by_chunk, claim.evidence_refs or [], claim.evidence_quote
        )
    else:
        resolved = resolve_evidence(
            db,
            claim.evidence_refs or [],
            quote=claim.evidence_quote,
        )
    output.evidence = [
        EvidenceOut.model_validate(item)
        for item in resolved
    ]
    return output


@router.get("", response_model=list[AssessmentListOut])
def list_assessments(db: Session = Depends(get_db)) -> list[AssessmentListOut]:
    from app.models.entities import Assessment

    rows = db.query(Assessment).order_by(Assessment.created_at.desc()).all()
    return [
        AssessmentListOut(
            id=a.id,
            name=a.name,
            status=a.status.value,
            workflow_stage=a.workflow_stage.value,
            created_at=a.created_at,
            updated_at=a.updated_at,
            document_count=len(a.documents),
            pipeline_started_at=a.pipeline_started_at,
            pipeline_finished_at=a.pipeline_finished_at,
            runtime_seconds=a.runtime_seconds,
        )
        for a in rows
    ]


@router.post("", response_model=AssessmentOut)
async def create_assessment(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
) -> AssessmentOut:
    if not name.strip():
        raise HTTPException(400, "name is required")
    assessment = assessments.create_assessment(db, name)
    if files:
        await assessments.add_uploads(db, assessment, files)
        _maybe_start_pipeline(background_tasks, assessment, db)
    return assessments.assessment_out(assessment)


@router.post("/json", response_model=AssessmentOut)
def create_assessment_json(
    body: AssessmentCreate,
    db: Session = Depends(get_db),
) -> AssessmentOut:
    return assessments.assessment_out(assessments.create_assessment(db, body.name))


@router.get("/{assessment_id}", response_model=AssessmentOut)
def get_assessment(assessment_id: str, db: Session = Depends(get_db)) -> AssessmentOut:
    return assessments.assessment_out(_assessment(db, assessment_id))


@router.patch("/{assessment_id}", response_model=AssessmentOut)
def patch_assessment(
    assessment_id: str, body: AssessmentUpdate, db: Session = Depends(get_db)
) -> AssessmentOut:
    try:
        updated = assessments.rename_assessment(
            db, _assessment(db, assessment_id), body.name
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return assessments.assessment_out(updated)


@router.delete("/{assessment_id}", status_code=204)
def delete_assessment(assessment_id: str, db: Session = Depends(get_db)) -> Response:
    try:
        assessments.delete_assessment(db, _assessment(db, assessment_id))
    except assessments.PipelineBusy as exc:
        raise HTTPException(409, "Pipeline is already running for this assessment") from exc
    return Response(status_code=204)


@router.post("/{assessment_id}/documents", response_model=AssessmentOut)
async def upload_documents(
    assessment_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    run: bool = True,
    db: Session = Depends(get_db),
) -> AssessmentOut:
    assessment = _assessment(db, assessment_id)
    await assessments.add_uploads(db, assessment, files)
    if run and files:
        _maybe_start_pipeline(background_tasks, assessment, db)
    return assessments.assessment_out(assessment)


@router.delete("/{assessment_id}/documents/{document_id}", response_model=AssessmentOut)
def delete_document(
    assessment_id: str,
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> AssessmentOut:
    assessment = _assessment(db, assessment_id)
    try:
        assessment, rerun = assessments.remove_document(db, assessment, document_id)
    except assessments.PipelineBusy as exc:
        raise HTTPException(409, "Pipeline is already running for this assessment") from exc
    except assessments.DocumentNotFound as exc:
        raise HTTPException(404, "Document not found") from exc
    if rerun:
        _maybe_start_pipeline(background_tasks, assessment, db)
    return assessments.assessment_out(assessment)


@router.post("/{assessment_id}/run", response_model=AssessmentOut)
def rerun_pipeline(
    assessment_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> AssessmentOut:
    assessment = _assessment(db, assessment_id)
    if not assessment.documents:
        raise HTTPException(400, "Upload documents before running the pipeline")
    if is_in_flight(assessment):
        raise HTTPException(409, "Pipeline is already running for this assessment")
    assessment.status = PipelineStatus.pending
    assessment.error_message = None
    db.commit()
    background_tasks.add_task(run_pipeline, assessment.id)
    db.refresh(assessment)
    return assessments.assessment_out(assessment)


@router.get("/{assessment_id}/claims", response_model=list[ClaimOut])
def list_claims(
    assessment_id: str,
    review_only: bool = False,
    db: Session = Depends(get_db),
) -> list[ClaimOut]:
    _assessment(db, assessment_id)
    q = db.query(Claim).filter(Claim.assessment_id == assessment_id)
    if review_only:
        q = q.filter(Claim.needs_human_review.is_(True))
    rows = q.order_by(Claim.confidence.asc()).all()
    evidence_by_chunk = resolve_evidence_map(
        db,
        [ref for row in rows for ref in (row.evidence_refs or [])],
    )
    return [_claim_out(db, row, evidence_by_chunk) for row in rows]


@router.post("/{assessment_id}/claims/{claim_id}/review", response_model=ClaimOut)
def review_claim(
    assessment_id: str,
    claim_id: str,
    body: ClaimReviewRequest,
    db: Session = Depends(get_db),
) -> ClaimOut:
    assessment = _assessment(db, assessment_id)
    try:
        updated = assessments.review_claim(
            db,
            assessment,
            claim_id,
            body.action,
            body.override_value,
            body.notes,
            retriever=_questions_retriever(),
        )
    except assessments.AssessmentNotFound as exc:
        raise HTTPException(404, "Claim not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _claim_out(db, updated)


@router.get("/{assessment_id}/entities", response_model=list[EntityOut])
def list_entities(assessment_id: str, db: Session = Depends(get_db)) -> list[EntityOut]:
    _assessment(db, assessment_id)
    return assessments.list_entities(db, assessment_id)


@router.get("/{assessment_id}/graph", response_model=GraphOut)
def get_graph(assessment_id: str, db: Session = Depends(get_db)) -> GraphOut:
    _assessment(db, assessment_id)
    return build_graph(db, assessment_id)


@router.get("/{assessment_id}/graph/blast-radius", response_model=BlastRadiusOut)
def graph_blast_radius(
    assessment_id: str,
    node: str,
    depth: int = 2,
    db: Session = Depends(get_db),
) -> BlastRadiusOut:
    _assessment(db, assessment_id)
    if ":" not in node:
        raise HTTPException(400, "node must look like application:billing-service")
    return get_blast_radius(db, assessment_id, node, depth)


@router.get("/{assessment_id}/conflicts", response_model=list[ConflictOut])
def list_conflicts(assessment_id: str, db: Session = Depends(get_db)) -> list[ConflictOut]:
    _assessment(db, assessment_id)
    rows = db.query(Conflict).filter(Conflict.assessment_id == assessment_id).all()
    return [ConflictOut.model_validate(r) for r in rows]


@router.get("/{assessment_id}/assessment-questions", response_model=AssessmentAnswersOut)
def get_assessment_questions(
    assessment_id: str, db: Session = Depends(get_db)
) -> AssessmentAnswersOut:
    _assessment(db, assessment_id)
    return AssessmentAnswersOut.model_validate(
        build_assessment_answers(
            db, assessment_id, retriever=_questions_retriever()
        )
    )


@router.post(
    "/{assessment_id}/assessment-questions/ask",
    response_model=AssessmentAnswersOut,
)
def ask_assessment_question(
    assessment_id: str,
    body: AskQuestionRequest,
    db: Session = Depends(get_db),
) -> AssessmentAnswersOut:
    assessment = _assessment(db, assessment_id)
    try:
        result = assessments.ask_engagement_question(
            db, assessment, body.question, _questions_retriever()
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return AssessmentAnswersOut.model_validate(result)


@router.get(
    "/{assessment_id}/recommendations",
    response_model=list[InfrastructureRecommendationOut],
)
def list_recommendations(
    assessment_id: str, db: Session = Depends(get_db)
) -> list[InfrastructureRecommendationOut]:
    _assessment(db, assessment_id)
    rows = (
        db.query(InfrastructureRecommendation)
        .filter(InfrastructureRecommendation.assessment_id == assessment_id)
        .order_by(InfrastructureRecommendation.server_key)
        .all()
    )
    return [InfrastructureRecommendationOut.model_validate(row) for row in rows]


@router.get("/{assessment_id}/report", response_model=ReportOut)
def get_report(assessment_id: str, db: Session = Depends(get_db)) -> ReportOut:
    _assessment(db, assessment_id)
    try:
        return report_to_schema(db, assessment_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{assessment_id}/complete-review", response_model=AssessmentOut)
def complete_assessment_review(
    assessment_id: str,
    db: Session = Depends(get_db),
) -> AssessmentOut:
    assessment = _assessment(db, assessment_id)
    try:
        updated = assessments.finish_review(db, assessment)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return assessments.assessment_out(updated)


@router.post("/{assessment_id}/follow-up", response_model=AssessmentOut)
def add_follow_up_note(
    assessment_id: str,
    body: FollowUpNoteRequest,
    db: Session = Depends(get_db),
) -> AssessmentOut:
    assessment = _assessment(db, assessment_id)
    return assessments.assessment_out(
        assessments.add_follow_up_note(db, assessment, body.note, body.tags)
    )


def _questions_retriever():
    try:
        return get_retriever()
    except Exception:
        return None


def _maybe_start_pipeline(background_tasks: BackgroundTasks, assessment, db) -> None:
    if is_in_flight(assessment):
        return
    assessment.status = PipelineStatus.pending
    assessment.error_message = None
    db.commit()
    background_tasks.add_task(run_pipeline, assessment.id)
