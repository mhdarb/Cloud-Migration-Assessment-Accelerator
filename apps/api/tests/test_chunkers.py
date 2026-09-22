from app.models.entities import DocumentType
from app.services.chunkers import (
    DocumentChunker,
    check_table_shape,
    chunk_code_manifest_file,
    chunk_inventory_rows,
    chunk_prose_recursive,
    chunk_questionnaire_pairs,
)
from app.services.parsers import ParsedPage
from app.services.search import reciprocal_rank_fusion


def test_chunk_prose_recursive_splits_and_respects_token_budget():
    long_para = "word " * 2000  # forces token-windowing of a single oversized block
    text = f"Intro paragraph.\n\n{long_para}\n\nClosing paragraph."
    pieces = chunk_prose_recursive([ParsedPage(page=1, text=text)], size_tokens=50, overlap_tokens=5)
    assert len(pieces) > 1
    assert all(p.text.strip() for p in pieces)
    assert all(p.page == 1 for p in pieces)


def test_chunk_prose_recursive_skips_empty_pages():
    pieces = chunk_prose_recursive([ParsedPage(page=1, text="   ")])
    assert pieces == []


def test_chunk_inventory_rows_repeats_header_and_groups_rows():
    header = "name | os | vcpu"
    rows = [f"server-{i} | Linux | 4" for i in range(25)]
    text = "\n".join(["# Sheet: Servers", header, *rows])
    pieces = chunk_inventory_rows([ParsedPage(page=1, text=text)], rows_per_chunk=10)
    # "vcpu" maps to the canonical numeric field "vcpus", so a summary chunk is expected
    # in addition to the 3 row-groups (25 rows / 10 per chunk).
    summary = [p for p in pieces if p.metadata.get("kind") == "table_summary"]
    row_groups = [p for p in pieces if "row_range" in p.metadata]
    assert len(summary) == 1
    assert len(row_groups) == 3
    for piece in row_groups:
        assert "# Sheet: Servers" in piece.text
        assert header in piece.text
    assert row_groups[0].metadata["row_range"] == [0, 10]
    assert row_groups[-1].metadata["row_range"] == [20, 25]


def test_chunk_inventory_rows_summary_computes_correct_aggregates():
    header = "name | os | vcpu | memory_gb"
    rows = [
        "server-1 | Linux | 4 | 16",
        "server-2 | Linux | 8 | 32",
        "server-3 | Linux |  | 8",  # missing vcpu on purpose
    ]
    text = "\n".join(["# Sheet: Servers", header, *rows])
    pieces = chunk_inventory_rows([ParsedPage(page=1, text=text)], rows_per_chunk=10)
    summary = next(p for p in pieces if p.metadata.get("kind") == "table_summary")
    assert summary.metadata["row_count"] == 3
    assert "vcpus: sum=12" in summary.text  # only 2 of 3 rows had a vcpu value
    assert "count=2/3" in summary.text
    assert "memory_gb: sum=56" in summary.text
    assert "count=3/3" in summary.text


def test_chunk_inventory_rows_no_summary_without_numeric_columns():
    header = "name | owner | notes"
    rows = ["server-1 | Alice | fine", "server-2 | Bob | fine"]
    text = "\n".join(["# Sheet: Servers", header, *rows])
    pieces = chunk_inventory_rows([ParsedPage(page=1, text=text)], rows_per_chunk=10)
    assert not any(p.metadata.get("kind") == "table_summary" for p in pieces)


def test_shape_guard_passes_clean_table():
    header = "server | os | vcpu"
    rows = [f"server-{i} | Linux | 4" for i in range(5)]
    assert check_table_shape(header, rows).ok


def test_shape_guard_flags_inconsistent_column_counts():
    header = "server | os | vcpu"
    rows = ["s1 | Linux | 4", "s2 | Linux", "s3 | Linux | 4 | extra", "s4 | Linux | 4"]
    result = check_table_shape(header, rows)
    assert not result.ok
    assert "column count" in result.reason


def test_shape_guard_flags_hidden_second_header_row():
    header = "server | os | vcpu"
    # A second header-like row (non-numeric) sitting among otherwise-numeric data rows.
    rows = [
        "Hostname | Operating System | Cores",
        "server-1 | Linux | 4",
        "server-2 | Linux | 8",
        "server-3 | Linux | 4",
    ]
    result = check_table_shape(header, rows)
    assert not result.ok
    assert "second header" in result.reason


def test_shape_guard_failure_falls_back_to_prose_and_is_tagged():
    header = "server | os | vcpu"
    rows = ["s1 | Linux | 4", "s2 | Linux", "s3 | Linux | 4 | extra"]
    text = "\n".join(["# Sheet: Weird", header, *rows])
    pieces = chunk_inventory_rows([ParsedPage(page=1, text=text)], rows_per_chunk=10)
    assert pieces  # text isn't lost, just not row-grouped
    assert all(p.metadata.get("shape_guard_failed") for p in pieces)
    assert all(p.metadata.get("shape_guard_reason") for p in pieces)
    assert not any("row_range" in p.metadata for p in pieces)


def test_chunk_questionnaire_pairs_splits_on_question_boundaries():
    text = "Preamble text.\nQ1: What is the app criticality?\nHigh.\nQ2: Any compliance needs?\nPCI DSS."
    pieces = chunk_questionnaire_pairs([ParsedPage(page=1, text=text)])
    qa_indexed = [p for p in pieces if p.metadata.get("qa_index", -1) >= 0]
    assert len(qa_indexed) == 2
    assert "What is the app criticality" in qa_indexed[0].text
    assert "Any compliance needs" in qa_indexed[1].text


def test_chunk_code_manifest_file_prefixes_only_first_piece():
    text = "\n\n".join([f"line block {i} " * 200 for i in range(3)])
    pieces = chunk_code_manifest_file("package.json", text, size_tokens=50, overlap_tokens=5)
    assert len(pieces) > 1
    assert pieces[0].text.startswith("# FILE: package.json\n")
    assert not pieces[1].text.startswith("# FILE:")
    assert all(p.metadata["file_path"] == "package.json" for p in pieces)
    assert [p.metadata["part"] for p in pieces] == list(range(len(pieces)))


def test_document_chunker_routes_by_doc_type():
    chunker = DocumentChunker()
    inv_pieces = chunker.chunk(
        [ParsedPage(page=1, text="# Sheet: S\nheader\n" + "\n".join(f"row{i}" for i in range(5)))],
        doc_type=DocumentType.inventory,
        filename="cmdb.xlsx",
    )
    assert inv_pieces and "row_range" in inv_pieces[0].metadata

    prose_pieces = chunker.chunk(
        [ParsedPage(page=1, text="Some architecture prose about the estate.")],
        doc_type=DocumentType.architecture,
        filename="arch.docx",
    )
    assert prose_pieces and "row_range" not in prose_pieces[0].metadata


def test_reciprocal_rank_fusion_prefers_agreement_across_lists():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]], k=60)
    assert fused[0] in {"a", "b"}
    assert set(fused) == {"a", "b", "c", "d"}


def test_reciprocal_rank_fusion_empty_input():
    assert reciprocal_rank_fusion([]) == []
