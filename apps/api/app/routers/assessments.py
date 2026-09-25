from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.entities import (
    Claim,
    Conflict,
    DependencyEdge,
    InfrastructureRecommendation,
    PipelineStatus,
)
from app.schemas.api import (
    AskQuestionRequest,
    AssessmentAnswersOut,
    AssessmentCreate,
    AssessmentListOut,
    AssessmentOut,
    AssessmentUpdate,
    BatchClaimReviewRequest,
    BlastRadiusOut,
    ClaimOut,
    ClaimReviewRequest,
    ConflictOut,
    DependencyEdgeOut,
    DismissConflictRequest,
    EntityOut,
    EvidenceOut,
    FollowUpNoteRequest,
    GraphOut,
    InfrastructureRecommendationOut,
    QuestionnaireAnswersOut,
    QuestionnaireOut,
    ReportOut,
    ReviewDecisionRequest,
    ReviewStatusOut,
)
from app.services import assessment_service as assessments
from app.services import questionnaires
from app.services.assessment_questions import build_assessment_answers
from app.services.evidence import build_evidence_list, resolve_evidence, resolve_evidence_map
from app.services.graph import build_graph, get_blast_radius
from app.services.pipeline import run_pipeline
from app.services.pipeline_lock import is_in_flight
from app.services.providers import get_retriever
from app.services.report import report_to_schema
from app.services.review import review_queue_status

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


_BUSY_DETAIL = (
    "A pipeline run is queued or in progress for this assessment; review decisions can't "
    "be applied until it finishes (the run rebuilds the items being reviewed)."
)


@contextmanager
def _review_errors(not_found: str) -> Iterator[None]:
    """Map review-service exceptions to HTTP errors consistently."""
    try:
        yield
    except assessments.PipelineBusy as exc:
        raise HTTPException(409, _BUSY_DETAIL) from exc
    except assessments.AssessmentNotFound as exc:
        raise HTTPException(404, not_found) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{assessment_id}/claims/{claim_id}/review", response_model=ClaimOut)
def review_claim(
    assessment_id: str,
    claim_id: str,
    body: ClaimReviewRequest,
    db: Session = Depends(get_db),
) -> ClaimOut:
    assessment = _assessment(db, assessment_id)
    with _review_errors("Claim not found"):
        updated = assessments.review_claim(
            db,
            assessment,
            claim_id,
            body.action,
            body.override_value,
            body.notes,
            retriever=_questions_retriever(),
            reviewer=body.reviewer,
        )
    return _claim_out(db, updated)


@router.post("/{assessment_id}/claims/review-batch", response_model=list[ClaimOut])
def review_claims_batch(
    assessment_id: str,
    body: BatchClaimReviewRequest,
    db: Session = Depends(get_db),
) -> list[ClaimOut]:
    """Apply many claim decisions at once — validated all-or-nothing, one rebuild."""
    assessment = _assessment(db, assessment_id)
    with _review_errors("Claim not found"):
        updated = assessments.review_claims(
            db,
            assessment,
            [
                assessments.ClaimReview(i.claim_id, i.action, i.override_value, i.notes)
                for i in body.reviews
            ],
            reviewer=body.reviewer,
            retriever=_questions_retriever(),
        )
    return [_claim_out(db, c) for c in updated]


@router.post("/{assessment_id}/conflicts/{conflict_id}/dismiss", response_model=ConflictOut)
def dismiss_conflict(
    assessment_id: str,
    conflict_id: str,
    body: DismissConflictRequest,
    db: Session = Depends(get_db),
) -> ConflictOut:
    """None of the candidate values is right: record the attribute as unknown."""
    assessment = _assessment(db, assessment_id)
    with _review_errors("Conflict not found"):
        conflict = assessments.dismiss_conflict(
            db, assessment, conflict_id, body.notes,
            reviewer=body.reviewer, retriever=_questions_retriever(),
        )
    return ConflictOut.model_validate(conflict)


@router.get("/{assessment_id}/edges", response_model=list[DependencyEdgeOut])
def list_edges(
    assessment_id: str,
    review_only: bool = False,
    db: Session = Depends(get_db),
) -> list[DependencyEdgeOut]:
    _assessment(db, assessment_id)
    q = db.query(DependencyEdge).filter(DependencyEdge.assessment_id == assessment_id)
    if review_only:
        q = q.filter(DependencyEdge.needs_human_review.is_(True))
    return [DependencyEdgeOut.model_validate(e) for e in q.order_by(DependencyEdge.confidence.asc()).all()]


@router.post("/{assessment_id}/edges/{edge_id}/review", response_model=DependencyEdgeOut)
def review_edge(
    assessment_id: str,
    edge_id: str,
    body: ReviewDecisionRequest,
    db: Session = Depends(get_db),
) -> DependencyEdgeOut:
    """Accept or reject a dependency edge (rejected edges drop out of graph and report)."""
    assessment = _assessment(db, assessment_id)
    with _review_errors("Dependency edge not found"):
        edge = assessments.review_edge(
            db, assessment, edge_id, body.action, body.notes,
            reviewer=body.reviewer, retriever=_questions_retriever(),
        )
    return DependencyEdgeOut.model_validate(edge)


@router.post(
    "/{assessment_id}/recommendations/{recommendation_id}/review",
    response_model=InfrastructureRecommendationOut,
)
def review_recommendation(
    assessment_id: str,
    recommendation_id: str,
    body: ReviewDecisionRequest,
    db: Session = Depends(get_db),
) -> InfrastructureRecommendationOut:
    """Sign off (accept) or reject a flagged sizing recommendation."""
    assessment = _assessment(db, assessment_id)
    with _review_errors("Recommendation not found"):
        rec = assessments.review_recommendation(
            db, assessment, recommendation_id, body.action, body.notes,
            reviewer=body.reviewer, retriever=_questions_retriever(),
        )
    return InfrastructureRecommendationOut.model_validate(rec)


@router.get("/{assessment_id}/review-status", response_model=ReviewStatusOut)
def review_status(assessment_id: str, db: Session = Depends(get_db)) -> ReviewStatusOut:
    """Everything that still blocks 'Complete review'."""
    _assessment(db, assessment_id)
    queue = review_queue_status(db, assessment_id)
    return ReviewStatusOut(
        pending_claims=queue.pending_claims,
        open_conflicts=queue.open_conflicts,
        pending_edges=queue.pending_edges,
        pending_recommendations=queue.pending_recommendations,
        clear=queue.clear,
        summary=queue.describe(),
        enforce_review=get_settings().enforce_review,
    )


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


_QUESTIONNAIRE_BUSY = (
    "A pipeline run is queued or in progress; questionnaire answers will be available "
    "once it finishes."
)


@contextmanager
def _questionnaire_errors() -> Iterator[None]:
    try:
        yield
    except questionnaires.QuestionnaireNotFound as exc:
        raise HTTPException(404, "Questionnaire not found") from exc
    except questionnaires.QuestionnaireBusy as exc:
        raise HTTPException(409, _QUESTIONNAIRE_BUSY) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{assessment_id}/questionnaires", response_model=QuestionnaireOut)
async def upload_questionnaire(
    assessment_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> QuestionnaireOut:
    assessment = _assessment(db, assessment_id)
    with _questionnaire_errors():
        created = await questionnaires.import_questionnaire(db, assessment, file)
    return QuestionnaireOut(**questionnaires.questionnaire_summary(db, created))


@router.get("/{assessment_id}/questionnaires", response_model=list[QuestionnaireOut])
def list_questionnaires(assessment_id: str, db: Session = Depends(get_db)) -> list[QuestionnaireOut]:
    _assessment(db, assessment_id)
    return [
        QuestionnaireOut(**questionnaires.questionnaire_summary(db, q))
        for q in questionnaires.list_questionnaires(db, assessment_id)
    ]


@router.get(
    "/{assessment_id}/questionnaires/{questionnaire_id}/answers",
    response_model=QuestionnaireAnswersOut,
)
def get_questionnaire_answers(
    assessment_id: str, questionnaire_id: str, db: Session = Depends(get_db)
) -> QuestionnaireAnswersOut:
    assessment = _assessment(db, assessment_id)
    with _questionnaire_errors():
        q = questionnaires.get_questionnaire(db, assessment_id, questionnaire_id)
        answers = questionnaires.questionnaire_answers(
            db, assessment, q, retriever=_questions_retriever()
        )
    return QuestionnaireAnswersOut(
        questionnaire=QuestionnaireOut(**questionnaires.questionnaire_summary(db, q)),
        answers=answers,
        review_required=any(a.get("needs_human_review") for a in answers),
    )


@router.get("/{assessment_id}/questionnaires/{questionnaire_id}/download")
def download_questionnaire(
    assessment_id: str,
    questionnaire_id: str,
    format: str = "original",
    db: Session = Depends(get_db),
) -> Response:
    if format not in {"original", "xlsx"}:
        raise HTTPException(400, "format must be 'original' or 'xlsx'")
    assessment = _assessment(db, assessment_id)
    with _questionnaire_errors():
        q = questionnaires.get_questionnaire(db, assessment_id, questionnaire_id)
        data, filename, media_type = questionnaires.export_questionnaire(
            db, assessment, q, summary=format == "xlsx", retriever=_questions_retriever()
        )
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": _attachment(filename)},
    )


@router.delete("/{assessment_id}/questionnaires/{questionnaire_id}", status_code=204)
def delete_questionnaire(
    assessment_id: str, questionnaire_id: str, db: Session = Depends(get_db)
) -> Response:
    _assessment(db, assessment_id)
    with _questionnaire_errors():
        questionnaires.delete_questionnaire(
            db, questionnaires.get_questionnaire(db, assessment_id, questionnaire_id)
        )
    return Response(status_code=204)


def _attachment(filename: str) -> str:
    """Content-Disposition for a user-supplied name: an ASCII fallback plus the RFC 5987
    UTF-8 form, so quotes/CR/LF in the name can't break (or inject into) the header."""
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


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
        updated = assessments.finish_review(db, assessment, retriever=_questions_retriever())
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
