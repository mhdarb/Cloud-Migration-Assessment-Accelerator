from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import (
    Application,
    AssessmentOutput,
    Chunk,
    Claim,
    Conflict,
    DatabaseEntity,
    DependencyEdge,
    Document,
    DocumentType,
    InfrastructureRecommendation,
    Interface,
    Server,
)
from app.schemas.api import ExtractionResult
from app.services.chunkers import DocumentChunker, chunk_code_manifest_file
from app.services.code_manifests import attach_chunk_ids, process_code_snapshot
from app.services.parsers import PRECEDENCE, ChunkPiece, ParseResult, parse_file
from app.services.ports import VectorIndex

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    manifest_extractions: list[ExtractionResult] = field(default_factory=list)
    code_snapshot_count: int = 0
    manifest_files_parsed: int = 0


def clear_derived(
    db: Session,
    assessment_id: str,
    indexes: list[VectorIndex],
    storage_dir: str,
) -> None:
    for model in (
        AssessmentOutput,
        InfrastructureRecommendation,
        Conflict,
        DependencyEdge,
        Claim,
        Application,
        Server,
        DatabaseEntity,
        Interface,
        Chunk,
    ):
        db.query(model).filter(model.assessment_id == assessment_id).delete()
    db.commit()
    for index in indexes:
        index.clear(assessment_id)
    code_root = Path(storage_dir) / assessment_id / "code"
    if code_root.exists():
        shutil.rmtree(code_root, ignore_errors=True)


def ingest_documents(db: Session, assessment_id: str) -> IngestResult:
    result = IngestResult()
    documents = db.query(Document).filter(Document.assessment_id == assessment_id).all()
    for doc in documents:
        _ingest_one(db, assessment_id, doc, result)
    db.commit()
    return result


def _classify_document(doc: Document, parsed: ParseResult, result: IngestResult) -> None:
    """Route document-type labeling through the configured DocClassifier.

    Uses the classifier's type when confident; otherwise keeps the keyword-ladder
    guess (`parsed.doc_type`, already assigned to `doc.doc_type`) and surfaces the
    uncertainty as a report gap — the same mechanism parse errors already use.
    `PRECEDENCE[doc_type]` lookup is unaffected either way.
    """
    from app.services.providers import get_doc_classifier

    sample = parsed.pages[0].text[:1500] if parsed.pages else ""
    try:
        classification = get_doc_classifier().classify(doc.filename, sample)
    except Exception:
        logger.exception("Doc classification failed for %s; keeping keyword guess", doc.filename)
        return
    doc.doc_type_confidence = classification.confidence
    doc.doc_type_rationale = classification.rationale
    settings = get_settings()
    if classification.confidence >= settings.doc_classify_review_threshold:
        doc.doc_type = classification.doc_type
    else:
        result.manifest_extractions.append(
            ExtractionResult(
                gaps=[
                    f"Document '{doc.filename}' classified as {classification.doc_type.value} "
                    f"with low confidence ({classification.confidence:.2f}): "
                    f"{classification.rationale} — verify document type"
                ]
            )
        )


def _flag_shape_guard_failures(
    doc: Document, pieces: list[ChunkPiece], result: IngestResult
) -> None:
    """Surface a report gap when `chunk_inventory_rows`'s shape guard routed a page to
    the plain-prose fallback instead of row-group chunking — visible uncertainty instead
    of silently mis-extracting an irregularly-shaped table."""
    reasons = sorted(
        {
            piece.metadata.get("shape_guard_reason", "irregular table shape")
            for piece in pieces
            if piece.metadata.get("shape_guard_failed")
        }
    )
    if reasons:
        result.manifest_extractions.append(
            ExtractionResult(
                gaps=[
                    f"'{doc.filename}' has an irregularly-shaped table ({reason}); "
                    "row-level extraction was skipped for it — verify manually."
                    for reason in reasons
                ]
            )
        )


def _cap_chunks(
    doc: Document, pieces: list[ChunkPiece], result: IngestResult
) -> list[ChunkPiece]:
    """Bound how many chunks a single document can contribute. A pathologically large or
    adversarial upload could otherwise generate tens of thousands of embedding calls and
    DB rows; past the cap we keep the leading chunks and surface the truncation as a gap."""
    cap = get_settings().max_chunks_per_doc
    if len(pieces) <= cap:
        return pieces
    result.manifest_extractions.append(
        ExtractionResult(
            gaps=[
                f"'{doc.filename}' produced {len(pieces)} chunks, over the {cap} limit "
                f"(MAX_CHUNKS_PER_DOC); only the first {cap} were ingested — some content "
                "was not indexed."
            ]
        )
    )
    return pieces[:cap]


def _ingest_one(
    db: Session, assessment_id: str, doc: Document, result: IngestResult
) -> None:
    try:
        parsed = parse_file(doc.storage_path, doc.filename)
    except Exception as exc:
        logger.exception("Parse failed for %s", doc.filename)
        summary = dict(doc.parse_summary or {})
        summary["parse_error"] = str(exc)
        doc.parse_summary = summary
        result.manifest_extractions.append(
            ExtractionResult(gaps=[f"Skipped unreadable document {doc.filename}: {exc}"])
        )
        return

    doc.doc_type = parsed.doc_type
    if parsed.warnings:
        # Scanned/empty pages, resource-cap truncation, recoverable format issues — make
        # each visible as a report gap instead of a silently degraded parse.
        result.manifest_extractions.append(ExtractionResult(gaps=list(parsed.warnings)))
    if not doc.filename.lower().endswith(".zip"):
        # Zip -> code_snapshot is unambiguous from the extension alone; classifying
        # it would just waste an LLM call for zero decision value.
        _classify_document(doc, parsed, result)
    doc.precedence = PRECEDENCE.get(doc.doc_type, 50)
    doc.page_count = parsed.page_count
    doc.parse_summary = parsed.summary
    pieces = DocumentChunker().chunk(parsed.pages, doc_type=parsed.doc_type, filename=doc.filename)
    pieces = _cap_chunks(doc, pieces, result)
    _flag_shape_guard_failures(doc, pieces, result)
    chunk_index = 0
    for piece in pieces:
        db.add(
            Chunk(
                assessment_id=assessment_id,
                document_id=doc.id,
                chunk_index=chunk_index,
                page=piece.page,
                offset_start=piece.offset_start,
                offset_end=piece.offset_end,
                text=piece.text,
                metadata_json=piece.metadata or None,
            )
        )
        chunk_index += 1

    if doc.doc_type == DocumentType.code_snapshot or doc.filename.lower().endswith(".zip"):
        _ingest_code_snapshot(db, assessment_id, doc, chunk_index, result)


def _ingest_code_snapshot(
    db: Session,
    assessment_id: str,
    doc: Document,
    chunk_index: int,
    result: IngestResult,
) -> None:
    result.code_snapshot_count += 1
    doc.doc_type = DocumentType.code_snapshot
    doc.precedence = PRECEDENCE[DocumentType.code_snapshot]
    try:
        scan = process_code_snapshot(doc.storage_path, assessment_id, doc.id)
        settings = get_settings()
        path_to_chunk: dict[str, list[tuple[str, str]]] = {}
        for mf in scan.files:
            pieces = chunk_code_manifest_file(
                mf.relative_path,
                mf.text,
                settings.chunk_size_tokens,
                settings.chunk_overlap_tokens,
            )
            items: list[tuple[str, str]] = []
            for piece in pieces:
                # Assign the id ourselves instead of relying on the column's default
                # (evaluated at flush/INSERT time) -- lets every chunk in this manifest
                # file batch into one `db.add_all` instead of a flush per chunk, which
                # for a code snapshot with many manifest files means many fewer
                # synchronous round-trips to the database.
                chunk_id = str(uuid.uuid4())
                chunk = Chunk(
                    id=chunk_id,
                    assessment_id=assessment_id,
                    document_id=doc.id,
                    chunk_index=chunk_index,
                    page=piece.page,
                    offset_start=piece.offset_start,
                    offset_end=piece.offset_end,
                    text=piece.text,
                    metadata_json=piece.metadata or None,
                )
                db.add(chunk)
                items.append((chunk_id, piece.text))
                chunk_index += 1
            path_to_chunk[mf.relative_path] = items
        result.manifest_files_parsed += len(scan.files)
        attached = attach_chunk_ids(scan, path_to_chunk)
        if scan.gaps and not scan.files:
            attached.gaps.extend(scan.gaps)
        result.manifest_extractions.append(attached)
        summary = dict(doc.parse_summary or {})
        summary["manifest_files"] = len(scan.files)
        doc.parse_summary = summary
    except Exception as exc:
        logger.exception("Code snapshot processing failed for %s", doc.filename)
        result.manifest_extractions.append(
            ExtractionResult(gaps=[f"Failed to process code snapshot {doc.filename}: {exc}"])
        )
