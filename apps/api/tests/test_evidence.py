from app.models.entities import Chunk, Document, DocumentType
from app.services.evidence import resolve_evidence


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
