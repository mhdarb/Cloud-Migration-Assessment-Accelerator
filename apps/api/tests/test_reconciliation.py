from app.models.entities import Document, DocumentType
from app.schemas.api import ExtractedClaim, ExtractionResult
from app.services.reconciliation import persist_extraction


def test_precedence_selects_inventory_over_architecture(db_session, assessment):
    inv = Document(
        assessment_id=assessment.id,
        filename="cmdb.xlsx",
        content_type="application/vnd.ms-excel",
        storage_path="/tmp/x",
        doc_type=DocumentType.inventory,
        precedence=100,
    )
    arch = Document(
        assessment_id=assessment.id,
        filename="arch.docx",
        content_type="application/docx",
        storage_path="/tmp/y",
        doc_type=DocumentType.architecture,
        precedence=80,
    )
    db_session.add_all([inv, arch])
    db_session.flush()

    from app.models.entities import Chunk

    c_inv = Chunk(
        assessment_id=assessment.id,
        document_id=inv.id,
        chunk_index=0,
        page=1,
        offset_start=0,
        offset_end=10,
        text="server app-01 runs Windows Server 2019",
    )
    c_arch = Chunk(
        assessment_id=assessment.id,
        document_id=arch.id,
        chunk_index=0,
        page=1,
        offset_start=0,
        offset_end=10,
        text="server app-01 runs RHEL 8",
    )
    db_session.add_all([c_inv, c_arch])
    db_session.commit()

    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                entity_type="server",
                entity_key="app-01",
                attribute="os",
                value="Windows Server 2019",
                confidence=0.8,
                chunk_ids=[c_inv.id],
                evidence_quote="Windows Server 2019",
            ),
            ExtractedClaim(
                entity_type="server",
                entity_key="app-01",
                attribute="os",
                value="RHEL 8",
                confidence=0.95,
                chunk_ids=[c_arch.id],
                evidence_quote="RHEL 8",
            ),
        ]
    )
    persist_extraction(db_session, assessment.id, result)

    from app.models.entities import Claim, Conflict

    selected = (
        db_session.query(Claim)
        .filter(
            Claim.assessment_id == assessment.id,
            Claim.attribute == "os",
            Claim.is_selected.is_(True),
        )
        .one()
    )
    assert selected.value == "Windows Server 2019"
    conflicts = (
        db_session.query(Conflict).filter(Conflict.assessment_id == assessment.id).all()
    )
    assert len(conflicts) == 1
