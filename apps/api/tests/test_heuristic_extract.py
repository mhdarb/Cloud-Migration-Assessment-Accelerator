from app.models.entities import Chunk, Document, DocumentType
from app.services.heuristic_extract import chunks_to_payload, extract_nfr_claims, heuristic_extract


def _add_doc(db_session, assessment, filename="doc.docx"):
    doc = Document(
        assessment_id=assessment.id,
        filename=filename,
        content_type="application/docx",
        storage_path="/tmp/doc.docx",
        doc_type=DocumentType.architecture,
    )
    db_session.add(doc)
    db_session.flush()
    return doc


def _add_chunk(db_session, assessment, doc, index, text):
    chunk = Chunk(
        assessment_id=assessment.id,
        document_id=doc.id,
        chunk_index=index,
        page=1,
        offset_start=0,
        offset_end=len(text),
        text=text,
    )
    db_session.add(chunk)
    db_session.flush()
    return chunk


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


def test_chunks_to_payload_forwards_section_title_set_by_the_chunker():
    chunk = Chunk(
        id="chunk-4",
        assessment_id="a",
        document_id="doc-4",
        chunk_index=0,
        text="app-01 | RHEL 8 | 4",
        metadata_json={"row_range": [0, 1], "section_title": "inventory — rows 1-1"},
    )
    payload = chunks_to_payload([chunk])
    assert payload[0]["section_title"] == "inventory — rows 1-1"
    assert payload[0]["row_range"] == [0, 1]


def test_extract_nfr_claims_strips_leading_pipe_from_table_cell():
    """A DOCX/XLSX table row renders as "label | value" (parsers._parse_docx /
    _parse_xlsx) -- the old `.strip(" .;:")` didn't include "|", so a label like
    "concurrent users" (whose regex separator only matches `[:\\s]`, not the "|" itself)
    left the pipe stuck to the front of the captured value."""
    claims = extract_nfr_claims("concurrent users | ~9,500 (seasonal)", "chunk-1")
    scalability = next(c for c in claims if c.attribute == "scalability")
    assert scalability.value == "~9,500 (seasonal)"


def test_extract_nfr_claims_splits_combined_rto_rpo_table_cell():
    """A single table row stating both RTO and RPO together (e.g. "RTO / RPO | 1 hour /
    30 minutes") used to make the generic RTO pattern swallow the entire ragged
    remainder -- "/ RPO | 1 hour / 30 minutes" -- as its own value. It must now split
    cleanly into two separate, correct claims and nothing else."""
    claims = extract_nfr_claims("RTO / RPO | 1 hour / 30 minutes", "chunk-1")
    by_attr = {c.attribute: c.value for c in claims}
    assert by_attr == {"rto": "1 hour", "rpo": "30 minutes"}


def test_extract_nfr_claims_standalone_rto_and_rpo_are_unaffected():
    """The combined-cell handling must not change behavior for the (more common) case
    where RTO and RPO are stated as separate lines, each already terminated the same way
    the pre-existing regex expects (a comma or newline, not a period -- that boundary is
    a separate, pre-existing limitation of the generic pattern, not part of this fix)."""
    claims = extract_nfr_claims("RTO: 4 hours,\nRPO: 1 hour,\n", "chunk-1")
    by_attr = {c.attribute: c.value for c in claims}
    assert by_attr["rto"] == "4 hours"
    assert by_attr["rpo"] == "1 hour"


def test_extract_nfr_claims_availability_from_real_architecture_table_row():
    """Regression check against the real row format that triggered this bug report."""
    text = (
        "Requirement | Target Availability SLA | 99.95% Recovery Time Objective (RTO) | "
        "1 hour Recovery Point Objective (RPO) | 30 minutes Peak concurrency | ~9,500 users"
    )
    claims = extract_nfr_claims(text, "chunk-1")
    availability = next(c for c in claims if c.attribute == "availability")
    assert availability.value == "99.95%"
    assert all("|" not in c.value[:1] for c in claims)  # no claim starts with a stray pipe


def test_chunks_to_payload_adds_parent_context_from_same_document_neighbors(
    db_session, assessment
):
    doc = _add_doc(db_session, assessment)
    before = _add_chunk(db_session, assessment, doc, 0, "Intro paragraph about the estate.")
    target = _add_chunk(db_session, assessment, doc, 1, "The above servers are production-critical.")
    after = _add_chunk(db_session, assessment, doc, 2, "Closing paragraph about the estate.")
    db_session.commit()

    payload = chunks_to_payload([target], db=db_session)
    item = payload[0]
    assert "parent_context" in item
    assert before.text in item["parent_context"]
    assert after.text in item["parent_context"]
    assert "[before]" in item["parent_context"]
    assert "[after]" in item["parent_context"]
    # The child's own text is unaffected -- citation-quote validation still checks
    # against this, not the parent context.
    assert item["text"] == target.text


def test_chunks_to_payload_excludes_siblings_from_other_documents(db_session, assessment):
    doc_a = _add_doc(db_session, assessment, "a.docx")
    doc_b = _add_doc(db_session, assessment, "b.docx")
    target = _add_chunk(db_session, assessment, doc_a, 0, "Chunk in document A.")
    _add_chunk(db_session, assessment, doc_b, 0, "Chunk in document B, same index.")
    db_session.commit()

    payload = chunks_to_payload([target], db=db_session)
    assert "parent_context" not in payload[0]


def test_chunks_to_payload_respects_radius(db_session, assessment):
    doc = _add_doc(db_session, assessment)
    target = _add_chunk(db_session, assessment, doc, 5, "Target chunk.")
    _add_chunk(db_session, assessment, doc, 3, "Two positions before -- out of radius 1.")
    near = _add_chunk(db_session, assessment, doc, 4, "One position before -- in radius.")
    db_session.commit()

    payload = chunks_to_payload([target], db=db_session)
    assert near.text in payload[0]["parent_context"]
    assert "out of radius" not in payload[0]["parent_context"]


def test_chunks_to_payload_omits_parent_context_without_db(db_session, assessment):
    doc = _add_doc(db_session, assessment)
    _add_chunk(db_session, assessment, doc, 0, "Sibling.")
    target = _add_chunk(db_session, assessment, doc, 1, "Target.")
    db_session.commit()

    payload = chunks_to_payload([target])  # no db -- existing callers unaffected
    assert "parent_context" not in payload[0]


def test_chunks_to_payload_respects_parent_context_enabled_setting(
    db_session, assessment, monkeypatch
):
    from app.config import get_settings

    monkeypatch.setenv("PARENT_CONTEXT_ENABLED", "false")
    get_settings.cache_clear()
    doc = _add_doc(db_session, assessment)
    _add_chunk(db_session, assessment, doc, 0, "Sibling.")
    target = _add_chunk(db_session, assessment, doc, 1, "Target.")
    db_session.commit()

    payload = chunks_to_payload([target], db=db_session)
    assert "parent_context" not in payload[0]
