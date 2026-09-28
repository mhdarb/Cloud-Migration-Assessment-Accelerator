from __future__ import annotations

import re
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


_WORD = re.compile(r"[a-z0-9][a-z0-9.%-]*", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Words too common in assessment documents to show that a passage is about the question.
_PASSAGE_NOISE = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "are",
    "is",
    "was",
    "what",
    "which",
    "who",
    "how",
    "does",
    "that",
    "this",
    "any",
    "all",
    "into",
    "must",
    "should",
    "will",
    "can",
    "not",
    "migration",
    "migrate",
    "service",
    "services",
    "application",
    "applications",
    "system",
    "systems",
    "use",
    "used",
    "uses",
    "using",
    "current",
    "requirements",
    "requirement",
}


def passage_terms(*texts: str) -> set[str]:
    """Content words (and short technical tokens like 'rto', 'os', '99.9%') from `texts`."""
    terms: set[str] = set()
    for text in texts:
        for word in _WORD.findall((text or "").lower()):
            word = word.strip(".-")
            if len(word) >= 2 and word not in _PASSAGE_NOISE:
                terms.add(word)
    return terms


def relevant_passage(text: str, terms: set[str], max_chars: int = 280) -> tuple[str, int]:
    """The part of a chunk that best matches `terms`, and how many distinct terms it holds.

    A citation used to show the chunk's first 240 characters — for a document that is its
    title, for a table its caption and header — so the passage that actually supports the
    answer was often not shown at all. Tables are scored row by row and quoted as
    "header / matching row"; prose sentence by sentence (with the next sentence when
    short). Score 0 means nothing in the chunk matches: the chunk shouldn't be cited.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines or not terms:
        return (text or "")[:max_chars], 0
    table_lines = [line for line in lines if " | " in line and not line.startswith("#")]
    if len(table_lines) >= 2:
        header, rows = table_lines[0], table_lines[1:]
        header_hits = terms & passage_terms(header)
        scored = [(len(terms & passage_terms(row)), row) for row in rows]
        score, row = max(scored, key=lambda item: item[0])
        if score == 0:
            return header[:max_chars], len(header_hits)
        # "Column: value · Column: value" keeps the values readable in a wide table, where
        # "header / row" pushed the matching row past the length limit.
        names = [c.strip() for c in header.split("|")]
        values = [c.strip() for c in row.split("|")]
        if len(names) == len(values):
            pairs = [f"{n}: {v}" for n, v in zip(names, values, strict=True) if v]
            return " · ".join(pairs)[:max_chars], score + len(header_hits)
        return f"{header} / {row}"[:max_chars], score + len(header_hits)
    units = [s.strip() for line in lines for s in _SENTENCE_END.split(line) if s.strip()]
    by_sentence = [(len(terms & passage_terms(u)), i) for i, u in enumerate(units)]
    score, index = max(by_sentence, key=lambda item: (item[0], -item[1]))
    passage = units[index]
    if len(passage) < max_chars // 2 and index + 1 < len(units):
        passage = f"{passage} {units[index + 1]}"
    return passage[:max_chars], score


def label_table_row(chunk_text: str, quote: str, max_chars: int = 280) -> str:
    """A quoted table row ("Customer Portal | app-portal-01 | PortalDB | ...") is unreadable
    without its columns; label it with the chunk's header when the cell counts line up."""
    if " | " not in quote:
        return quote
    header = next(
        (line.strip() for line in (chunk_text or "").splitlines() if " | " in line and not line.startswith("#")),
        None,
    )
    names = [c.strip() for c in (header or "").split("|")]
    values = [c.strip() for c in quote.split("|")]
    if not header or header.strip() == quote.strip() or len(names) != len(values):
        return quote
    return " · ".join(f"{n}: {v}" for n, v in zip(names, values, strict=True) if v)[:max_chars]


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
        items.append({**public_evidence_fields(entry), "quote": grounded_quote_for_chunk(entry, quote)})
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
    documents = db.query(Document).filter(Document.id.in_(document_ids)).all() if document_ids else []
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
