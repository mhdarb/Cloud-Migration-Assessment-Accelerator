from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from openpyxl import load_workbook
from pypdf import PdfReader

from app.models.entities import DocumentType

TABLE_BLOCK_START = "<<TABLE>>"
TABLE_BLOCK_END = "<<END_TABLE>>"


@dataclass
class ParsedPage:
    page: int
    text: str


@dataclass
class ParseResult:
    pages: list[ParsedPage] = field(default_factory=list)
    doc_type: DocumentType = DocumentType.unknown
    page_count: int = 0
    summary: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkPiece:
    """A structure-aware chunk produced by a `Chunker` strategy (see `chunkers.py`)."""

    page: int | None
    offset_start: int
    offset_end: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def infer_doc_type(filename: str, text_sample: str) -> DocumentType:
    name = filename.lower()
    sample = text_sample.lower()
    if name.endswith(".zip"):
        return DocumentType.code_snapshot
    if name.endswith((".xlsx", ".xls", ".csv", ".json")) or "inventory" in name or "cmdb" in name:
        return DocumentType.inventory
    if (
        "requirement" in name
        or "nfr" in name
        or "brd" in name
        or "rfc" in name
        or "sla" in name
        or "non-functional" in sample
        or "non functional" in sample
    ):
        return DocumentType.requirements
    if "questionnaire" in name or "assessment q" in sample or "q&a" in sample:
        return DocumentType.questionnaire
    if "runbook" in name or "sop" in name:
        return DocumentType.runbook
    if "architecture" in name or "diagram" in sample or "application" in sample:
        return DocumentType.architecture
    if name.endswith((".pdf", ".docx", ".doc")):
        return DocumentType.architecture
    return DocumentType.unknown


PRECEDENCE = {
    DocumentType.inventory: 100,
    DocumentType.code_snapshot: 90,
    DocumentType.architecture: 80,
    DocumentType.requirements: 70,
    DocumentType.runbook: 60,
    DocumentType.questionnaire: 40,
    DocumentType.unknown: 50,
}


def parse_file(path: str, filename: str) -> ParseResult:
    suffix = Path(filename).suffix.lower()
    if suffix == ".zip":
        result = ParseResult(
            pages=[
                ParsedPage(
                    page=1,
                    text=f"Code snapshot archive: {filename}\nManifests will be extracted during pipeline ingest.",
                )
            ],
            page_count=1,
            doc_type=DocumentType.code_snapshot,
        )
        result.summary = {
            "chars": len(result.pages[0].text),
            "pages": 1,
            "filename": filename,
        }
        return result
    if suffix == ".pdf":
        result = _parse_pdf(path)
    elif suffix in {".doc", ".docx"}:
        result = _parse_docx(path)
    elif suffix in {".xlsx", ".xls", ".xlsm"}:
        result = _parse_xlsx(path)
    elif suffix == ".csv":
        result = _parse_csv(path)
    elif suffix == ".json":
        result = _parse_json(path)
    else:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        result = ParseResult(pages=[ParsedPage(page=1, text=text)], page_count=1)

    sample = " ".join(p.text for p in result.pages)[:2000]
    result.doc_type = infer_doc_type(filename, sample)
    result.summary = {
        "chars": sum(len(p.text) for p in result.pages),
        "pages": result.page_count,
        "filename": filename,
    }
    return result


def _parse_pdf(path: str) -> ParseResult:
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(ParsedPage(page=i, text=text))
    return ParseResult(pages=pages, page_count=len(pages))


def _iter_docx_blocks(document: Any):
    # `docx.Document(...)` is a factory function, not a class — python-docx ships no
    # type stubs for its real return type (docx.document.Document), so this stays `Any`
    # rather than mistyping it as the `Document` factory itself.
    """Walk the document body in true document order, yielding paragraphs and tables
    interleaved as they actually appear — `document.paragraphs`/`document.tables` each
    give one type at a time, losing where a table sits relative to the surrounding text."""
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield DocxParagraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield DocxTable(child, document)


def _parse_docx(path: str) -> ParseResult:
    doc = DocxDocument(path)
    blocks: list[str] = []
    for item in _iter_docx_blocks(doc):
        if isinstance(item, DocxParagraph):
            text = item.text.strip()
            if text:
                blocks.append(text)
            continue
        table_lines = []
        for row in item.rows:
            # Keep every cell (even blank ones) as its own pipe field — dropping empty
            # cells here would shift later columns out of position on any row with a
            # blank value, corrupting the positional column indexing chunking relies on.
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                table_lines.append(" | ".join(cells))
        if len(table_lines) >= 2:
            blocks.append(f"{TABLE_BLOCK_START}\n" + "\n".join(table_lines) + f"\n{TABLE_BLOCK_END}")
        elif table_lines:
            blocks.append(table_lines[0])
    text = "\n\n".join(blocks)
    return ParseResult(pages=[ParsedPage(page=1, text=text)], page_count=1)


def _parse_xlsx(path: str) -> ParseResult:
    wb = load_workbook(path, data_only=True, read_only=True)
    pages = []
    for idx, sheet_name in enumerate(wb.sheetnames, start=1):
        ws = wb[sheet_name]
        lines = [f"# Sheet: {sheet_name}"]
        for row in ws.iter_rows(values_only=True):
            values = [str(c) if c is not None else "" for c in row]
            if any(v.strip() for v in values):
                lines.append(" | ".join(values))
        pages.append(ParsedPage(page=idx, text="\n".join(lines)))
    wb.close()
    return ParseResult(pages=pages, page_count=len(pages))


def _parse_csv(path: str) -> ParseResult:
    with Path(path).open(encoding="utf-8-sig", errors="ignore", newline="") as handle:
        rows = list(csv.reader(handle))
    text = "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
    return ParseResult(pages=[ParsedPage(page=1, text=text)], page_count=1)


def _parse_json(path: str) -> ParseResult:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("servers", [data])
    if isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows):
        headers = list(dict.fromkeys(key for row in rows for key in row))
        lines = [" | ".join(headers)]
        lines.extend(
            " | ".join(str(row.get(key, "")) for key in headers) for row in rows
        )
        text = "\n".join(lines)
    else:
        text = json.dumps(data, indent=2)
    return ParseResult(pages=[ParsedPage(page=1, text=text)], page_count=1)


def chunk_pages(
    pages: list[ParsedPage], chunk_size: int = 1200, overlap: int = 150
) -> list[tuple[int | None, int, int, str]]:
    """Return list of (page, offset_start, offset_end, text)."""
    chunks = []
    for page in pages:
        text = page.text.strip()
        if not text:
            continue
        if len(text) <= chunk_size:
            chunks.append((page.page, 0, len(text), text))
            continue
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk_text = text[start:end]
            chunks.append((page.page, start, end, chunk_text))
            if end >= len(text):
                break
            start = max(0, end - overlap)
    return chunks
