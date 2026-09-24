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
    _detect_header_row,
    _find_record_list,
    _forward_fill_header,
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
