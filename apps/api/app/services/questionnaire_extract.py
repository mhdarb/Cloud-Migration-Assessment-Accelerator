from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.entities import (
    Chunk,
    Document,
    DocumentType,
    EngagementQuestion,
    QuestionOrigin,
)

_Q_LINE = re.compile(
    r"^\s*(?:Q(?:uestion)?\s*[:.)-]|Q\d+\s*[:.)-])\s*(.+)$",
    re.I,
)


def normalize_question_key(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    return cleaned[:240]


def parse_questionnaire_questions(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        match = _Q_LINE.match(raw.strip())
        if not match:
            continue
        question = match.group(1).strip().rstrip("?")
        question = f"{question}?" if question else ""
        key = normalize_question_key(question)
        if len(key) < 8 or key in seen:
            continue
        seen.add(key)
        found.append(question)
    return found


def sync_uploaded_questions(db: Session, assessment_id: str) -> list[EngagementQuestion]:
    db.query(EngagementQuestion).filter(
        EngagementQuestion.assessment_id == assessment_id,
        EngagementQuestion.origin == QuestionOrigin.uploaded,
    ).delete(synchronize_session=False)

    existing = {
        row.question_key
        for row in db.query(EngagementQuestion)
        .filter(EngagementQuestion.assessment_id == assessment_id)
        .all()
    }
    rows: list[EngagementQuestion] = []
    chunks = (
        db.query(Chunk)
        .join(Document, Document.id == Chunk.document_id)
        .filter(
            Chunk.assessment_id == assessment_id,
            Document.doc_type == DocumentType.questionnaire,
        )
        .all()
    )
    for chunk in chunks:
        for question in parse_questionnaire_questions(chunk.text):
            key = normalize_question_key(question)
            if key in existing:
                continue
            existing.add(key)
            row = EngagementQuestion(
                assessment_id=assessment_id,
                origin=QuestionOrigin.uploaded,
                question=question,
                question_key=key,
                source_document_id=chunk.document_id,
                source_chunk_id=chunk.id,
            )
            db.add(row)
            rows.append(row)
    db.commit()
    return rows
