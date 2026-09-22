from app.models.entities import Chunk, Document, DocumentType
from app.services.heuristic_extract import heuristic_extract


def _criticality_value(result, app_key: str) -> str | None:
    for claim in result.claims:
        if claim.attribute == "business_criticality" and claim.entity_key == app_key:
            return claim.value
    return None


def test_criticality_extraction_matches_sample_data_phrasing():
    """Reproduces the exact phrasing generate_sample_data.py plants, but the extractor
    must now discover the app names generically (no hardcoded literal list)."""
    doc = Document(id="doc-1", doc_type=DocumentType.questionnaire)
    chunk = Chunk(
        id="chunk-1",
        assessment_id="a",
        document_id="doc-1",
        chunk_index=0,
        text=(
            "Application: Billing Service — core billing engine.\n"
            "Application: Identity Gateway — centralized authentication.\n"
            "Application: Customer Portal — customer self-service web application.\n"
            "A: Billing Service and Identity Gateway are business critical. "
            "Customer Portal is medium criticality."
        ),
    )
    result = heuristic_extract([chunk], {"doc-1": doc})
    assert _criticality_value(result, "billing-service") == "high"
    assert _criticality_value(result, "customer-portal") == "medium"


def test_criticality_extraction_is_generic_not_name_specific():
    """A completely different, made-up app name must also be picked up — proving the
    extractor no longer hardcodes 'Billing Service'/'Identity Gateway'/'Customer Portal'."""
    doc = Document(id="doc-2", doc_type=DocumentType.questionnaire)
    chunk = Chunk(
        id="chunk-2",
        assessment_id="a",
        document_id="doc-2",
        chunk_index=0,
        text=(
            "Application: Zephyr Logistics Tracker — real-time freight tracking.\n"
            "Zephyr Logistics Tracker is business critical with low criticality for reporting."
        ),
    )
    result = heuristic_extract([chunk], {"doc-2": doc})
    assert _criticality_value(result, "zephyr-logistics-tracker") == "low"
    # Confirms the old hardcoded names produce nothing when they're not even mentioned.
    assert _criticality_value(result, "billing-service") is None


def test_gap_signals_are_detected_in_inventory_documents_too():
    """Regression: PCI/HIPAA/criticality substring checks used to sit after a `continue`
    that skipped inventory-typed chunks entirely, so a CMDB "Notes" column mentioning
    PCI or business criticality was silently invisible. They're generic checks, not
    prose-specific, so they must run for every doc type."""
    doc = Document(id="doc-inv", doc_type=DocumentType.inventory)
    chunk = Chunk(
        id="chunk-inv",
        assessment_id="a",
        document_id="doc-inv",
        chunk_index=0,
        text=(
            "# Sheet: CMDB\n"
            "server | os | notes\n"
            "app-pay-01 | RHEL 8 | PCI-relevant workload, business critical"
        ),
    )
    result = heuristic_extract([chunk], {"doc-inv": doc})
    assert any("PCI-relevant" in g for g in result.gaps)


def test_no_criticality_claim_without_a_known_app_name():
    doc = Document(id="doc-3", doc_type=DocumentType.questionnaire)
    chunk = Chunk(
        id="chunk-3",
        assessment_id="a",
        document_id="doc-3",
        chunk_index=0,
        text="Something in this system is business critical but unnamed.",
    )
    result = heuristic_extract([chunk], {"doc-3": doc})
    assert not any(c.attribute == "business_criticality" for c in result.claims)
