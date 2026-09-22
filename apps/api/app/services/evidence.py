from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Chunk, Document


def resolve_evidence(
    db: Session,
    chunk_ids: list[str],
    *,
    quote: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve internal chunk IDs into human-readable document locations."""
    resolved = resolve_evidence_map(db, chunk_ids)
    return [
        {**resolved[chunk_id], "quote": quote}
        for chunk_id in chunk_ids
        if chunk_id in resolved
    ]


def resolve_evidence_map(
    db: Session,
    chunk_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Resolve many chunks in two queries, keyed by chunk ID."""
    if not chunk_ids:
        return {}

    unique_ids = list(dict.fromkeys(chunk_ids))
    chunks = db.query(Chunk).filter(Chunk.id.in_(unique_ids)).all()
    document_ids = {chunk.document_id for chunk in chunks}
    documents = (
        db.query(Document).filter(Document.id.in_(document_ids)).all()
        if document_ids
        else []
    )
    documents_by_id = {document.id: document for document in documents}

    evidence: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        document = documents_by_id.get(chunk.document_id)
        if not document:
            continue
        evidence[chunk.id] = {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "filename": document.filename,
            "doc_type": document.doc_type.value,
            "page": chunk.page,
        }
    return evidence
