"""Regression over the generated 'stress' estate (`sample-data/stress`).

These fixtures deliberately cover awkward real-world shapes (scanned PDF, merged data
cells, ragged CSV, nested-key JSON, markdown/embedded tables, legacy .doc, multilingual
text). The invariant this suite protects is the one that makes ingest trustworthy:

    every document either yields usable structured chunks, or degrades to a VISIBLE
    signal (a parse rejection or a surfaced warning) — never silent data loss.

The corpus is regenerated into a tmp dir from `scripts/generate_stress_fixtures.py`, so
the test is hermetic and never depends on committed binary fixtures.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.services.chunkers import DocumentChunker
from app.services.parsers import TABLE_BLOCK_START, parse_file

_GEN = Path(__file__).resolve().parents[3] / "scripts" / "generate_stress_fixtures.py"


@pytest.fixture(scope="module")
def estate(tmp_path_factory) -> Path:
    """Generate the stress corpus into a temp dir and return it."""
    if not _GEN.exists():  # pragma: no cover - defensive
        pytest.skip("generator script not found")
    spec = importlib.util.spec_from_file_location("_stress_gen", _GEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path_factory.mktemp("stress")
    module.OUT = out
    try:
        module.main()
    except ModuleNotFoundError as exc:  # pragma: no cover - reportlab/PIL missing
        pytest.skip(f"fixture generator dependency missing: {exc}")
    return out


def _parse(estate: Path, name: str):
    return parse_file(str(estate / name), name)


def test_no_document_is_silently_empty(estate: Path):
    """Every generated file either parses to real chunks, or is rejected, or carries a
    warning. Nothing is accepted as zero content with no trace."""
    for path in sorted(estate.iterdir()):
        if path.name == "README.md" or not path.is_file():
            continue
        try:
            result = parse_file(str(path), path.name)
        except ValueError:
            continue  # a clear rejection is a visible signal
        pieces = DocumentChunker().chunk(result.pages, doc_type=result.doc_type, filename=path.name)
        chars = sum(len(p.text.strip()) for p in result.pages)
        assert pieces or result.warnings or chars == 0 and result.warnings, (
            f"{path.name} produced neither chunks nor a warning"
        )
        if chars < 8:
            assert result.warnings, f"{path.name} is near-empty but surfaced no warning"


def test_scanned_pdf_warns(estate: Path):
    result = _parse(estate, "scanned-runbook.pdf")
    assert result.warnings
    assert "scanned" in result.warnings[0].lower() or "no extractable text" in result.warnings[0].lower()


def test_empty_text_warns(estate: Path):
    assert _parse(estate, "empty-notes.txt").warnings


def test_legacy_doc_rejected(estate: Path):
    with pytest.raises(ValueError, match="legacy binary .doc"):
        _parse(estate, "legacy-spec.doc")


def test_ruled_pdf_table_recovered(estate: Path):
    joined = "\n".join(p.text for p in _parse(estate, "architecture-brief.pdf").pages)
    assert TABLE_BLOCK_START in joined
    assert "ord-api-01 | Order API | RHEL 9 | 8 | 32" in joined


def test_two_column_pdf_not_mangled_into_table(estate: Path):
    """The two-column report must read as prose, not be carved into a bogus table."""
    result = _parse(estate, "capacity-report.pdf")
    joined = "\n".join(p.text for p in result.pages)
    assert TABLE_BLOCK_START not in joined
    assert "Order Management" in joined and "Fleet Tracking" in joined


def test_xlsx_merged_data_cells_and_summary(estate: Path):
    result = _parse(estate, "cmdb-export.xlsx")
    servers = result.pages[0].text
    # Merged 'datacenter' value propagated to every row, not just the anchor.
    assert servers.count("DC-North") >= 3
    assert servers.count("DC-South") >= 2
    pieces = DocumentChunker().chunk(result.pages, doc_type=result.doc_type, filename="cmdb-export.xlsx")
    assert any(p.metadata.get("kind") == "table_summary" for p in pieces)
    # The Network sheet's hidden second header trips the shape guard -> visible fallback.
    assert any(p.metadata.get("shape_guard_failed") for p in pieces)


def test_ragged_csv_hits_shape_guard(estate: Path):
    result = _parse(estate, "messy-inventory.csv")
    pieces = DocumentChunker().chunk(result.pages, doc_type=result.doc_type, filename="messy-inventory.csv")
    assert pieces  # not lost
    assert any(p.metadata.get("shape_guard_failed") for p in pieces)


def test_json_records_under_nested_key(estate: Path):
    text = _parse(estate, "cloud-assets.json").pages[0].text
    assert "asset_id | name | vcpu | memory_gb | os" in text
    assert "vm-101 | ord-api-01 | 8 | 32 | RHEL 9" in text


def test_markdown_table_becomes_block(estate: Path):
    text = _parse(estate, "migration-runbook.md").pages[0].text
    assert TABLE_BLOCK_START in text
    assert "ord-api-01 | primary | DC-North | 1" in text


def test_docx_embedded_table_chunked(estate: Path):
    result = _parse(estate, "solution-architecture.docx")
    pieces = DocumentChunker().chunk(result.pages, doc_type=result.doc_type, filename="solution-architecture.docx")
    assert any(p.metadata.get("embedded_table") for p in pieces)


def test_questionnaire_pairs(estate: Path):
    result = _parse(estate, "discovery-questionnaire.docx")
    pieces = DocumentChunker().chunk(result.pages, doc_type=result.doc_type, filename="discovery-questionnaire.docx")
    assert sum(1 for p in pieces if p.metadata.get("qa_index", -1) >= 0) == 4


def test_fleet_csv_row_groups_and_summary(estate: Path):
    result = _parse(estate, "fleet-inventory.csv")
    pieces = DocumentChunker().chunk(result.pages, doc_type=result.doc_type, filename="fleet-inventory.csv")
    assert sum(1 for p in pieces if "row_range" in p.metadata) >= 2  # 60 rows -> multiple groups
    assert any(p.metadata.get("kind") == "table_summary" for p in pieces)
