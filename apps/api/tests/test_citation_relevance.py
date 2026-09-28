"""Citations must support the answer they're shown under.

Reported: citations, when read, didn't seem relevant. Three causes, each covered here:
  1. a cited chunk was quoted by its first 240 characters (a title or a table header),
     not by the passage that supports the answer;
  2. every top-k retrieved chunk was cited, whether or not it said anything relevant;
  3. facts were matched loosely ("portal" pulled in the portal server's CPU and memory for
     "which database does the customer portal use?"; "migration" matched every NFR).
"""

from __future__ import annotations

from app.models.entities import Chunk, Claim, DependencyEdge, Document, DocumentType
from app.services.assessment_questions import (
    _attach_evidence,
    answer_custom_question,
    match_claims_to_question,
    match_edges_to_question,
)
from app.services.evidence import label_table_row, passage_terms, relevant_passage
from app.services.search import KeywordRetriever

REQUIREMENTS = (
    "Contoso Wave-1 Requirements & NFR Pack\n\nBusiness requirements document (BRD) for cloud migration "
    "readiness.\n\nSLA: 99.9% monthly availability for Billing Service customer APIs.\n"
    "RTO: 4 hours for Billing Service.\nRPO: 15 minutes for FinanceDB.\nLatency p95 under 300 ms."
)
CMDB = (
    "# Sheet: CMDB Inventory\nApplication | Server | Database | Tier | OS\n"
    "Customer Portal | app-portal-01 | PortalDB | Web | Windows Server 2019\n"
    "Billing Service | app-bill-01 | FinanceDB | App | RHEL 8\n"
)


def _claim(etype, key, attr, value, **kw) -> Claim:
    return Claim(
        id=kw.pop("id", f"{key}-{attr}"),
        assessment_id="a",
        entity_type=etype,
        entity_key=key,
        attribute=attr,
        value=value,
        confidence=0.9,
        evidence_refs=kw.pop("refs", []),
        evidence_quote=kw.pop("quote", None),
        is_selected=True,
        needs_human_review=False,
        unsupported=False,
    )


NFRS = [
    _claim("business", "migration-requirements", "sla", "99.9% monthly availability for Billing Service"),
    _claim("business", "migration-requirements", "rto", "4 hours for Billing Service"),
    _claim("business", "migration-requirements", "rpo", "15 minutes for FinanceDB"),
    _claim("business", "migration-requirements", "latency", "p95 under 300 ms"),
]
PORTAL = [
    _claim("application", "customer-portal", "name", "Customer Portal"),
    _claim("server", "app-portal-01", "os", "Windows Server 2019"),
    _claim("server", "app-portal-01", "vcpus", "4"),
    _claim("server", "app-portal-01", "memory_gb", "16"),
]


# --------------------------------------------------------------------------- #
# 1. Quote the passage that supports the answer
# --------------------------------------------------------------------------- #
def test_prose_quote_is_the_matching_sentence_not_the_title():
    passage, score = relevant_passage(REQUIREMENTS, passage_terms("What is the RTO for the billing service?"))
    assert passage.startswith("RTO: 4 hours for Billing Service.") and score >= 2
    assert "Wave-1 Requirements" not in passage


def test_table_quote_is_the_matching_row_labelled_with_its_columns():
    passage, score = relevant_passage(CMDB, passage_terms("Which database does the customer portal use?"))
    assert passage == (
        "Application: Customer Portal · Server: app-portal-01 · Database: PortalDB · Tier: Web · "
        "OS: Windows Server 2019"
    )
    assert score >= 2


def test_unrelated_chunk_scores_zero():
    assert relevant_passage(CMDB, passage_terms("What is the encryption standard at rest?"))[1] == 0


def test_raw_row_quotes_are_labelled():
    row = "Billing Service | app-bill-01 | FinanceDB | App | RHEL 8"
    assert label_table_row(CMDB, row).startswith("Application: Billing Service · Server: app-bill-01")
    assert label_table_row(CMDB, "plain sentence") == "plain sentence"


# --------------------------------------------------------------------------- #
# 3. Match facts precisely
# --------------------------------------------------------------------------- #
def test_named_attributes_and_entity_narrow_the_facts():
    got = match_claims_to_question("What is the RTO and RPO for the billing service?", NFRS)
    assert [(c.attribute, c.value) for c in got] == [("rto", "4 hours for Billing Service")]


def test_group_question_still_gets_every_service_level_fact():
    got = match_claims_to_question("What service levels and performance requirements apply?", NFRS)
    assert {c.attribute for c in got} >= {"sla", "latency"}


def test_a_partial_name_does_not_pull_in_another_entitys_specs():
    got = match_claims_to_question("Which database does the customer portal use?", PORTAL)
    assert not any(c.entity_key == "app-portal-01" for c in got)


def test_relationship_questions_are_answered_from_the_dependency_graph():
    edges = [
        DependencyEdge(
            id="e1",
            assessment_id="a",
            source_type="application",
            source_key="customer-portal",
            target_type="database",
            target_key="portaldb",
            rel_type="uses",
            confidence=0.9,
            evidence_refs=["c-cmdb"],
            needs_human_review=False,
        ),
        DependencyEdge(
            id="e2",
            assessment_id="a",
            source_type="application",
            source_key="customer-portal",
            target_type="server",
            target_key="app-portal-01",
            rel_type="hosted_on",
            confidence=0.9,
            evidence_refs=["c-cmdb"],
            needs_human_review=False,
        ),
    ]
    got = match_edges_to_question("Which database does the customer portal use?", PORTAL, edges)
    assert [e.target_key for e in got] == ["portaldb"]
    assert match_edges_to_question("What is the RTO for billing?", PORTAL, edges) == []


# --------------------------------------------------------------------------- #
# 2. Cite only what's relevant; quotes are per answer
# --------------------------------------------------------------------------- #
def _docs(db, assessment) -> dict[str, Chunk]:
    chunks = {}
    for name, dtype, text in [
        ("requirements-nfr.docx", DocumentType.requirements, REQUIREMENTS),
        ("cmdb-inventory.xlsx", DocumentType.inventory, CMDB),
        ("security.docx", DocumentType.architecture, "Encryption: TLS 1.2 in transit and AES-256 at rest."),
    ]:
        doc = Document(assessment_id=assessment.id, filename=name, storage_path=f"/tmp/{name}", doc_type=dtype)
        db.add(doc)
        db.flush()
        chunk = Chunk(assessment_id=assessment.id, document_id=doc.id, chunk_index=0, text=text)
        db.add(chunk)
        db.flush()
        chunks[name] = chunk
    db.commit()
    return chunks


def test_unrelated_retrieved_chunks_are_not_cited(db_session, assessment):
    chunks = _docs(db_session, assessment)
    answer = answer_custom_question(
        db_session,
        assessment.id,
        question_id="q1",
        question="What encryption is used at rest?",
        origin="ad_hoc",
        claims=[],
        retriever=KeywordRetriever(top_k=6),
        edges=[],
    )
    _attach_evidence(db_session, [answer], [])
    cited = {e["filename"]: e["quote"] for e in answer["evidence"]}
    assert set(cited) == {"security.docx"}
    assert cited["security.docx"].startswith("Encryption: TLS 1.2")
    assert "AES-256" in answer["answer"]
    del chunks


def test_a_facts_quote_is_shown_only_under_answers_that_use_that_fact(db_session, assessment):
    chunks = _docs(db_session, assessment)
    req = chunks["requirements-nfr.docx"].id
    rto = _claim(
        "business",
        "migration-requirements",
        "rto",
        "4 hours for Billing Service",
        id="c-rto",
        refs=[req],
        quote="RTO: 4 hours for Billing Service.",
    )
    sla = _claim(
        "business",
        "migration-requirements",
        "sla",
        "99.9% monthly availability",
        id="c-sla",
        refs=[req],
        quote="SLA: 99.9% monthly availability for Billing Service customer APIs.",
    )
    answers = [
        {
            "id": "a-rto",
            "question": "What is the RTO?",
            "claim_ids": ["c-rto"],
            "evidence_refs": [req],
            "facts": [{"entity": "business:migration-requirements", "value": "4 hours"}],
        },
        {
            "id": "a-sla",
            "question": "What is the SLA?",
            "claim_ids": ["c-sla"],
            "evidence_refs": [req],
            "facts": [{"entity": "business:migration-requirements", "value": "99.9%"}],
        },
    ]
    _attach_evidence(db_session, answers, [rto, sla])
    assert answers[0]["evidence"][0]["quote"] == "RTO: 4 hours for Billing Service."
    assert answers[1]["evidence"][0]["quote"].startswith("SLA: 99.9%")


def test_every_fact_from_the_same_source_gets_its_own_quote(db_session, assessment):
    """An answer built from several facts in one section shows each supporting sentence —
    including for a fact that has no quote of its own (its best-matching sentence)."""
    security = (
        "Security baseline.\nData residency: EU and US in-region only for payment metadata.\n"
        "Encryption: TLS 1.2 in transit and AES-256 at rest.\nCompliance: PCI-DSS for card data."
    )
    doc = Document(assessment_id=assessment.id, filename="security.docx", storage_path="/tmp/s.docx")
    db_session.add(doc)
    db_session.flush()
    chunk = Chunk(assessment_id=assessment.id, document_id=doc.id, chunk_index=0, text=security)
    db_session.add(chunk)
    db_session.commit()
    residency = _claim(
        "business",
        "migration-requirements",
        "data_residency",
        "EU and US in-region only",
        id="c-res",
        refs=[chunk.id],
        quote="Data residency: EU and US in-region only for payment metadata.",
    )
    encryption = _claim(
        "business", "migration-requirements", "encryption", "TLS 1.2 and AES-256", id="c-enc", refs=[chunk.id]
    )  # no quote of its own
    duplicate = _claim(
        "business",
        "migration-requirements",
        "data_residency",
        "EU and US in-region",
        id="c-res2",
        refs=[chunk.id],
        quote="Data residency: EU and US in-region only for payment metadata.",
    )
    answer = {
        "id": "a-sec",
        "question": "What encryption and data residency constraints apply?",
        "claim_ids": ["c-res", "c-enc", "c-res2"],
        "evidence_refs": [chunk.id],
        "facts": [],
    }
    _attach_evidence(db_session, [answer], [residency, encryption, duplicate])
    (cited,) = answer["evidence"]
    assert cited["quotes"] == [
        "Data residency: EU and US in-region only for payment metadata.",
        "Encryption: TLS 1.2 in transit and AES-256 at rest.",
    ]
    assert cited["quote"] == cited["quotes"][0]  # single-quote callers keep working


def test_clipped_fact_quotes_are_widened_to_their_line():
    from app.services.evidence import complete_quote

    text = "Compliance\nEncryption: TLS 1.2+ in transit, AES-256 at rest.\nPCI-DSS controls apply to Billing Service."
    assert complete_quote(text, "Encryption: TLS 1") == "Encryption: TLS 1.2+ in transit, AES-256 at rest."
    assert complete_quote(text, "PCI-DSS") == "PCI-DSS controls apply to Billing Service."
    assert complete_quote(text, "not in the text") == "not in the text"
    row = "Billing Service | app-bill-01 | FinanceDB | App | RHEL 8"
    assert complete_quote(CMDB, "app-bill-01") == row  # then labelled by label_table_row
