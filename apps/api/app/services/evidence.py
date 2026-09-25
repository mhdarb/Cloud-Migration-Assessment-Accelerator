from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Chunk, Document
from app.services.citations import quote_grounded

_INTERNAL_FIELDS = {"text"}


def grounded_quote_for_chunk(entry: dict[str, Any], quote: str | None) -> str | None:
    """A claim/answer's evidence can span multiple chunks, but a single `evidence_quote`
    string doesn't necessarily appear verbatim in every one of them -- `citations.py`
    sometimes keeps every originally-cited chunk when the quote is only grounded in their
    *combined* text, not any single chunk. Only attribute the quote to a chunk it's
    actually present in, so a source doesn't get shown claiming a substring it doesn't
    literally contain."""
    if not quote:
        return None
    return quote if quote_grounded(quote, [entry.get("text", "")]) else None


def evidence_locator(filename: str, page: int | None, metadata: dict[str, Any] | None) -> str | None:
    """Where in the source document a chunk came from, phrased for that format.

    A bare page number is only meaningful for PDFs. DOCX has no page concept (Word paginates
    at render time, so the whole document parses as one unit), a workbook's "page" is its
    sheet index, and CSV/JSON/code are single-unit — printing "p. 1" for those is noise. We
    use the structural anchor the chunker recorded instead, and return None when there is
    nothing more specific than the filename itself.
    """
    meta = metadata or {}
    sheet = meta.get("sheet")
    sheet_prefix = f'sheet "{sheet}", ' if sheet else ""

    if meta.get("file_path"):
        return str(meta["file_path"])
    if meta.get("kind") == "table_summary":
        return f"{sheet_prefix}computed summary"
    row_range = meta.get("row_range")
    if isinstance(row_range, list | tuple) and len(row_range) == 2:
        start, end = int(row_range[0]), int(row_range[1])
        rows = f"row {end}" if end - start == 1 else f"rows {start + 1}–{end}"
        return f"{sheet_prefix}{rows}"
    qa_index = meta.get("qa_index")
    if isinstance(qa_index, int) and qa_index >= 0:
        return f"Q{qa_index + 1}"
    if sheet:
        return f'sheet "{sheet}"'
    if page and filename.lower().endswith(".pdf"):
        return f"p. {page}"
    return None


def public_evidence_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop internal-only fields (`text`, kept in `resolve_evidence_map` entries only for
    `grounded_quote_for_chunk`'s own use) before an entry is serialized as `EvidenceOut`."""
    return {k: v for k, v in entry.items() if k not in _INTERNAL_FIELDS}


def build_evidence_list(
    evidence_by_chunk: dict[str, dict[str, Any]],
    chunk_ids: list[str],
    quote: str | None,
) -> list[dict[str, Any]]:
    """Build the evidence list for one claim/dependency's `chunk_ids`, attaching `quote`
    only to the specific chunk(s) it's actually grounded in (see `grounded_quote_for_chunk`)."""
    items = []
    for chunk_id in chunk_ids:
        entry = evidence_by_chunk.get(chunk_id)
        if not entry:
            continue
        items.append(
            {**public_evidence_fields(entry), "quote": grounded_quote_for_chunk(entry, quote)}
        )
    return items


def resolve_evidence(
    db: Session,
    chunk_ids: list[str],
    *,
    quote: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve internal chunk IDs into human-readable document locations."""
    resolved = resolve_evidence_map(db, chunk_ids)
    return build_evidence_list(resolved, chunk_ids, quote)


def resolve_evidence_map(
    db: Session,
    chunk_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Resolve many chunks in two queries, keyed by chunk ID. Each entry carries an
    internal `text` field (the chunk's own text, needed by `_grounded_quote`) that callers
    must not forward as-is into an `EvidenceOut` response -- `build_evidence_list` strips it."""
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
            "locator": evidence_locator(document.filename, chunk.page, chunk.metadata_json),
            "text": chunk.text,
        }
    return evidence
