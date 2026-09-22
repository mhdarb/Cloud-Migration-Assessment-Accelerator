"""Golden-set regression test over the Contoso sample-data estate.

Runs the real pipeline stages (mock LLM + local embeddings, in-process, no HTTP server)
against `sample-data/*` and checks a small set of hand-authored invariants. This is the
regression signal for the chunking/retrieval rewrite (and future contextual-retrieval /
reranker follow-ups) — see `Claude outputs/CMAA_Modernization_Review.md` §3/§8 and the
approved modernization plan.

Marked `eval` (see `pyproject.toml`) since it exercises the full pipeline rather than a
single unit, and is run as its own CI job (`pytest -q -m eval`) alongside the fast suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.models.entities import (
    Application,
    Claim,
    Conflict,
    DatabaseEntity,
    DependencyEdge,
    Document,
    DocumentType,
    Interface,
    Server,
)
from app.services.extraction import AssessmentClaimExtractor
from app.services.extraction_merge import merge_extractions
from app.services.ingest import ingest_documents
from app.services.llm_reasoning import get_grounded_prose
from app.services.pipeline import NFR_ATTRIBUTES
from app.services.providers import get_embedder, get_llm_extractor, get_retriever, get_vector_indexes
from app.services.questionnaire_extract import sync_uploaded_questions
from app.services.reconciliation import persist_extraction
from app.services.report import generate_report
from app.services.search import ChunkIndexer
from app.services.sizing import generate_recommendations

pytestmark = pytest.mark.eval

SAMPLE_DATA = Path(__file__).resolve().parents[3] / "sample-data"

SAMPLE_FILES = [
    ("cmdb-inventory.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    (
        "architecture-overview.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    (
        "requirements-nfr.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    (
        "assessment-questionnaire.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ("sample-app.zip", "application/zip"),
]


def _run_pipeline_sync(db_session, assessment_id: str) -> dict:
    """Mirror `AssessmentPipeline._execute` using the test's own in-memory session."""
    ingested = ingest_documents(db_session, assessment_id)
    embedder = get_embedder()
    indexes = get_vector_indexes()
    index_meta = ChunkIndexer(embedder, indexes).embed_and_index(db_session, assessment_id)
    retriever = get_retriever(embedder, indexes)
    llm = get_llm_extractor()
    extractor = AssessmentClaimExtractor(retriever, llm, rag_enabled=True, use_mock=True)
    extraction, rag_metrics = extractor.extract_assessment(db_session, assessment_id)
    extraction = merge_extractions([extraction, *ingested.manifest_extractions])
    prose = get_grounded_prose()
    persist_extraction(db_session, assessment_id, extraction, prose=prose)
    sync_uploaded_questions(db_session, assessment_id)
    generate_recommendations(db_session, assessment_id)
    generate_report(
        db_session, assessment_id, extraction, prose=prose, retriever=retriever, rag_metrics=rag_metrics
    )
    return {"rag_metrics": rag_metrics, "index_meta": index_meta}


def test_golden_contoso_pipeline(db_session, assessment, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("LOCAL_EMBEDDINGS", "true")
    get_settings.cache_clear()

    for filename, content_type in SAMPLE_FILES:
        path = SAMPLE_DATA / filename
        assert path.exists(), f"missing sample-data fixture: {filename}"
        db_session.add(
            Document(
                assessment_id=assessment.id,
                filename=filename,
                content_type=content_type,
                storage_path=str(path),
                doc_type=DocumentType.unknown,
            )
        )
    db_session.commit()

    outcome = _run_pipeline_sync(db_session, assessment.id)
    rag_metrics = outcome["rag_metrics"]

    assert rag_metrics["chunks_retrieved"] > 0, "expected retrieval to find chunks"
    assert rag_metrics["rag_queries"] > 0, "expected the agent harness to run RAG queries"
    assert outcome["index_meta"]["indexed"] > 0, "expected chunks to be embedded/indexed"

    claims = db_session.query(Claim).filter(Claim.assessment_id == assessment.id).all()
    assert claims, "expected at least one extracted claim"

    cited = [c for c in claims if c.evidence_refs]
    grounding_rate = len(cited) / len(claims)
    assert grounding_rate >= 0.5, f"citation-grounding rate too low: {grounding_rate:.2f}"

    runtimes = [c for c in claims if c.attribute == "runtime"]
    assert any(c.value == "node" for c in runtimes), "expected node runtime claim from sample-app package.json"

    nfr = [c for c in claims if c.entity_type == "business" or c.attribute in NFR_ATTRIBUTES]
    assert nfr, "expected NFR/compliance claims from requirements doc"

    infra_keys = {
        e.normalized_key
        for model in (Application, Server, DatabaseEntity, Interface)
        for e in db_session.query(model).filter(model.assessment_id == assessment.id)
    }
    assert infra_keys & {"postgres", "redis", "kafka"}, "expected infra deps from sample-app manifests"

    # Precise entity/dependency/NFR expectations, verified against the literal facts
    # planted by scripts/generate_sample_data.py (not hand-traced from the regexes —
    # run and inspected to confirm each of these actually holds before committing it).
    app_keys = {
        a.normalized_key
        for a in db_session.query(Application).filter(Application.assessment_id == assessment.id)
    }
    assert {"customer-portal", "billing-service", "identity-gateway", "reporting-hub"} <= app_keys

    servers_by_key = {
        s.normalized_key: s
        for s in db_session.query(Server).filter(Server.assessment_id == assessment.id)
    }
    assert {"app-portal-01", "app-bill-01", "app-id-01", "app-rpt-01"} <= set(servers_by_key)
    # The deliberately-planted OS conflict (CMDB main sheet vs. its own "Legacy Notes"
    # sheet, plus the architecture doc's historical note) must resolve to the CMDB's
    # current value, not the legacy one — precedence-driven, with confidence as the
    # same-document tiebreaker per reconciliation.persist_extraction.
    assert (servers_by_key["app-bill-01"].attributes or {}).get("os") == "RHEL 8"

    edges = {
        (e.source_key, e.rel_type, e.target_key)
        for e in db_session.query(DependencyEdge).filter(DependencyEdge.assessment_id == assessment.id)
    }
    assert ("customer-portal", "depends_on", "billing-service") in edges
    assert ("billing-service", "calls", "identity-gateway") in edges
    assert ("reporting-hub", "depends_on", "billing-service") in edges

    nfr_attrs_covered = {
        c.attribute
        for c in claims
        if c.is_selected and c.entity_type == "business"
    }
    assert {
        "sla",
        "rto",
        "rpo",
        "availability",
        "latency",
        "compliance",
        "data_residency",
        "encryption",
        "scalability",
    } <= nfr_attrs_covered

    # Conflict reconciliation: whatever conflicts naturally arise from the CMDB/architecture
    # overlap must resolve to the higher-precedence source document, not just higher confidence.
    conflicts = db_session.query(Conflict).filter(Conflict.assessment_id == assessment.id).all()
    if conflicts:
        docs_by_id = {d.id: d for d in db_session.query(Document).filter(Document.assessment_id == assessment.id)}
        claims_by_id = {c.id: c for c in claims}
        for conflict in conflicts:
            selected = claims_by_id.get(conflict.selected_claim_id or "")
            if not selected or not selected.source_document_id:
                continue
            selected_doc = docs_by_id.get(selected.source_document_id)
            if not selected_doc:
                continue
            for cid in conflict.claim_ids:
                other = claims_by_id.get(cid)
                if not other or other.id == selected.id or not other.source_document_id:
                    continue
                other_doc = docs_by_id.get(other.source_document_id)
                if other_doc:
                    assert selected_doc.precedence >= other_doc.precedence, (
                        f"conflict {conflict.id} winner has lower document precedence than a loser"
                    )

    # Retrieval sanity check: a plausible query should return grounded hits.
    hits = get_retriever().retrieve(db_session, assessment.id, "server operating system inventory", top_k=5)
    assert hits, "expected retrieval to return hits for a plausible estate query"

    # The single highest-leverage probe for the shape-guard/table-summary chunking work:
    # a "total X" question must retrieve the one authoritative computed-summary chunk,
    # not just the row-group chunks the model would have to (unreliably) re-sum itself.
    total_vcpu_hits = get_retriever().retrieve(
        db_session, assessment.id, "total vCPU across all servers combined sum", top_k=5
    )
    summary_hits = [c for c in total_vcpu_hits if (c.metadata_json or {}).get("kind") == "table_summary"]
    assert summary_hits, "expected the CMDB's computed-summary chunk to be retrievable for a total-vCPU query"
    # 4 + 8 + 4 from Customer Portal/Billing Service/Identity Gateway; Reporting Hub's vCPU
    # cell is deliberately blank in the sample data, so it's excluded from the sum (count=3/4).
    assert "vcpus: sum=16" in summary_hits[0].text
    assert "count=3/4" in summary_hits[0].text
