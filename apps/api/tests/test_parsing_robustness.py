"""Robustness tests for the parsing + chunking front door: the goal is that any
document either produces usable structured chunks, or degrades to a *visible* gap —
never silent data loss, an OOM, or a runaway chunk count."""

from __future__ import annotations

import json
import sys
import types

import pytest
from openpyxl import Workbook
from pypdf import PdfWriter

from app.config import get_settings
from app.services import parsers
from app.services.chunkers import (
    _apply_prose_overlap,
    _paragraph_spans,
    _split_sentences,
    chunk_prose_recursive,
)
from app.services.parsers import (
    ParsedPage,
    ParseResult,
    _convert_markdown_tables,
    _detect_column_boundary,
    _detect_header_row,
    _find_record_list,
    _find_tables_robust,
    _forward_fill_header,
    _looks_like_table,
    _parse_xlsx_full,
    _words_to_text,
    parse_file,
)


# --------------------------------------------------------------------------- #
# Empty / scanned document detection
# --------------------------------------------------------------------------- #
def _blank_pdf(path) -> str:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as handle:
        writer.write(handle)
    return str(path)


def test_scanned_pdf_surfaces_gap_instead_of_silent_empty(tmp_path):
    result = parse_file(_blank_pdf(tmp_path / "scan.pdf"), "scan.pdf")
    assert result.page_count == 1
    assert result.warnings, "an image-only PDF must surface a warning, not zero content silently"
    assert "scanned" in result.warnings[0].lower() or "no extractable text" in result.warnings[0].lower()


def test_flag_empty_extraction_ignores_healthy_document():
    settings = get_settings()
    result = ParseResult(pages=[ParsedPage(page=1, text="a" * 500)], page_count=1)
    parsers._flag_empty_extraction(result, "ok.pdf", ".pdf", settings)
    assert result.warnings == []


# --------------------------------------------------------------------------- #
# Legacy .doc rejection
# --------------------------------------------------------------------------- #
def test_legacy_doc_ole2_rejected_clearly(tmp_path):
    doc = tmp_path / "old.doc"
    doc.write_bytes(parsers._OLE2_MAGIC + b"\x00" * 32)
    with pytest.raises(ValueError, match="legacy binary .doc"):
        parse_file(str(doc), "old.doc")


# --------------------------------------------------------------------------- #
# Markdown tables -> <<TABLE>> blocks
# --------------------------------------------------------------------------- #
def test_markdown_table_becomes_table_block():
    md = (
        "Intro text.\n\n"
        "| name | vcpu |\n"
        "| --- | --- |\n"
        "| srv-1 | 4 |\n"
        "| srv-2 | 8 |\n\n"
        "Trailing text."
    )
    converted = _convert_markdown_tables(md)
    assert parsers.TABLE_BLOCK_START in converted
    assert parsers.TABLE_BLOCK_END in converted
    assert "srv-1 | 4" in converted
    assert "--- | ---" not in converted  # separator row dropped
    assert "Intro text." in converted and "Trailing text." in converted


def test_markdown_without_tables_unchanged():
    text = "Just prose.\n\nMore prose with a | pipe but no table."
    assert _convert_markdown_tables(text) == text


# --------------------------------------------------------------------------- #
# Generalized JSON record discovery (no hard-coded "servers" key)
# --------------------------------------------------------------------------- #
def test_json_finds_records_under_any_key(tmp_path):
    payload = {"assets": [{"host": "a", "vcpu": 4}, {"host": "b", "vcpu": 8}]}
    p = tmp_path / "cmdb.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    result = parse_file(str(p), "cmdb.json")
    text = result.pages[0].text
    assert "host | vcpu" in text
    assert "a | 4" in text and "b | 8" in text


def test_json_finds_nested_records():
    data = {"data": {"items": [{"x": 1}, {"x": 2}]}}
    rows = _find_record_list(data)
    assert rows == [{"x": 1}, {"x": 2}]


def test_json_top_level_array():
    assert _find_record_list([{"a": 1}]) == [{"a": 1}]
    assert _find_record_list({"nope": 5}) is None


# --------------------------------------------------------------------------- #
# XLSX header detection + merged-header forward fill + preamble folding
# --------------------------------------------------------------------------- #
def test_forward_fill_header_spans_merged_cells():
    assert _forward_fill_header(["Compute", "", "", "Storage", ""]) == [
        "Compute",
        "Compute",
        "Compute",
        "Storage",
        "Storage",
    ]


def test_detect_header_row_skips_title_band():
    rows = [
        ["Server Inventory Q3", "", ""],
        ["", "", ""],
        ["name", "os", "vcpu"],
        ["srv-1", "Linux", "4"],
    ]
    # Row 2 (0-indexed) is the real header; the title/blank band above must be skipped.
    assert _detect_header_row(rows) == 2


def test_xlsx_preamble_folded_into_title_and_header_first(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Servers"
    ws.append(["Acme Server Inventory"])  # title band
    ws.append([])  # blank
    ws.append(["name", "os", "vcpu"])  # true header
    ws.append(["srv-1", "Linux", 4])
    path = tmp_path / "inv.xlsx"
    wb.save(path)

    result = parse_file(str(path), "inv.xlsx")
    lines = result.pages[0].text.split("\n")
    assert lines[0].startswith("# Sheet: Servers")
    assert "Acme Server Inventory" in lines[0]  # preamble folded into the single title line
    assert lines[1] == "name | os | vcpu"  # the FIRST non-title line is the real header


# --------------------------------------------------------------------------- #
# Resource caps
# --------------------------------------------------------------------------- #
def test_file_size_cap(tmp_path, monkeypatch):
    big = tmp_path / "big.csv"
    big.write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "max_file_mb", 0.0)
    with pytest.raises(ValueError, match="over the"):
        parse_file(str(big), "big.csv")


def test_page_cap_truncates_and_warns():
    result = ParseResult(
        pages=[ParsedPage(page=i, text="x") for i in range(1, 6)], page_count=5
    )
    parsers._enforce_page_cap(result, "many.pdf", type("S", (), {"max_pages_per_doc": 3})())
    assert result.page_count == 3
    assert len(result.pages) == 3
    assert result.warnings and "skipped" in result.warnings[0].lower()


def test_csv_row_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_rows_per_sheet", 3)
    rows = "\n".join(f"srv-{i},Linux" for i in range(10))
    p = tmp_path / "big.csv"
    p.write_text("name,os\n" + rows, encoding="utf-8")
    result = parse_file(str(p), "big.csv")
    assert "truncated at 3 rows" in result.pages[0].text


# --------------------------------------------------------------------------- #
# PDF via a fake pdfplumber: structured tables + OCR fallback
# --------------------------------------------------------------------------- #
class _FakeFilteredPage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self):
        return self._text


class _FakeTable:
    def __init__(self, rows, bbox=(0, 0, 10, 10)):
        self._rows = rows
        self.bbox = bbox

    def extract(self):
        return self._rows


class _FakeImage:
    original = object()


class _FakePage:
    def __init__(self, prose: str, tables: list[_FakeTable]):
        self._prose = prose
        self._tables = tables

    def find_tables(self):
        return self._tables

    def filter(self, fn):
        return _FakeFilteredPage(self._prose)

    def extract_text(self):
        return self._prose

    def to_image(self, resolution=200):
        return _FakeImage()


class _FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install_fake_pdfplumber(monkeypatch, pages):
    module = types.ModuleType("pdfplumber")
    module.open = lambda path: _FakePDF(pages)
    monkeypatch.setitem(sys.modules, "pdfplumber", module)


def test_pdf_tables_extracted_as_table_blocks(tmp_path, monkeypatch):
    page = _FakePage(
        prose="Architecture overview paragraph.",
        tables=[_FakeTable([["name", "vcpu"], ["srv-1", "4"], ["srv-2", "8"]])],
    )
    _install_fake_pdfplumber(monkeypatch, [page])
    f = tmp_path / "arch.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    result = parse_file(str(f), "arch.pdf")
    text = result.pages[0].text
    assert "Architecture overview paragraph." in text
    assert parsers.TABLE_BLOCK_START in text
    assert "srv-1 | 4" in text
    assert not result.warnings  # real content -> no empty gap


def test_pdf_ocr_fallback_when_page_empty(tmp_path, monkeypatch):
    _install_fake_pdfplumber(monkeypatch, [_FakePage(prose="", tables=[])])
    fake_tess = types.ModuleType("pytesseract")
    fake_tess.image_to_string = lambda image, lang="eng": "OCR RECOVERED TEXT"
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tess)
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)

    f = tmp_path / "scanned.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    result = parse_file(str(f), "scanned.pdf")
    assert "OCR RECOVERED TEXT" in result.pages[0].text
    assert not result.warnings


# --------------------------------------------------------------------------- #
# Chunker: CJK sentence splitting, real offsets, prose overlap
# --------------------------------------------------------------------------- #
def test_cjk_sentence_splitting():
    text = "第一句话。第二句话！第三句话？"
    sentences = _split_sentences(text)
    assert len(sentences) == 3


def test_paragraph_spans_are_real_offsets_into_source():
    text = "First para.\n\nSecond para is here."
    spans = _paragraph_spans(text)
    assert [s[0] for s in spans] == ["First para.", "Second para is here."]
    for chunk, start, end in spans:
        assert text[start:end] == chunk  # offsets point back into the real source


def test_prose_offsets_locate_in_source():
    text = "Para one about servers.\n\nPara two about databases.\n\nPara three about network."
    pieces = chunk_prose_recursive([ParsedPage(page=1, text=text)], size_tokens=8, overlap_tokens=0)
    assert len(pieces) > 1
    for piece in pieces:
        # With no overlap, every chunk is a verbatim slice of the source text.
        assert piece.text in text
        assert text[piece.offset_start : piece.offset_end] == piece.text


def test_prose_overlap_prepends_previous_tail():
    packed = [
        ("Alpha sentence one. Alpha sentence two.", 0, 39),
        ("Beta sentence one. Beta sentence two.", 41, 78),
    ]

    class _Enc:
        def encode(self, t):
            return t.split()

    result = _apply_prose_overlap(packed, overlap_tokens=4, enc=_Enc())
    assert result[0] == packed[0]  # first chunk unchanged
    assert "Beta sentence one." in result[1][0]
    assert "Alpha sentence two." in result[1][0]  # tail of previous chunk carried in
    assert result[1][1] < packed[1][1]  # start pulled back to reflect the overlap


def test_extended_script_sentence_splitting():
    assert len(_split_sentences("पहला वाक्य। दूसरा वाक्य।")) == 2  # Devanagari danda
    assert len(_split_sentences("جملة أولى؟ جملة ثانية.")) == 2  # Arabic question mark


# --------------------------------------------------------------------------- #
# Gap 1: borderless (whitespace-aligned) PDF table detection
# --------------------------------------------------------------------------- #
def test_looks_like_table_accepts_grid_rejects_prose():
    good = _FakeTable([["name", "os", "vcpu"], ["srv-1", "RHEL", "4"], ["srv-2", "RHEL", "8"]])
    assert _looks_like_table(good)
    prose = _FakeTable([["A long line of running prose here."], ["Another single-column line."]])
    assert not _looks_like_table(prose)  # single column => not a table
    two_col = _FakeTable([["Order Management", "Fleet Tracking"], ["prose left", "prose right"]])
    assert not _looks_like_table(two_col)  # 2-col unruled block treated as prose, not a table


class _FakeBorderlessPage:
    """find_tables() finds nothing on the default (line-ruled) pass, but the text-alignment
    retry surfaces a valid grid — the borderless-table case."""

    def find_tables(self, table_settings=None):
        if table_settings is None:
            return []
        return [_FakeTable([["name", "os", "vcpu"], ["srv-1", "RHEL", "4"], ["srv-2", "RHEL", "8"]])]


def test_find_tables_robust_recovers_borderless_table(monkeypatch):
    monkeypatch.setattr(get_settings(), "pdf_borderless_tables", True)  # opt-in feature
    tables = _find_tables_robust(_FakeBorderlessPage(), get_settings())
    assert len(tables) == 1
    assert _looks_like_table(tables[0])


def test_find_tables_robust_respects_disable_flag(monkeypatch):
    monkeypatch.setattr(get_settings(), "pdf_borderless_tables", False)
    assert _find_tables_robust(_FakeBorderlessPage(), get_settings()) == []


# --------------------------------------------------------------------------- #
# Gap 4: multi-column PDF reading order
# --------------------------------------------------------------------------- #
def _word(text, x0, top):
    return {"text": text, "x0": x0, "x1": x0 + 20, "top": top, "bottom": top + 10}


def test_column_boundary_detected_for_two_columns():
    words = []
    for row, top in enumerate((0, 12, 24)):
        words.append(_word(f"L{row}", 50, top))  # left column
        words.append(_word(f"R{row}", 350, top))  # right column
    boundary = _detect_column_boundary(words, page_width=600)
    assert boundary is not None
    assert 70 < boundary < 350


def test_single_column_returns_no_boundary():
    words = [_word(f"w{i}", 50, i * 12) for i in range(6)]
    assert _detect_column_boundary(words, page_width=600) is None


def test_words_to_text_reads_left_column_top_to_bottom():
    words = [_word("first", 50, 0), _word("second", 50, 12), _word("third", 50, 24)]
    assert _words_to_text(words) == "first\nsecond\nthird"


# --------------------------------------------------------------------------- #
# Gap 2: merged *data* cells expanded (full-load XLSX path)
# --------------------------------------------------------------------------- #
def test_merged_data_cells_forward_filled(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Servers"
    ws.append(["name", "datacenter", "vcpu"])
    ws.append(["srv-1", "DC-East", 4])
    ws.append(["srv-2", None, 8])
    ws.append(["srv-3", None, 16])
    ws.merge_cells("B2:B4")  # DC-East merged down across three server rows
    path = tmp_path / "merged.xlsx"
    wb.save(path)

    result = _parse_xlsx_full(str(path), get_settings())
    lines = result.pages[0].text.split("\n")
    # Every row's datacenter column is populated, not just the top-left anchor cell.
    assert "srv-1 | DC-East | 4" in lines
    assert "srv-2 | DC-East | 8" in lines
    assert "srv-3 | DC-East | 16" in lines


# --------------------------------------------------------------------------- #
# Gap 7: a REAL text-bearing PDF through the live pdfplumber path
# --------------------------------------------------------------------------- #
def _make_real_pdf_with_table(path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    story = [
        Paragraph("Architecture overview of the production estate.", styles["Normal"]),
        Spacer(1, 12),
        Table(
            [["name", "vcpu", "memory_gb"], ["srv-1", "4", "16"], ["srv-2", "8", "32"]],
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]),
        ),
    ]
    SimpleDocTemplate(str(path), pagesize=letter).build(story)
    return str(path)


def test_real_pdf_text_and_table_extracted(tmp_path):
    result = parse_file(_make_real_pdf_with_table(tmp_path / "arch.pdf"), "arch.pdf")
    joined = "\n".join(p.text for p in result.pages)
    assert "Architecture overview of the production estate." in joined
    assert parsers.TABLE_BLOCK_START in joined  # the ruled table was recovered as a block
    assert "srv-1 | 4 | 16" in joined
    assert not result.warnings  # real content -> no scanned/empty gap
