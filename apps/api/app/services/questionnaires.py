"""Client questionnaires: upload a question list, answer it from the assessment's evidence,
download it with the answers written in.

A questionnaire is *not* evidence (it is never chunked or indexed) and its questions are
*not* engagement questions (they don't appear in the Questions list or the report). Each
question is answered exactly like an ad-hoc question — claim match first, grounded RAG
fallback — and cites the source documents, never the questionnaire itself.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.entities import (
    Assessment,
    PipelineStatus,
    Questionnaire,
    QuestionnaireItem,
    QuestionOrigin,
    ReviewDecision,
)
from app.services.assessment_questions import (
    _attach_evidence,
    _questionnaire_document_ids,
    _selected_claims,
    answer_custom_question,
)
from app.services.llm_reasoning import GroundedProse, get_grounded_prose
from app.services.pipeline_lock import is_in_flight
from app.services.ports import Retriever
from app.services.questionnaire_extract import normalize_question_key
from app.services.questionnaire_io import (
    MEDIA_TYPES,
    AnswerCell,
    ParsedItem,
    parse_questionnaire,
    questionnaire_format,
    write_answered,
)
from app.services.storage import save_upload

logger = logging.getLogger(__name__)

MAX_SOURCES_PER_ANSWER = 5
ACCEPTED_SUFFIXES = (".xlsx", ".xlsm", ".csv", ".docx", ".txt", ".md", ".pdf")


class QuestionnaireNotFound(Exception):
    pass


class QuestionnaireBusy(Exception):
    """A pipeline run is rebuilding the evidence the answers would come from."""


def _question_count(db: Session, questionnaire_id: str) -> int:
    return db.query(QuestionnaireItem).filter(QuestionnaireItem.questionnaire_id == questionnaire_id).count()


def questionnaire_summary(db: Session, q: Questionnaire) -> dict[str, Any]:
    fmt = q.file_format
    return {
        "id": q.id,
        "filename": q.filename,
        "file_format": fmt,
        "question_count": _question_count(db, q.id),
        "created_at": q.created_at,
        # PDF can't be edited in place; its download is an Excel answer sheet.
        "answered_format": "xlsx" if fmt == "pdf" else Path(q.filename).suffix.lower().lstrip("."),
    }


async def import_questionnaire(db: Session, assessment: Assessment, upload: UploadFile) -> Questionnaire:
    filename = upload.filename or "questionnaire"
    fmt = questionnaire_format(filename)
    if fmt is None:
        suffix = Path(filename).suffix.lower() or "(none)"
        hint = " Save it as .xlsx or .docx first." if suffix in {".xls", ".doc"} else ""
        raise ValueError(f"Unsupported questionnaire type {suffix}; use {', '.join(ACCEPTED_SUFFIXES)}.{hint}")
    path, filename = await save_upload(assessment.id, upload)
    try:
        items = parse_questionnaire(path, fmt, filename)
    except Exception as exc:
        Path(path).unlink(missing_ok=True)
        logger.info("questionnaire parse failed for %s: %s", filename, exc)
        raise ValueError(f"Could not read {filename}: {exc}") from exc
    if not items:
        Path(path).unlink(missing_ok=True)
        raise ValueError(
            f"No questions found in {filename}. Put questions in a column headed 'Question', "
            "or write them as lines ending in '?'."
        )
    questionnaire = Questionnaire(assessment_id=assessment.id, filename=filename, storage_path=path, file_format=fmt)
    db.add(questionnaire)
    db.flush()
    for position, item in enumerate(items):
        db.add(
            QuestionnaireItem(
                questionnaire_id=questionnaire.id,
                position=position,
                question=item.question,
                question_key=normalize_question_key(item.question),
                locator=item.locator,
            )
        )
    db.commit()
    db.refresh(questionnaire)
    return questionnaire


def list_questionnaires(db: Session, assessment_id: str) -> list[Questionnaire]:
    return (
        db.query(Questionnaire)
        .filter(Questionnaire.assessment_id == assessment_id)
        .order_by(Questionnaire.created_at.asc())
        .all()
    )


def get_questionnaire(db: Session, assessment_id: str, questionnaire_id: str) -> Questionnaire:
    q = (
        db.query(Questionnaire)
        .filter(Questionnaire.id == questionnaire_id, Questionnaire.assessment_id == assessment_id)
        .one_or_none()
    )
    if q is None:
        raise QuestionnaireNotFound(questionnaire_id)
    return q


def _items(db: Session, questionnaire_id: str) -> list[QuestionnaireItem]:
    return (
        db.query(QuestionnaireItem)
        .filter(QuestionnaireItem.questionnaire_id == questionnaire_id)
        .order_by(QuestionnaireItem.position.asc())
        .all()
    )


def _ensure_answerable(assessment: Assessment) -> None:
    if is_in_flight(assessment):
        raise QuestionnaireBusy()
    if assessment.status != PipelineStatus.completed:
        raise ValueError("Run the assessment successfully before answering a questionnaire")


def _fingerprint(db: Session, assessment: Assessment) -> str:
    """Changes whenever the answers could: a new pipeline run, or any review decision."""
    count, latest = (
        db.query(func.count(ReviewDecision.id), func.max(ReviewDecision.updated_at))
        .filter(ReviewDecision.assessment_id == assessment.id)
        .one()
    )
    finished = assessment.pipeline_finished_at.isoformat() if assessment.pipeline_finished_at else "-"
    return f"{finished}|{count}|{latest.isoformat() if latest else '-'}"


def questionnaire_answers(
    db: Session,
    assessment: Assessment,
    questionnaire: Questionnaire,
    *,
    retriever: Retriever | None = None,
    prose: GroundedProse | None = None,
) -> list[dict[str, Any]]:
    _ensure_answerable(assessment)
    fingerprint = _fingerprint(db, assessment)
    if questionnaire.answers_cache is not None and questionnaire.answers_fingerprint == fingerprint:
        return questionnaire.answers_cache

    items = _items(db, questionnaire.id)
    claims = _selected_claims(db, assessment.id)
    excluded = _questionnaire_document_ids(db, assessment.id)
    answers = [
        answer_custom_question(
            db,
            assessment.id,
            question_id=item.id,
            question=item.question,
            origin=QuestionOrigin.questionnaire.value,
            claims=claims,
            retriever=retriever,
            questionnaire_document_ids=excluded,
        )
        for item in items
    ]
    _attach_evidence(db, answers, claims)
    (prose or get_grounded_prose()).rewrite_question_answers(answers)
    for item, answer in zip(items, answers, strict=True):
        answer["position"] = item.position + 1
    questionnaire.answers_cache = _jsonable(answers)
    questionnaire.answers_fingerprint = fingerprint
    db.commit()
    return questionnaire.answers_cache


def _jsonable(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return json.loads(json.dumps(answers, default=str))


def _sources(answer: dict[str, Any]) -> str:
    cited: list[str] = []
    for ev in answer.get("evidence") or []:
        label = ev.get("filename") or "source"
        if ev.get("locator"):
            label = f"{label} ({ev['locator']})"
        if label not in cited:
            cited.append(label)
    if not cited:
        return "None"
    extra = len(cited) - MAX_SOURCES_PER_ANSWER
    shown = "; ".join(cited[:MAX_SOURCES_PER_ANSWER])
    return f"{shown}; +{extra} more" if extra > 0 else shown


def export_questionnaire(
    db: Session,
    assessment: Assessment,
    questionnaire: Questionnaire,
    *,
    summary: bool = False,
    retriever: Retriever | None = None,
    prose: GroundedProse | None = None,
) -> tuple[bytes, str, str]:
    """(file bytes, download filename, media type)."""
    answers = questionnaire_answers(db, assessment, questionnaire, retriever=retriever, prose=prose)
    by_id = {a["id"]: a for a in answers}
    pairs: list[tuple[ParsedItem, AnswerCell]] = []
    for item in _items(db, questionnaire.id):
        answer = by_id.get(item.id)
        if answer is None:
            continue
        pairs.append(
            (
                ParsedItem(item.question, dict(item.locator or {})),
                AnswerCell(
                    answer=str(answer.get("answer") or ""),
                    confidence=float(answer.get("confidence") or 0.0),
                    needs_review=bool(answer.get("needs_human_review")),
                    sources=_sources(answer),
                ),
            )
        )
    data, suffix = write_answered(questionnaire.file_format, questionnaire.storage_path, pairs, summary=summary)
    stem = Path(questionnaire.filename).stem
    return data, f"{stem} - answered{suffix}", MEDIA_TYPES[suffix]


def delete_questionnaire(db: Session, questionnaire: Questionnaire) -> None:
    path = Path(questionnaire.storage_path)
    db.query(QuestionnaireItem).filter(QuestionnaireItem.questionnaire_id == questionnaire.id).delete()
    db.delete(questionnaire)
    db.commit()
    path.unlink(missing_ok=True)


def delete_assessment_questionnaires(db: Session, assessment_id: str) -> None:
    """Rows only — the files live under the assessment's storage directory, which the
    caller removes wholesale."""
    ids = [qid for (qid,) in db.query(Questionnaire.id).filter(Questionnaire.assessment_id == assessment_id)]
    if ids:
        db.query(QuestionnaireItem).filter(QuestionnaireItem.questionnaire_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(Questionnaire).filter(Questionnaire.id.in_(ids)).delete(synchronize_session=False)
