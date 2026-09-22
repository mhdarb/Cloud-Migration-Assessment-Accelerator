"""Structure-aware chunking strategies, routed by document type.

Replaces the previous flat character-window `chunk_pages` for the ingest path.
Falls back to character-based windowing if `tiktoken` is unavailable, mirroring
the existing optional-dependency pattern used for `faiss` elsewhere in the app.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import get_settings
from app.models.entities import DocumentType
from app.services.parsers import TABLE_BLOCK_END, TABLE_BLOCK_START, ChunkPiece, ParsedPage
from app.services.questionnaire_extract import _Q_LINE

_TABLE_BLOCK = re.compile(
    re.escape(TABLE_BLOCK_START) + r"\n(.*?)\n" + re.escape(TABLE_BLOCK_END), re.S
)

_CHARS_PER_TOKEN_ESTIMATE = 4

# Canonical numeric sizing fields worth summarizing — same vocabulary heuristic_extract's
# INFRA_COLUMN_ALIASES/_canonical_header normalizes column headers into, imported lazily
# in _build_table_summary to avoid a module-level import cycle (heuristic_extract already
# imports this module lazily inside chunks_to_payload).
_NUMERIC_SUMMARY_FIELDS = (
    "vcpus",
    "memory_gb",
    "cpu_utilization_pct",
    "memory_utilization_pct",
    "disk_gb",
    "disk_iops",
    "disk_throughput_mbps",
)


def _get_encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _count_tokens(text: str, enc) -> int:
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def _window_by_tokens(text: str, size_tokens: int, overlap_tokens: int, enc) -> list[str]:
    if enc is not None:
        ids = enc.encode(text)
        if len(ids) <= size_tokens:
            return [text]
        pieces: list[str] = []
        start = 0
        while start < len(ids):
            end = min(start + size_tokens, len(ids))
            pieces.append(enc.decode(ids[start:end]))
            if end >= len(ids):
                break
            start = max(0, end - overlap_tokens)
        return pieces
    char_size = size_tokens * _CHARS_PER_TOKEN_ESTIMATE
    char_overlap = overlap_tokens * _CHARS_PER_TOKEN_ESTIMATE
    if len(text) <= char_size:
        return [text]
    pieces = []
    start = 0
    while start < len(text):
        end = min(start + char_size, len(text))
        pieces.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - char_overlap)
    return pieces


def _pack_blocks(blocks: list[str], size_tokens: int, overlap_tokens: int, enc) -> list[str]:
    """Greedily pack text blocks into token-bounded windows; oversized blocks get token-windowed."""
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0
    for block in blocks:
        block_tokens = _count_tokens(block, enc)
        if block_tokens > size_tokens:
            if buffer:
                chunks.append("\n\n".join(buffer))
                buffer, buffer_tokens = [], 0
            chunks.extend(_window_by_tokens(block, size_tokens, overlap_tokens, enc))
            continue
        if buffer and buffer_tokens + block_tokens > size_tokens:
            chunks.append("\n\n".join(buffer))
            buffer, buffer_tokens = [], 0
        buffer.append(block)
        buffer_tokens += block_tokens
    if buffer:
        chunks.append("\n\n".join(buffer))
    return chunks


def chunk_prose_recursive(
    pages: list[ParsedPage], size_tokens: int = 600, overlap_tokens: int = 80
) -> list[ChunkPiece]:
    """Split on paragraph boundaries first, token-window any oversized paragraph."""
    enc = _get_encoding()
    pieces: list[ChunkPiece] = []
    for page in pages:
        text = (page.text or "").strip()
        if not text:
            continue
        blocks = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()] or [text]
        offset = 0
        for chunk_text in _pack_blocks(blocks, size_tokens, overlap_tokens, enc):
            pieces.append(
                ChunkPiece(
                    page=page.page,
                    offset_start=offset,
                    offset_end=offset + len(chunk_text),
                    text=chunk_text,
                )
            )
            offset += len(chunk_text)
    return pieces


def _extract_number(cell: str) -> float | None:
    """Strict numeric parse — a whole cell, not "contains a digit somewhere" (which would
    wrongly count "RHEL 8" or "x64" as numeric and skew the shape-guard/summary math)."""
    text = cell.strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class ShapeCheck:
    ok: bool
    reason: str = ""


def _looks_like_header(candidate: str, following: list[str]) -> bool:
    """True if `candidate` reads structurally like a header compared to the rows after it —
    mostly non-numeric while the following rows are mostly numeric in the same columns.
    Used to catch a second header row hiding among the data (multi-row header)."""
    cand_cells = [c.strip() for c in candidate.split("|")]
    if not cand_cells or not following:
        return False
    cand_ratio = sum(1 for c in cand_cells if _extract_number(c) is not None) / len(cand_cells)
    sample_ratios = []
    for line in following[:5]:
        cells = [c.strip() for c in line.split("|")]
        if cells:
            sample_ratios.append(sum(1 for c in cells if _extract_number(c) is not None) / len(cells))
    if not sample_ratios:
        return False
    avg_sample_ratio = sum(sample_ratios) / len(sample_ratios)
    return cand_ratio < 0.2 and (avg_sample_ratio - cand_ratio) > 0.3


def check_table_shape(header: str, data_lines: list[str]) -> ShapeCheck:
    """Detect inconsistent column counts or a likely second header row before trusting
    flat row-group chunking — a table this irregular should be flagged, not silently
    flat-chunked and hoped to work."""
    data_lines = [line for line in data_lines if line.strip()]
    if not data_lines:
        return ShapeCheck(True)
    header_count = len(header.split("|"))
    col_counts = [len(line.split("|")) for line in data_lines]
    mismatched = sum(1 for c in col_counts if c != header_count)
    if mismatched / len(col_counts) > 0.1:
        return ShapeCheck(
            False,
            f"inconsistent column counts ({mismatched}/{len(col_counts)} rows differ from "
            f"the header's {header_count} columns)",
        )
    if len(data_lines) >= 2 and _looks_like_header(data_lines[0], data_lines[1:]):
        return ShapeCheck(False, "a second header-like row was detected under the first")
    return ShapeCheck(True)


def _build_table_summary(
    page: ParsedPage, title: str, header: str, data_lines: list[str]
) -> ChunkPiece | None:
    """One extra chunk per clean inventory table holding deterministically computed
    aggregates (sum/avg/min/max/count) for each numeric sizing field — so "what's the
    total vCPU across all servers?" retrieves one authoritative chunk instead of the
    model trying to re-sum across many row-group chunks."""
    from app.services.heuristic_extract import _canonical_header

    headers = [_canonical_header(c) for c in header.split("|")]
    field_indexes = {f: i for i, f in enumerate(headers) if f in _NUMERIC_SUMMARY_FIELDS}
    if not field_indexes:
        return None

    values: dict[str, list[float]] = {f: [] for f in field_indexes}
    row_count = 0
    for line in data_lines:
        cells = [c.strip() for c in line.split("|")]
        if not any(cells):
            continue
        row_count += 1
        for field, idx in field_indexes.items():
            if idx < len(cells):
                num = _extract_number(cells[idx])
                if num is not None:
                    values[field].append(num)

    aggregated = {f: nums for f, nums in values.items() if nums}
    if row_count == 0 or not aggregated:
        return None

    sheet_label = title.lstrip("#").strip() or "Inventory"
    lines_out = [f"# {sheet_label} — COMPUTED SUMMARY", f"Row count: {row_count}"]
    for field, nums in aggregated.items():
        lines_out.append(
            f"{field}: sum={sum(nums):g}, avg={sum(nums) / len(nums):.1f}, "
            f"min={min(nums):g}, max={max(nums):g}, count={len(nums)}/{row_count}"
        )
    text = "\n".join(lines_out)
    return ChunkPiece(
        page=page.page,
        offset_start=0,
        offset_end=len(text),
        text=text,
        metadata={"kind": "table_summary", "row_count": row_count},
    )


def _chunk_one_table(
    page: ParsedPage, title: str, header: str, data_lines: list[str], rows_per_chunk: int
) -> list[ChunkPiece]:
    """Shared table-chunking core — shape guard, then either a fallback-tagged prose
    chunking or a computed-summary chunk plus row-groups. Used both for whole-sheet
    inventory tables and tables detected embedded inside a prose document."""
    shape = check_table_shape(header, data_lines)
    if not shape.ok:
        combined = "\n".join(([title] if title else []) + [header, *data_lines])
        fallback = chunk_prose_recursive([ParsedPage(page=page.page, text=combined)])
        return [
            ChunkPiece(
                page=piece.page,
                offset_start=piece.offset_start,
                offset_end=piece.offset_end,
                text=piece.text,
                metadata={
                    **piece.metadata,
                    "shape_guard_failed": True,
                    "shape_guard_reason": shape.reason,
                },
            )
            for piece in fallback
        ]

    pieces: list[ChunkPiece] = []
    summary_piece = _build_table_summary(page, title, header, data_lines)
    if summary_piece:
        pieces.append(summary_piece)

    offset = 0
    for start in range(0, len(data_lines), rows_per_chunk):
        group = data_lines[start : start + rows_per_chunk]
        body_lines = ([title] if title else []) + [header] + group
        chunk_text = "\n".join(body_lines)
        pieces.append(
            ChunkPiece(
                page=page.page,
                offset_start=offset,
                offset_end=offset + len(chunk_text),
                text=chunk_text,
                metadata={"row_range": [start, start + len(group)]},
            )
        )
        offset += len(chunk_text)
    return pieces


def chunk_inventory_rows(pages: list[ParsedPage], rows_per_chunk: int = 20) -> list[ChunkPiece]:
    """Group N pipe-delimited rows per chunk, repeating the header row in every chunk.

    A page whose shape looks irregular (inconsistent column counts, a hidden second
    header row) is *not* flat-chunked and hoped to work — it falls back to the plain
    paragraph chunker and the pieces are tagged so `ingest.py` can surface a gap instead
    of silently mis-extracting it.
    """
    pieces: list[ChunkPiece] = []
    for page in pages:
        text = (page.text or "").strip()
        if not text:
            continue
        lines = text.split("\n")
        title = ""
        idx = 0
        if lines and lines[0].startswith("#"):
            title = lines[0]
            idx = 1
        if idx >= len(lines):
            pieces.append(
                ChunkPiece(page=page.page, offset_start=0, offset_end=len(text), text=text)
            )
            continue
        header = lines[idx]
        data_lines = lines[idx + 1 :]
        if not data_lines:
            pieces.append(
                ChunkPiece(page=page.page, offset_start=0, offset_end=len(text), text=text)
            )
            continue
        pieces.extend(_chunk_one_table(page, title, header, data_lines, rows_per_chunk))
    return pieces


@dataclass(frozen=True)
class _ProseSegment:
    text: str


@dataclass(frozen=True)
class _TableSegment:
    header: str
    data_lines: list[str]


def _split_prose_and_tables(text: str) -> list[_ProseSegment | _TableSegment]:
    """Split page text into ordered prose/table segments around any
    `<<TABLE>>...<<END_TABLE>>`-marked blocks (from `_parse_docx`), preserving the
    original document order."""
    segments: list[_ProseSegment | _TableSegment] = []
    last_end = 0
    for m in _TABLE_BLOCK.finditer(text):
        before = text[last_end : m.start()].strip()
        if before:
            segments.append(_ProseSegment(before))
        table_lines = [line for line in m.group(1).split("\n") if line.strip()]
        if len(table_lines) >= 2:
            segments.append(_TableSegment(table_lines[0], table_lines[1:]))
        elif table_lines:
            segments.append(_ProseSegment(table_lines[0]))
        last_end = m.end()
    tail = text[last_end:].strip()
    if tail:
        segments.append(_ProseSegment(tail))
    return segments or [_ProseSegment(text)]


def chunk_prose_with_tables(
    pages: list[ParsedPage],
    size_tokens: int = 600,
    overlap_tokens: int = 80,
    rows_per_chunk: int = 20,
) -> list[ChunkPiece]:
    """Prose chunking (paragraph-boundary packing) that also detects embedded
    `<<TABLE>>`-marked blocks and routes just those through the same shape-guard +
    row-group + summary logic `chunk_inventory_rows` uses — keeping a table embedded in
    an architecture/requirements doc in its original document position, instead of
    losing its structure to plain paragraph packing (or, for PDFs, at parse time)."""
    enc = _get_encoding()
    pieces: list[ChunkPiece] = []
    for page in pages:
        text = (page.text or "").strip()
        if not text:
            continue
        offset = 0
        for segment in _split_prose_and_tables(text):
            if isinstance(segment, _ProseSegment):
                blocks = [b.strip() for b in re.split(r"\n\s*\n", segment.text) if b.strip()] or [
                    segment.text
                ]
                for chunk_text in _pack_blocks(blocks, size_tokens, overlap_tokens, enc):
                    pieces.append(
                        ChunkPiece(
                            page=page.page,
                            offset_start=offset,
                            offset_end=offset + len(chunk_text),
                            text=chunk_text,
                        )
                    )
                    offset += len(chunk_text)
            else:
                for table_piece in _chunk_one_table(
                    page, "", segment.header, segment.data_lines, rows_per_chunk
                ):
                    pieces.append(
                        ChunkPiece(
                            page=table_piece.page,
                            offset_start=offset,
                            offset_end=offset + len(table_piece.text),
                            text=table_piece.text,
                            metadata={**table_piece.metadata, "embedded_table": True},
                        )
                    )
                    offset += len(table_piece.text)
    return pieces


def chunk_questionnaire_pairs(pages: list[ParsedPage]) -> list[ChunkPiece]:
    """One chunk per Q + trailing answer text, using the same boundary regex as
    `questionnaire_extract.parse_questionnaire_questions`."""
    pieces: list[ChunkPiece] = []
    for page in pages:
        text = (page.text or "").strip()
        if not text:
            continue
        lines = text.split("\n")
        boundaries = [i for i, line in enumerate(lines) if _Q_LINE.match(line.strip())]
        if not boundaries:
            pieces.append(
                ChunkPiece(page=page.page, offset_start=0, offset_end=len(text), text=text)
            )
            continue
        offset = 0
        if boundaries[0] > 0:
            preamble = "\n".join(lines[: boundaries[0]]).strip()
            if preamble:
                pieces.append(
                    ChunkPiece(
                        page=page.page,
                        offset_start=0,
                        offset_end=len(preamble),
                        text=preamble,
                        metadata={"qa_index": -1},
                    )
                )
                offset = len(preamble)
        for i, start in enumerate(boundaries):
            end = boundaries[i + 1] if i + 1 < len(boundaries) else len(lines)
            block = "\n".join(lines[start:end]).strip()
            if not block:
                continue
            pieces.append(
                ChunkPiece(
                    page=page.page,
                    offset_start=offset,
                    offset_end=offset + len(block),
                    text=block,
                    metadata={"qa_index": i},
                )
            )
            offset += len(block)
    return pieces


def chunk_code_manifest_file(
    path: str, text: str, size_tokens: int = 600, overlap_tokens: int = 80
) -> list[ChunkPiece]:
    """Split one manifest file's text into bounded pieces instead of a single unbounded chunk."""
    enc = _get_encoding()
    body = text or ""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()] or [body]
    packed = _pack_blocks(blocks, size_tokens, overlap_tokens, enc) or [""]
    pieces: list[ChunkPiece] = []
    offset = 0
    for i, chunk_text in enumerate(packed):
        prefixed = f"# FILE: {path}\n{chunk_text}" if i == 0 else chunk_text
        pieces.append(
            ChunkPiece(
                page=1,
                offset_start=offset,
                offset_end=offset + len(prefixed),
                text=prefixed,
                metadata={"file_path": path, "part": i},
            )
        )
        offset += len(prefixed)
    return pieces


class DocumentChunker:
    """Dispatches to a chunking strategy by `DocumentType`."""

    def chunk(
        self,
        pages: list[ParsedPage],
        *,
        doc_type: DocumentType,
        filename: str = "",
    ) -> list[ChunkPiece]:
        settings = get_settings()
        if doc_type == DocumentType.inventory:
            return chunk_inventory_rows(pages, settings.chunk_inventory_rows)
        if doc_type == DocumentType.questionnaire:
            return chunk_questionnaire_pairs(pages)
        return chunk_prose_with_tables(
            pages, settings.chunk_size_tokens, settings.chunk_overlap_tokens, settings.chunk_inventory_rows
        )
