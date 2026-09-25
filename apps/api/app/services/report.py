from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import Assessment, AssessmentOutput, Claim
from app.schemas.api import ExtractionResult, ReportOut
from app.services.assessment_questions import build_assessment_answers
from app.services.evidence import build_evidence_list, resolve_evidence_map
from app.services.graph import build_graph
from app.services.inventory import load_inventory
from app.services.llm_reasoning import GroundedProse, get_grounded_prose
from app.services.ports import Retriever


def _build_evidence_appendix(db: Session, claims: list[Claim]) -> list[dict]:
    evidence_by_chunk = resolve_evidence_map(
        db,
        [ref for claim in claims for ref in (claim.evidence_refs or [])],
    )
    return [
        {
            "claim_id": claim.id,
            "entity": f"{claim.entity_type}:{claim.entity_key}",
            "attribute": claim.attribute,
            "value": claim.override_value or claim.value,
            "confidence": claim.confidence,
            "quote": claim.evidence_quote,
            "chunk_ids": claim.evidence_refs,
            "evidence": build_evidence_list(
                evidence_by_chunk, claim.evidence_refs or [], claim.evidence_quote
            ),
            "unsupported": claim.unsupported,
        }
        for claim in claims
        if claim.is_selected
    ]


def generate_report(
    db: Session,
    assessment_id: str,
    extraction: ExtractionResult | None = None,
    *,
    prose: GroundedProse | None = None,
    retriever: Retriever | None = None,
    rag_metrics: dict | None = None,
) -> AssessmentOutput:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    snap = load_inventory(
        db,
        assessment_id,
        documents=True,
        claims=True,
        conflicts=True,
        recommendations=True,
    )
    apps = snap.applications
    servers = snap.servers
    databases = snap.databases
    edges = snap.edges
    claims = snap.claims
    conflicts = snap.conflicts
    documents = snap.documents
    graph = build_graph(db, assessment_id)
    writer = prose or get_grounded_prose()
    question_answers = build_assessment_answers(
        db, assessment_id, prose=writer, retriever=retriever
    )
    recommendations = [row.result for row in snap.recommendations]

    gaps = list(extraction.gaps) if extraction else []
    assumptions = list(extraction.assumptions) if extraction else []

    review_pending = [c for c in claims if c.needs_human_review]
    unsupported = [c for c in claims if c.unsupported]
    open_conflicts = [c for c in conflicts if c.status.value == "open"]

    if review_pending:
        gaps.append(f"{len(review_pending)} claims require human review")
    if unsupported:
        gaps.append(f"{len(unsupported)} unsupported claims lack source evidence")
    if open_conflicts:
        gaps.append(f"{len(open_conflicts)} unresolved conflicting facts")
    guardrail_blocks = (rag_metrics or {}).get("guardrail_blocks", 0)
    guardrail_sanitizes = (rag_metrics or {}).get("guardrail_sanitizes", 0)
    if guardrail_blocks:
        gaps.append(
            f"Guardrails blocked {guardrail_blocks} extraction attempt(s) "
            "(e.g. a prompt-injection attempt in an uploaded document) — see guardrail metrics"
        )
    if guardrail_sanitizes:
        gaps.append(
            f"Guardrails sanitized {guardrail_sanitizes} extraction output(s) "
            "(disallowed entity/relationship types or credential-like values were dropped)"
        )
    if not assumptions:
        assumptions.append(
            "Assessment derived from uploaded documents only; live discovery not performed"
        )

    cited = sum(1 for c in claims if c.evidence_refs)
    metrics = {
        "document_count": len(documents),
        "claim_count": len(claims),
        "cited_claim_pct": round((cited / len(claims) * 100) if claims else 0, 1),
        "conflict_count": len(conflicts),
        "application_count": len(apps),
        "server_count": len(servers),
        "database_count": len(databases),
        "edge_count": len(edges),
        "review_queue_count": len(review_pending),
        "recommendation_count": len(recommendations),
    }
    # Merge, don't replace: `metrics` also holds the review activity feed
    # (`follow_up_log`), the review sign-off, and the pipeline's run metrics — replacing
    # it wiped all of those every time the report was rebuilt (e.g. after each review).
    assessment.metrics = {**(assessment.metrics or {}), **metrics}

    code_docs = sum(
        1
        for d in documents
        if (hasattr(d.doc_type, "value") and d.doc_type.value == "code_snapshot")
        or str(d.doc_type) == "code_snapshot"
    )
    req_docs = sum(
        1
        for d in documents
        if (hasattr(d.doc_type, "value") and d.doc_type.value == "requirements")
        or str(d.doc_type) == "requirements"
    )
    nfr_claims = [
        c
        for c in claims
        if c.is_selected
        and (
            c.entity_type == "business"
            or c.attribute
            in {
                "sla",
                "rto",
                "rpo",
                "availability",
                "latency",
                "compliance",
                "data_residency",
                "encryption",
                "scalability",
                "nfr",
                "runtime",
                "framework",
            }
        )
    ]

    readiness_bits = []
    if apps and servers:
        readiness_bits.append(
            f"Identified {len(apps)} applications across {len(servers)} servers and {len(databases)} databases."
        )
    else:
        readiness_bits.append(
            "Inventory coverage is incomplete; additional source documents are recommended."
        )
    if code_docs:
        readiness_bits.append(
            f"Code snapshot analysis included ({code_docs} archive(s)); "
            "runtime/framework and infra deps inferred from manifests."
        )
    if req_docs or any(c.entity_type == "business" for c in nfr_claims):
        business_nfr_count = len([c for c in nfr_claims if c.entity_type == "business"])
        readiness_bits.append(
            f"Requirements/NFR signals captured ({business_nfr_count} business/NFR claims)."
        )
    if open_conflicts or review_pending:
        readiness_bits.append(
            "Migration readiness is provisional pending human review of low-confidence and conflicting claims."
        )
    else:
        readiness_bits.append(
            "No open conflicts; assessment is ready for wave planning subject to stakeholder confirmation."
        )
    readiness_summary = writer.rewrite_readiness_summary(
        {
            "application_count": len(apps),
            "server_count": len(servers),
            "database_count": len(databases),
            "code_snapshot_count": code_docs,
            "nfr_claim_count": len(nfr_claims),
            "open_conflicts": len(open_conflicts),
            "review_pending": len(review_pending),
            "unsupported_claims": len(unsupported),
            "gaps": gaps[:12],
            "template": " ".join(readiness_bits),
        },
        " ".join(readiness_bits),
    )

    inventory = {
        "applications": [
            {"name": a.name, "key": a.normalized_key, "attributes": a.attributes, "confidence": a.confidence}
            for a in apps
        ],
        "servers": [
            {"name": s.name, "key": s.normalized_key, "attributes": s.attributes, "confidence": s.confidence}
            for s in servers
        ],
        "databases": [
            {"name": d.name, "key": d.normalized_key, "attributes": d.attributes, "confidence": d.confidence}
            for d in databases
        ],
        "nfr_and_runtime": [
            {
                "entity": f"{c.entity_type}:{c.entity_key}",
                "attribute": c.attribute,
                "value": c.override_value or c.value,
                "confidence": c.confidence,
                "evidence": c.evidence_quote,
            }
            for c in nfr_claims
            if c.is_selected
        ],
    }
    dependencies = [
        {
            "source": f"{e.source_type}:{e.source_key}",
            "target": f"{e.target_type}:{e.target_key}",
            "relationship": e.rel_type,
            "confidence": e.confidence,
        }
        for e in edges
    ]
    evidence_appendix = _build_evidence_appendix(db, claims)

    report_json = {
        "assessment_id": assessment_id,
        "name": assessment.name,
        "readiness_summary": readiness_summary,
        "gaps": gaps,
        "assumptions": assumptions,
        "inventory": inventory,
        "dependencies": dependencies,
        "graph": graph.model_dump(),
        "metrics": metrics,
        "assessment_questions": question_answers,
        "infrastructure_recommendations": recommendations,
        "evidence_appendix": evidence_appendix,
        "workflow_stage": assessment.workflow_stage.value,
    }

    existing = (
        db.query(AssessmentOutput)
        .filter(AssessmentOutput.assessment_id == assessment_id)
        .one_or_none()
    )
    if existing:
        existing.readiness_summary = readiness_summary
        existing.gaps = gaps
        existing.assumptions = assumptions
        existing.inventory = inventory
        existing.dependencies = dependencies
        existing.evidence_appendix = evidence_appendix
        existing.report_json = report_json
        output = existing
    else:
        output = AssessmentOutput(
            assessment_id=assessment_id,
            readiness_summary=readiness_summary,
            gaps=gaps,
            assumptions=assumptions,
            inventory=inventory,
            dependencies=dependencies,
            evidence_appendix=evidence_appendix,
            report_json=report_json,
        )
        db.add(output)

    db.commit()
    db.refresh(output)
    return output


def report_to_schema(db: Session, assessment_id: str) -> ReportOut:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).one()
    output = (
        db.query(AssessmentOutput)
        .filter(AssessmentOutput.assessment_id == assessment_id)
        .one_or_none()
    )
    if not output:
        raise ValueError("Report not generated yet")
    evidence_appendix = output.evidence_appendix or []
    report_json = dict(output.report_json or {})
    if not evidence_appendix:
        claims = db.query(Claim).filter(Claim.assessment_id == assessment_id).all()
        evidence_appendix = _build_evidence_appendix(db, claims)
        report_json["evidence_appendix"] = evidence_appendix
    return ReportOut(
        assessment_id=assessment_id,
        readiness_summary=output.readiness_summary,
        gaps=output.gaps or [],
        assumptions=output.assumptions or [],
        inventory=output.inventory or {},
        dependencies=output.dependencies or [],
        evidence_appendix=evidence_appendix,
        report_json=report_json,
        metrics=assessment.metrics or {},
    )
