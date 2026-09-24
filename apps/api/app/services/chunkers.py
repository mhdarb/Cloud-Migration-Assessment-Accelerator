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


# Split after Western terminators followed by whitespace, OR after a CJK terminator
# (。！？；) which is written with no trailing space — without the CJK arm a Chinese/
# Japanese page reads as one giant "sentence" and always falls to the raw token cut.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|(?<=[。！？；])")


def _split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_BOUNDARY.split(text) if s.strip()]
    return parts if len(parts) > 1 else [text]


def _paragraph_spans(text: str, base: int = 0) -> list[tuple[str, int, int]]:
    """Paragraphs split on blank lines, each with its real (start, end) character offset in
    `text` (shifted by `base`). Unlike a running length counter, these offsets point back
    into the actual source, so a retrieved chunk can be located/highlighted in the original
    document. Paragraphs are non-overlapping substrings, so a forward-only `find` is exact."""
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for block in re.split(r"\n\s*\n", text):
        stripped = block.strip()
        if not stripped:
            continue
        start = text.find(stripped, cursor)
        if start < 0:
            start = cursor
        end = start + len(stripped)
        cursor = end
        spans.append((stripped, base + start, base + end))
    if not spans:
        s = text.strip()
        return [(s, base, base + len(s))] if s else []
    return spans


def _pack_spans(
    spans: list[tuple[str, int, int]], size_tokens: int, overlap_tokens: int, enc
) -> list[tuple[str, int, int]]:
    """Greedily pack (text, start, end) paragraph spans into token-bounded chunks, tracking
    real source offsets. Chunk boundaries match `_pack_blocks` exactly (so behavior is
    unchanged); only the offsets are now genuine. An oversized paragraph is token-windowed,
    and each of its sub-pieces inherits that paragraph's span."""
    results: list[tuple[str, int, int]] = []
    buffer: list[tuple[str, int, int]] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buffer, buf_tokens
        if buffer:
            results.append(("\n\n".join(b[0] for b in buffer), buffer[0][1], buffer[-1][2]))
            buffer, buf_tokens = [], 0

    for btext, bstart, bend in spans:
        block_tokens = _count_tokens(btext, enc)
        if block_tokens > size_tokens:
            flush()
            for piece in _window_by_tokens(btext, size_tokens, overlap_tokens, enc):
                results.append((piece, bstart, bend))
            continue
        if buffer and buf_tokens + block_tokens > size_tokens:
            flush()
        buffer.append((btext, bstart, bend))
        buf_tokens += block_tokens
    flush()
    return results


def _overlap_tail(text: str, overlap_tokens: int, enc) -> str:
    """Trailing ~`overlap_tokens` slice of `text`, cut on a sentence boundary so the context
    carried into the next chunk reads cleanly. Empty when overlap is disabled."""
    if overlap_tokens <= 0 or not text.strip():
        return ""
    sentences = _split_sentences(text)
    tail: list[str] = []
    tokens = 0
    for sentence in reversed(sentences):
        stoks = _count_tokens(sentence, enc)
        if tail and tokens + stoks > overlap_tokens:
            break
        tail.insert(0, sentence)
        tokens += stoks
        if tokens >= overlap_tokens:
            break
    # A single-chunk page has nothing to carry into — its whole text would just be its own
    # overlap, so return nothing in that case.
    return " ".join(tail).strip() if len(tail) < len(sentences) else ""


def _apply_prose_overlap(
    packed: list[tuple[str, int, int]], overlap_tokens: int, enc
) -> list[tuple[str, int, int]]:
    """Prepend each chunk (after the first) with the trailing sentences of the previous
    chunk, so a fact split across a paragraph boundary survives in at least one chunk. The
    offset start is pulled back to reflect the carried region, so overlapping chunks report
    overlapping source spans — the correct meaning of overlap."""
    if overlap_tokens <= 0 or len(packed) < 2:
        return packed
    out: list[tuple[str, int, int]] = []
    for i, (text, start, end) in enumerate(packed):
        if i > 0:
            prev_text, _, prev_end = packed[i - 1]
            tail = _overlap_tail(prev_text, overlap_tokens, enc)
            if tail and not text.startswith(tail):
                text = f"{tail}\n\n{text}"
                start = max(0, prev_end - len(tail))
        out.append((text, start, end))
    return out


def _raw_token_window(text: str, size_tokens: int, overlap_tokens: int, enc) -> list[str]:
    """Last-resort cut at a raw token/char offset — used only when a block has no sentence
    boundary to split on (a single run-on sentence, or text without terminal punctuation)."""
    if enc is not None:
        ids = enc.encode(text)
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
    pieces = []
    start = 0
    while start < len(text):
        end = min(start + char_size, len(text))
        pieces.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - char_overlap)
    return pieces


def _window_by_tokens(text: str, size_tokens: int, overlap_tokens: int, enc) -> list[str]:
    """Split an oversized block. Tries sentence boundaries first (via `_pack_blocks`, so
    multiple undersized sentences still get packed together rather than one-per-chunk);
    only falls back to a raw token/char cut — which can land mid-sentence — when the block
    has no sentence boundary to split on at all."""
    if _count_tokens(text, enc) <= size_tokens:
        return [text]
    sentences = _split_sentences(text)
    if len(sentences) > 1:
        return _pack_blocks(sentences, size_tokens, overlap_tokens, enc, separator=" ")
    return _raw_token_window(text, size_tokens, overlap_tokens, enc)


def _pack_blocks(
    blocks: list[str], size_tokens: int, overlap_tokens: int, enc, *, separator: str = "\n\n"
) -> list[str]:
    """Greedily pack text blocks into token-bounded windows; oversized blocks get token-windowed."""
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0
    for block in blocks:
        block_tokens = _count_tokens(block, enc)
        if block_tokens > size_tokens:
            if buffer:
                chunks.append(separator.join(buffer))
                buffer, buffer_tokens = [], 0
            chunks.extend(_window_by_tokens(block, size_tokens, overlap_tokens, enc))
            continue
        if buffer and buffer_tokens + block_tokens > size_tokens:
            chunks.append(separator.join(buffer))
            buffer, buffer_tokens = [], 0
        buffer.append(block)
        buffer_tokens += block_tokens
    if buffer:
        chunks.append(separator.join(buffer))
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank-line paragraph boundaries, dropping empty blocks. Shared by every
    chunker that packs prose into token windows paragraph-first."""
    blocks = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return blocks or [text]


def chunk_prose_recursive(
    pages: list[ParsedPage], size_tokens: int = 600, overlap_tokens: int = 80
) -> list[ChunkPiece]:
    """Split on paragraph boundaries first, token-window any oversized paragraph. Offsets
    are real positions in the page text, and consecutive chunks carry a sentence-level
    overlap so a fact spanning a paragraph boundary isn't lost between chunks."""
    enc = _get_encoding()
    pieces: list[ChunkPiece] = []
    for page in pages:
        text = (page.text or "").strip()
        if not text:
            continue
        packed = _pack_spans(_paragraph_spans(text), size_tokens, overlap_tokens, enc)
        for chunk_text, start, end in _apply_prose_overlap(packed, overlap_tokens, enc):
            pieces.append(
                ChunkPiece(
                    page=page.page,
                    offset_start=start,
                    offset_end=end,
                    text=chunk_text,
                )
            )
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


def _group_rows_adaptively(
    data_lines: list[str],
    header: str,
    title: str,
    rows_per_chunk: int,
    size_tokens: int,
    enc,
) -> list[list[str]]:
    """Group data rows by a token budget, capped by `rows_per_chunk` rows per group —
    not a fixed row count alone. A fixed row count either fragments a table of short rows
    into needlessly many small chunks, or lets a table with long cell values (e.g. a
    free-text notes column) blow well past a reasonable chunk size. `rows_per_chunk` stays
    as an upper bound so a chunk of very short rows doesn't grow large enough to make its
    row-range citation impractically broad."""
    overhead = _count_tokens("\n".join(([title] if title else []) + [header]), enc)
    budget = max(size_tokens - overhead, size_tokens // 4)
    groups: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for line in data_lines:
        line_tokens = _count_tokens(line, enc)
        if current and (len(current) >= rows_per_chunk or current_tokens + line_tokens > budget):
            groups.append(current)
            current, current_tokens = [], 0
        current.append(line)
        current_tokens += line_tokens
    if current:
        groups.append(current)
    return groups


def _chunk_one_table(
    page: ParsedPage,
    title: str,
    header: str,
    data_lines: list[str],
    rows_per_chunk: int,
    size_tokens: int = 600,
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

    enc = _get_encoding()
    offset = 0
    start = 0
    for group in _group_rows_adaptively(data_lines, header, title, rows_per_chunk, size_tokens, enc):
        body_lines = ([title] if title else []) + [header] + group
        chunk_text = "\n".join(body_lines)
        end = start + len(group)
        pieces.append(
            ChunkPiece(
                page=page.page,
                offset_start=offset,
                offset_end=offset + len(chunk_text),
                text=chunk_text,
                metadata={"row_range": [start, end]},
            )
        )
        offset += len(chunk_text)
        start = end
    return pieces


def chunk_inventory_rows(
    pages: list[ParsedPage], rows_per_chunk: int = 20, size_tokens: int = 600
) -> list[ChunkPiece]:
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
        pieces.extend(_chunk_one_table(page, title, header, data_lines, rows_per_chunk, size_tokens))
    return pieces


@dataclass(frozen=True)
class _ProseSegment:
    text: str


@dataclass(frozen=True)
class _TableSegment:
    header: str
    data_lines: list[str]


def _split_prose_and_tables(
    text: str,
) -> list[tuple[_ProseSegment | _TableSegment, int]]:
    """Split page text into ordered (segment, start_offset) pairs around any
    `<<TABLE>>...<<END_TABLE>>`-marked blocks (from `_parse_docx` or the PDF table
    extractor), preserving original document order and each segment's real start offset in
    the page so downstream chunks carry genuine offsets."""
    segments: list[tuple[_ProseSegment | _TableSegment, int]] = []
    last_end = 0
    for m in _TABLE_BLOCK.finditer(text):
        raw_before = text[last_end : m.start()]
        before = raw_before.strip()
        if before:
            segments.append((_ProseSegment(before), last_end + raw_before.find(before[:1])))
        table_lines = [line for line in m.group(1).split("\n") if line.strip()]
        if len(table_lines) >= 2:
            segments.append((_TableSegment(table_lines[0], table_lines[1:]), m.start()))
        elif table_lines:
            segments.append((_ProseSegment(table_lines[0]), m.start()))
        last_end = m.end()
    raw_tail = text[last_end:]
    tail = raw_tail.strip()
    if tail:
        segments.append((_ProseSegment(tail), last_end + raw_tail.find(tail[:1])))
    return segments or [(_ProseSegment(text), 0)]


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
        for segment, seg_start in _split_prose_and_tables(text):
            if isinstance(segment, _ProseSegment):
                packed = _pack_spans(
                    _paragraph_spans(segment.text, base=seg_start), size_tokens, overlap_tokens, enc
                )
                for chunk_text, start, end in _apply_prose_overlap(packed, overlap_tokens, enc):
                    pieces.append(
                        ChunkPiece(
                            page=page.page,
                            offset_start=start,
                            offset_end=end,
                            text=chunk_text,
                        )
                    )
            else:
                offset = seg_start
                for table_piece in _chunk_one_table(
                    page, "", segment.header, segment.data_lines, rows_per_chunk, size_tokens
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
    blocks = _split_paragraphs(body)
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


def _is_plain_prose(piece: ChunkPiece) -> bool:
    """True for a piece with no structural anchor of its own (not a row-group, Q&A pair,
    computed summary, or manifest-file piece) — the case where a chunk pulled out of
    document order is hardest to place, and most likely to contain a dangling reference
    like "the above servers" with nothing to disambiguate it."""
    meta = piece.metadata
    return not (
        "row_range" in meta
        or "qa_index" in meta
        or "file_path" in meta
        or meta.get("kind") == "table_summary"
    )


def _structural_label(piece: ChunkPiece, doc_type: DocumentType) -> str:
    """A short breadcrumb built only from doc-type/structural metadata — never raw
    document content (that includes the filename, which is user-controlled) — so it's
    safe to surface directly in the LLM extraction prompt without needing injection
    scanning: there's nothing in it an uploaded file could have influenced."""
    meta = piece.metadata
    if meta.get("kind") == "table_summary":
        return f"{doc_type.value} — computed summary"
    if "row_range" in meta:
        start, end = meta["row_range"]
        anchor = f"rows {start + 1}-{end}"
        if meta.get("embedded_table"):
            anchor = f"embedded table, {anchor}"
        return f"{doc_type.value} — {anchor}"
    if "qa_index" in meta and meta["qa_index"] >= 0:
        return f"{doc_type.value} — question {meta['qa_index'] + 1}"
    if "file_path" in meta:
        return f"{doc_type.value} — {meta['file_path']}"
    return doc_type.value


_LOOKBACK_CHARS = 160


def _annotate_with_context(pieces: list[ChunkPiece], doc_type: DocumentType) -> None:
    """Mutate each piece's metadata in place with two distinct context signals:

    - `section_title`: the safe structural breadcrumb above, plus a `part N of M`
      position for plain-prose pieces. Forwarded all the way to the LLM extraction
      prompt (`chunks_to_payload` → `_spotlight_chunks`) so the model has some idea what
      it's looking at even when a chunk is retrieved on its own.
    - `embed_context`: for a plain-prose piece only, a short tail snippet of the
      *previous* plain-prose piece's own text. This is raw document content, so it's
      retrieval-only (folded into the text handed to the embedder/BM25 in `search.py`)
      and deliberately never forwarded into the LLM-facing payload — it would otherwise
      be a second, unscanned copy of chunk text reaching the model. It exists so a
      pronoun-only chunk like "the above servers are production-critical" still has a
      chance to score well against a query naming the actual servers, without widening
      the prompt-injection surface to fix it.
    """
    prose_order = [i for i, p in enumerate(pieces) if _is_plain_prose(p)]
    prose_position = {idx: pos for pos, idx in enumerate(prose_order)}
    for i, piece in enumerate(pieces):
        label = _structural_label(piece, doc_type)
        if i in prose_position:
            pos = prose_position[i]
            label = f"{label} — part {pos + 1} of {len(prose_order)}"
            if pos > 0:
                prev_text = pieces[prose_order[pos - 1]].text
                tail = prev_text[-_LOOKBACK_CHARS:].strip()
                if tail:
                    piece.metadata["embed_context"] = tail
        piece.metadata["section_title"] = label


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
            pieces = chunk_inventory_rows(pages, settings.chunk_inventory_rows, settings.chunk_size_tokens)
        elif doc_type == DocumentType.questionnaire:
            pieces = chunk_questionnaire_pairs(pages)
        else:
            pieces = chunk_prose_with_tables(
                pages, settings.chunk_size_tokens, settings.chunk_overlap_tokens, settings.chunk_inventory_rows
            )
        _annotate_with_context(pieces, doc_type)
        return pieces
