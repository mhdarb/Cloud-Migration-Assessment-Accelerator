from __future__ import annotations

import csv
import json
import logging
import re
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

from app.config import get_settings
from app.models.entities import DocumentType

logger = logging.getLogger(__name__)

TABLE_BLOCK_START = "<<TABLE>>"
TABLE_BLOCK_END = "<<END_TABLE>>"

# Legacy binary Word (.doc) is an OLE2 compound file; a .docx is a ZIP (starts "PK").
# python-docx only reads the ZIP format, so a .doc renamed/handed to it raises an opaque
# error -- we detect the OLE2 magic and raise a clear, actionable message instead.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# A markdown table separator row, e.g. "|---|:--:|" or "--- | ---".
_MD_TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


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
    # Non-fatal parse issues (scanned/empty pages, truncation from a resource cap, an
    # unsupported-but-recoverable format). `ingest` surfaces each as a report gap, so a
    # degraded parse is visible instead of silently lossy.
    warnings: list[str] = field(default_factory=list)


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
    settings = get_settings()
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

    _check_file_size(path, filename, settings)

    if suffix == ".pdf":
        result = _parse_pdf(path, settings)
    elif suffix == ".docx":
        result = _parse_docx(path)
    elif suffix == ".doc":
        # A true .doc (OLE2) can't be read by python-docx; some tools also mislabel a
        # .docx as .doc, so sniff the magic bytes rather than assuming.
        if _is_ole2(path):
            raise ValueError(
                "legacy binary .doc format is not supported; re-save as .docx (or PDF) and re-upload"
            )
        result = _parse_docx(path)
    elif suffix in {".xlsx", ".xls", ".xlsm"}:
        result = _parse_xlsx(path, settings)
    elif suffix == ".csv":
        result = _parse_csv(path)
    elif suffix == ".json":
        result = _parse_json(path)
    elif suffix in {".md", ".markdown"}:
        text = _read_text_file(path)
        result = ParseResult(
            pages=[ParsedPage(page=1, text=_convert_markdown_tables(text))], page_count=1
        )
    else:
        text = _read_text_file(path)
        result = ParseResult(pages=[ParsedPage(page=1, text=_convert_markdown_tables(text))], page_count=1)

    _enforce_page_cap(result, filename, settings)
    _flag_empty_extraction(result, filename, suffix, settings)

    sample = " ".join(p.text for p in result.pages)[:2000]
    result.doc_type = infer_doc_type(filename, sample)
    result.summary = {
        "chars": sum(len(p.text) for p in result.pages),
        "pages": result.page_count,
        "filename": filename,
        "warnings": list(result.warnings),
    }
    return result


def _read_text_file(path: str) -> str:
    """Decode a text file defensively: honor a UTF-8 BOM, never raise on a stray byte."""
    return Path(path).read_text(encoding="utf-8-sig", errors="replace")


def _is_ole2(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(8) == _OLE2_MAGIC
    except OSError:
        return False


def _check_file_size(path: str, filename: str, settings) -> None:
    """Refuse a file larger than the configured cap before we load it into memory."""
    try:
        size_mb = Path(path).stat().st_size / (1024 * 1024)
    except OSError:
        return
    if size_mb > settings.max_file_mb:
        raise ValueError(
            f"{filename} is {size_mb:.0f} MB, over the {settings.max_file_mb:.0f} MB limit; "
            "split it or raise MAX_FILE_MB"
        )


def _enforce_page_cap(result: ParseResult, filename: str, settings) -> None:
    """Truncate a document with more pages than the cap, surfacing the loss as a warning
    rather than letting an enormous PDF blow up ingest downstream."""
    cap = settings.max_pages_per_doc
    if result.page_count > cap:
        result.warnings.append(
            f"'{filename}' has {result.page_count} pages; only the first {cap} were ingested "
            "(MAX_PAGES_PER_DOC) — remaining pages were skipped."
        )
        result.pages = result.pages[:cap]
        result.page_count = cap


def _flag_empty_extraction(result: ParseResult, filename: str, suffix: str, settings) -> None:
    """Detect a document that parsed 'successfully' but yielded almost no text — the
    classic scanned/image-only PDF, or a corrupt file. Without this, such a document is
    accepted and contributes zero chunks with no trace; here it becomes a visible gap."""
    total_chars = sum(len(p.text.strip()) for p in result.pages)
    floor = settings.min_chars_per_page * max(result.page_count, 1)
    if result.page_count > 0 and total_chars < floor:
        if suffix == ".pdf":
            hint = (
                "it looks scanned or image-only — enable OCR (OCR_ENABLED=true with the "
                "tesseract binary installed) or upload a text-based version"
            )
        else:
            hint = "it may be empty or corrupt — verify the source file"
        result.warnings.append(
            f"'{filename}' produced almost no extractable text ({total_chars} chars across "
            f"{result.page_count} page(s)); {hint}."
        )


def _parse_pdf(path: str, settings) -> ParseResult:
    """Prefer `pdfplumber` (structured tables + OCR-able page images) when it's installed
    and enabled; fall back to plain `pypdf` text extraction otherwise. Both paths honor
    the page cap and never raise on a single bad page."""
    if settings.pdf_table_extraction or settings.ocr_enabled:
        plumbed = _parse_pdf_plumber(path, settings)
        if plumbed is not None:
            return plumbed
    return _parse_pdf_pypdf(path, settings)


def _parse_pdf_pypdf(path: str, settings) -> ParseResult:
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        if i > settings.max_pages_per_doc:
            break
        try:
            text = page.extract_text() or ""
        except Exception:
            logger.warning("pypdf failed to extract page %d of %s", i, path)
            text = ""
        pages.append(ParsedPage(page=i, text=text))
    return ParseResult(pages=pages, page_count=len(pages))


def _parse_pdf_plumber(path: str, settings) -> ParseResult | None:
    """Returns None (so the caller falls back to pypdf) when pdfplumber isn't installed.

    For each page: pull structured tables out as `<<TABLE>>` blocks (so inventory tables
    embedded in a PDF get the same row-group/summary chunking a spreadsheet does), take
    the surrounding prose from the non-table regions, and — only when a page is otherwise
    empty and OCR is enabled — OCR the rendered page image as a last resort."""
    try:
        import pdfplumber
    except Exception:
        return None

    pages: list[ParsedPage] = []
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                if i > settings.max_pages_per_doc:
                    break
                pages.append(ParsedPage(page=i, text=_plumber_page_text(page, settings)))
    except Exception:
        logger.exception("pdfplumber failed on %s; falling back to pypdf", path)
        return None
    return ParseResult(pages=pages, page_count=len(pages))


def _plumber_page_text(page: Any, settings) -> str:
    tables: list[Any] = []
    if settings.pdf_table_extraction:
        try:
            tables = page.find_tables()
        except Exception:
            tables = []

    segments: list[str] = []
    try:
        if tables:
            table_bboxes = [t.bbox for t in tables]
            prose_page = page.filter(lambda obj: not _inside_any(obj, table_bboxes))
            prose = prose_page.extract_text() or ""
        else:
            prose = page.extract_text() or ""
    except Exception:
        prose = page.extract_text() or ""
    if prose.strip():
        segments.append(prose.strip())

    for table in tables:
        block = _table_rows_to_block(_safe_extract_table(table))
        if block:
            segments.append(block)

    text = "\n\n".join(segments)
    if not text.strip() and settings.ocr_enabled:
        text = _ocr_page(page, settings)
    return text


def _inside_any(obj: dict, bboxes: list[tuple]) -> bool:
    """True if a pdfplumber object's center falls inside any table bounding box."""
    cx = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
    cy = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
    for x0, top, x1, bottom in bboxes:
        if x0 <= cx <= x1 and top <= cy <= bottom:
            return True
    return False


def _safe_extract_table(table: Any) -> list[list[str]]:
    try:
        return table.extract() or []
    except Exception:
        return []


def _table_rows_to_block(rows: list[list[Any]]) -> str:
    """Render extracted table rows as a `<<TABLE>>` block matching the docx pipe format,
    keeping every cell positional (blank cells preserved) so column indexing survives."""
    lines = []
    for row in rows:
        cells = ["" if c is None else str(c).replace("\n", " ").strip() for c in row]
        if any(cells):
            lines.append(" | ".join(cells))
    if len(lines) < 2:
        return ""
    return f"{TABLE_BLOCK_START}\n" + "\n".join(lines) + f"\n{TABLE_BLOCK_END}"


def _ocr_page(page: Any, settings) -> str:
    """OCR one rendered PDF page. Soft dependency: returns "" (leaving the empty-extraction
    gap to fire) when pytesseract or the tesseract binary is unavailable, or on any error —
    OCR must never be able to fail an ingest."""
    try:
        import pytesseract
    except Exception:
        return ""
    try:
        image = page.to_image(resolution=200).original
        return pytesseract.image_to_string(image, lang=settings.ocr_language) or ""
    except Exception:
        logger.warning("OCR failed for a page of a PDF; leaving it empty")
        return ""


def _convert_markdown_tables(text: str) -> str:
    """Rewrite GitHub-style markdown pipe tables into `<<TABLE>>` blocks so a table in an
    uploaded .md/.txt gets the same structure-aware chunking a docx/xlsx table does,
    instead of being flattened into prose. Non-table text passes through unchanged."""
    if "|" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        # A table needs a header row containing a pipe, then a --- separator row.
        if "|" in lines[i] and i + 1 < n and _MD_TABLE_SEPARATOR.match(lines[i + 1]) and "|" in lines[i + 1]:
            header = _strip_md_row(lines[i])
            block_rows = [header]
            j = i + 2
            while j < n and "|" in lines[j] and lines[j].strip():
                block_rows.append(_strip_md_row(lines[j]))
                j += 1
            if len(block_rows) >= 2:
                out.append(f"{TABLE_BLOCK_START}\n" + "\n".join(block_rows) + f"\n{TABLE_BLOCK_END}")
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _strip_md_row(line: str) -> str:
    """Normalize a markdown row `| a | b |` to the pipe format chunking expects: `a | b`."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return " | ".join(cell.strip() for cell in stripped.split("|"))


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


def _looks_numeric(cell: str) -> bool:
    t = cell.strip().replace(",", "").rstrip("%")
    if not t:
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


def _forward_fill_header(cells: list[str]) -> list[str]:
    """Fill blank header cells from the last non-blank one to the left. A merged header
    cell spanning several columns is read (in read-only mode) as one value plus blanks;
    forward-filling keeps every data column labeled so downstream column indexing lines up."""
    filled: list[str] = []
    last = ""
    for c in cells:
        if c.strip():
            last = c.strip()
        filled.append(c.strip() or last)
    return filled


def _detect_header_row(rows: list[list[str]]) -> int:
    """Pick the header row among the first several rows: the first row that is mostly
    non-numeric AND is followed by a row of the same width with some numeric/populated
    cells. Real spreadsheets often carry a title/blank band above the true header, which
    would otherwise be mistaken for the header and shift every column."""
    for i in range(min(len(rows) - 1, 10)):
        row = rows[i]
        nonblank = [c for c in row if c.strip()]
        if len(nonblank) < 2:
            continue
        numeric_ratio = sum(1 for c in nonblank if _looks_numeric(c)) / len(nonblank)
        nxt = rows[i + 1]
        next_populated = sum(1 for c in nxt if c.strip())
        if numeric_ratio < 0.3 and next_populated >= 2 and len(nxt) == len(row):
            return i
    return 0


def _parse_xlsx(path: str, settings) -> ParseResult:
    wb = load_workbook(path, data_only=True, read_only=True)
    pages = []
    for idx, sheet_name in enumerate(wb.sheetnames, start=1):
        ws = wb[sheet_name]
        raw_rows: list[list[str]] = []
        truncated = False
        for row in ws.iter_rows(values_only=True):
            values = [str(c) if c is not None else "" for c in row]
            if any(v.strip() for v in values):
                raw_rows.append(values)
            if len(raw_rows) >= settings.max_rows_per_sheet:
                truncated = True
                break

        # A single leading "# Sheet:" line stays the chunker's title (it only reads one),
        # so fold any preamble rows above the detected header into that same line rather
        # than emitting extra rows the inventory chunker would treat as the header.
        title = f"# Sheet: {sheet_name}"
        body_lines: list[str] = []
        if raw_rows:
            header_idx = _detect_header_row(raw_rows)
            preamble = [" ".join(c for c in r if c.strip()) for r in raw_rows[:header_idx]]
            preamble = [p for p in preamble if p]
            if preamble:
                title = f"{title} | {' / '.join(preamble)}"
            header = _forward_fill_header(raw_rows[header_idx])
            body_lines.append(" | ".join(header))
            for r in raw_rows[header_idx + 1 :]:
                body_lines.append(" | ".join(r))

        text = "\n".join([title, *body_lines])
        if truncated:
            text += f"\n# NOTE: sheet truncated at {settings.max_rows_per_sheet} rows (MAX_ROWS_PER_SHEET)"
        pages.append(ParsedPage(page=idx, text=text))
    wb.close()
    return ParseResult(pages=pages, page_count=len(pages))


def _parse_csv(path: str) -> ParseResult:
    settings = get_settings()
    rows: list[list[str]] = []
    truncated = False
    with Path(path).open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            rows.append(row)
            if len(rows) >= settings.max_rows_per_sheet:
                truncated = True
                break
    text = "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
    if truncated:
        text += f"\n# NOTE: file truncated at {settings.max_rows_per_sheet} rows (MAX_ROWS_PER_SHEET)"
    return ParseResult(pages=[ParsedPage(page=1, text=text)], page_count=1)


def _find_record_list(data: Any) -> list[dict] | None:
    """Locate the list-of-records in a JSON export without hard-coding a key like
    "servers". Handles a top-level array, and an object whose first list-of-dicts value
    is the inventory (common shapes: {"servers": [...]}, {"assets": [...]}, {"vms": [...]},
    {"data": {"items": [...]}}), searched shallowly."""
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        return data
    if isinstance(data, dict):
        # Prefer a direct child that is a list of dicts.
        for value in data.values():
            if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
                return value
        # One level deeper (e.g. {"data": {"items": [...]}}).
        for value in data.values():
            if isinstance(value, dict):
                found = _find_record_list(value)
                if found is not None:
                    return found
    return None


def _parse_json(path: str) -> ParseResult:
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    rows = _find_record_list(data)
    if rows:
        headers = list(dict.fromkeys(key for row in rows for key in row))
        lines = [" | ".join(headers)]
        lines.extend(" | ".join(str(row.get(key, "")) for key in headers) for row in rows)
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
