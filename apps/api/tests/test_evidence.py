from app.models.entities import Chunk, Document, DocumentType
from app.services.evidence import build_evidence_list, grounded_quote_for_chunk, resolve_evidence


def test_resolve_evidence_returns_human_readable_location(db_session, assessment):
    document = Document(
        assessment_id=assessment.id,
        filename="cmdb-inventory.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        doc_type=DocumentType.inventory,
        storage_path="/tmp/cmdb-inventory.xlsx",
        precedence=100,
    )
    db_session.add(document)
    db_session.flush()
    chunk = Chunk(
        assessment_id=assessment.id,
        document_id=document.id,
        chunk_index=0,
        page=2,
        offset_start=0,
        offset_end=24,
        text="app-01 | 8 | 32 | Linux",
    )
    db_session.add(chunk)
    db_session.commit()

    evidence = resolve_evidence(
        db_session,
        [chunk.id],
        quote="app-01 | 8 | 32 | Linux",
    )

    assert evidence == [
        {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "filename": "cmdb-inventory.xlsx",
            "doc_type": "inventory",
            "page": 2,
            "quote": "app-01 | 8 | 32 | Linux",
        }
    ]


def test_resolve_evidence_does_not_attach_quote_to_a_chunk_that_lacks_it(db_session, assessment):
    """A claim's `evidence_refs` can list more than one chunk (citations.py sometimes keeps
    every originally-cited chunk when a quote is only grounded in their *combined* text) --
    the quote must not be shown against a chunk that doesn't actually contain it, even
    though the old behavior duplicated it onto every listed chunk unconditionally."""
    document = Document(
        assessment_id=assessment.id,
        filename="architecture.docx",
        content_type="application/docx",
        doc_type=DocumentType.architecture,
        storage_path="/tmp/arch.docx",
        precedence=80,
    )
    db_session.add(document)
    db_session.flush()
    grounded_chunk = Chunk(
        assessment_id=assessment.id,
        document_id=document.id,
        chunk_index=0,
        text="Billing Service is business critical.",
    )
    other_chunk = Chunk(
        assessment_id=assessment.id,
        document_id=document.id,
        chunk_index=1,
        text="Customer Portal has no special criticality noted.",
    )
    db_session.add_all([grounded_chunk, other_chunk])
    db_session.commit()

    evidence = resolve_evidence(
        db_session,
        [grounded_chunk.id, other_chunk.id],
        quote="Billing Service is business critical.",
    )

    by_id = {item["chunk_id"]: item for item in evidence}
    assert by_id[grounded_chunk.id]["quote"] == "Billing Service is business critical."
    assert by_id[other_chunk.id]["quote"] is None


def test_grounded_quote_for_chunk_returns_none_when_not_present():
    entry = {"text": "The server runs RHEL 8."}
    assert grounded_quote_for_chunk(entry, "The server runs Windows Server 2019.") is None
    assert grounded_quote_for_chunk(entry, "The server runs RHEL 8.") == "The server runs RHEL 8."
    assert grounded_quote_for_chunk(entry, None) is None


def test_build_evidence_list_excludes_internal_text_field():
    evidence_by_chunk = {
        "c1": {
            "chunk_id": "c1",
            "document_id": "d1",
            "filename": "f.docx",
            "doc_type": "architecture",
            "page": 1,
            "text": "internal only",
        }
    }
    result = build_evidence_list(evidence_by_chunk, ["c1"], None)
    assert "text" not in result[0]
    assert result[0]["quote"] is None
