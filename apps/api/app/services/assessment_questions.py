from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import (
    Assessment,
    Claim,
    DependencyEdge,
    Document,
    DocumentType,
    EngagementQuestion,
    PipelineStatus,
    QuestionOrigin,
    ReviewDecision,
)
from app.services.evidence import (
    grounded_quote_for_chunk,
    public_evidence_fields,
    resolve_evidence_map,
)
from app.services.inventory import not_rejected_edge
from app.services.llm_clients import DisabledChatCompleter
from app.services.llm_reasoning import GroundedProse, get_grounded_prose
from app.services.ports import Retriever
from app.services.questionnaire_extract import normalize_question_key

QUESTION_SET_VERSION = "migration-readiness-v1"
ASK_MAX_CHARS = 500
EMPTY_CUSTOM = "The uploaded evidence does not answer this question."

_TOKEN = re.compile(r"[a-z0-9]+", re.I)
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "what",
    "which",
    "who",
    "how",
    "are",
    "is",
    "there",
    "known",
    "does",
    "do",
    "a",
    "an",
    "of",
    "in",
    "to",
    "on",
    "or",
    "this",
    "that",
    "any",
    "question",
}
_HINTS: list[tuple[tuple[str, ...], set[str]]] = [
    (("critical", "criticality"), {"business_criticality", "criticality"}),
    (
        ("compliance", "pci", "hipaa", "residency", "encryption"),
        {"compliance", "data_residency", "encryption", "security"},
    ),
    (("depend", "integration", "interface"), {"dependency", "integration", "interface"}),
    (("gap", "blocker", "debt"), {"gap", "constraint", "tech_debt", "blocker"}),
    (("runtime", "platform", "operating"), {"os", "runtime", "framework", "architecture"}),
    (
        ("sla", "rto", "rpo", "latency", "availab", "scale"),
        {"sla", "availability", "latency", "rto", "rpo", "scalability"},
    ),
]


def _selected_claims(db: Session, assessment_id: str) -> list[Claim]:
    return (
        db.query(Claim)
        .filter(Claim.assessment_id == assessment_id, Claim.is_selected.is_(True))
        .all()
    )


def _question_tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "") if t.lower() not in _STOP and len(t) > 2}


def _hinted_attributes(question: str) -> set[str]:
    lower = (question or "").lower()
    attrs: set[str] = set()
    for needles, names in _HINTS:
        if any(n in lower for n in needles):
            attrs |= names
    return attrs


def match_claims_to_question(question: str, claims: list[Claim]) -> list[Claim]:
    tokens = _question_tokens(question)
    hinted = _hinted_attributes(question)
    matches: list[Claim] = []
    for claim in claims:
        value = claim.override_value or claim.value
        blob = f"{claim.entity_type} {claim.entity_key} {claim.attribute} {value}"
        blob_tokens = _question_tokens(blob)
        if hinted and claim.attribute in hinted:
            matches.append(claim)
            continue
        if tokens and tokens & blob_tokens:
            matches.append(claim)
    return matches


def _answer(
    question_id: str,
    question: str,
    claims: list[Claim],
    predicate: Callable[[Claim], bool],
    empty: str,
    *,
    origin: str = "standard",
) -> dict[str, Any]:
    matches = [c for c in claims if predicate(c)]
    supported = [c for c in matches if c.evidence_refs and not c.unsupported]
    values = [
        {
            "entity": f"{c.entity_type}:{c.entity_key}",
            "attribute": c.attribute,
            "value": c.override_value or c.value,
        }
        for c in matches
    ]
    answer = (
        "; ".join(
            f"{item['entity']} {item['attribute']}={item['value']}" for item in values
        )
        if values
        else empty
    )
    confidence = (
        round(sum(c.confidence for c in matches) / len(matches), 2) if matches else 0.0
    )
    return {
        "id": question_id,
        "origin": origin,
        "question": question,
        "answer": answer,
        "facts": values,
        "confidence": confidence,
        "supported": bool(matches) and len(supported) == len(matches),
        "needs_human_review": not matches
        or any(c.needs_human_review for c in matches)
        or len(supported) != len(matches),
        "claim_ids": [c.id for c in matches],
        "evidence_refs": list(
            dict.fromkeys(ref for c in supported for ref in (c.evidence_refs or []))
        ),
        "assumptions": [] if matches else [empty],
        "answer_source": "template",
    }


def _questionnaire_document_ids(db: Session, assessment_id: str) -> frozenset[str]:
    """A question's evidence must come from an actual source document, not from the
    questionnaire it may have been extracted from -- otherwise an `uploaded`-origin
    question (its text pulled straight out of a questionnaire chunk via
    `questionnaire_extract.parse_questionnaire_questions`) can end up citing that very
    same chunk as its own "evidence" once retrieved back, which is circular, not grounding."""
    return frozenset(
        doc_id
        for (doc_id,) in db.query(Document.id)
        .filter(Document.assessment_id == assessment_id, Document.doc_type == DocumentType.questionnaire)
        .all()
    )


def answer_custom_question(
    db: Session,
    assessment_id: str,
    *,
    question_id: str,
    question: str,
    origin: str,
    claims: list[Claim],
    retriever: Retriever | None = None,
    questionnaire_document_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    matches = match_claims_to_question(question, claims)
    payload = _answer(
        question_id,
        question,
        matches,
        lambda _c: True,
        EMPTY_CUSTOM,
        origin=origin,
    )
    retrieved_ids: list[str] = []
    retrieved_quotes: dict[str, str] = {}
    if retriever is not None:
        try:
            # Over-fetch and filter rather than requesting exactly 6 -- excluding
            # questionnaire chunks after the fact shouldn't leave fewer than 6 real
            # candidates just because a questionnaire chunk happened to rank near the top.
            candidates = retriever.retrieve(db, assessment_id, question, top_k=12)
        except Exception:
            candidates = []
        chunks = [c for c in candidates if c.document_id not in questionnaire_document_ids][:6]
        for chunk in chunks:
            retrieved_ids.append(chunk.id)
            retrieved_quotes[chunk.id] = (chunk.text or "")[:240]
    payload["evidence_refs"] = list(
        dict.fromkeys([*payload["evidence_refs"], *retrieved_ids])
    )
    if not matches and retrieved_ids:
        quotes = [retrieved_quotes[cid] for cid in retrieved_ids if retrieved_quotes.get(cid)]
        payload["answer"] = " ".join(quotes)[:500] if quotes else EMPTY_CUSTOM
        payload["supported"] = bool(quotes)
        payload["needs_human_review"] = not quotes
        payload["assumptions"] = [] if quotes else [EMPTY_CUSTOM]
        payload["confidence"] = 0.55 if quotes else 0.0
    payload["_retrieved_quotes"] = retrieved_quotes
    return payload


def _attach_evidence(
    db: Session, answers: list[dict[str, Any]], claims: list[Claim]
) -> None:
    evidence_by_chunk = resolve_evidence_map(
        db,
        [ref for answer in answers for ref in answer["evidence_refs"]],
    )
    # A ref can only be attributed a claim's quote when it's actually grounded in that
    # specific chunk's text (see `evidence.grounded_quote_for_chunk`) -- a claim's
    # evidence_refs can span multiple chunks, and citations.py doesn't guarantee the quote
    # appears verbatim in every one of them.
    quote_by_ref: dict[str, str] = {}
    for claim in claims:
        if not claim.evidence_quote:
            continue
        for ref in claim.evidence_refs or []:
            entry = evidence_by_chunk.get(ref)
            if not entry:
                continue
            grounded = grounded_quote_for_chunk(entry, claim.evidence_quote)
            if grounded:
                quote_by_ref[ref] = grounded
    for answer in answers:
        # Quotes retrieved directly from a chunk's own text (the RAG fallback in
        # `answer_custom_question`) are correct by construction -- no grounding check needed.
        quote_by_ref.update(answer.pop("_retrieved_quotes", {}) or {})
    for answer in answers:
        answer["evidence"] = [
            {
                **public_evidence_fields(evidence_by_chunk[ref]),
                "quote": quote_by_ref.get(ref),
            }
            for ref in answer["evidence_refs"]
            if ref in evidence_by_chunk
        ]


def _standard_answers(
    claims: list[Claim], edges: list[DependencyEdge]
) -> list[dict[str, Any]]:
    answers = [
        _answer(
            "estate_inventory",
            "What applications, servers, and databases are in scope?",
            claims,
            lambda c: c.attribute == "name"
            and c.entity_type in {"application", "server", "database"},
            "The uploaded evidence does not provide a complete estate inventory.",
        ),
        _answer(
            "dependencies",
            "What application and infrastructure dependencies affect migration sequencing?",
            claims,
            lambda c: c.attribute in {"dependency", "integration", "interface"},
            "No claim-level dependency facts were found; consult the dependency graph.",
        ),
        _answer(
            "platform_compatibility",
            "Which operating systems, runtimes, and platforms require compatibility review?",
            claims,
            lambda c: c.attribute in {"os", "runtime", "framework", "architecture"},
            "Platform compatibility evidence is incomplete.",
        ),
        _answer(
            "service_levels",
            "What availability, performance, recovery, and scale requirements apply?",
            claims,
            lambda c: c.attribute
            in {"sla", "availability", "latency", "rto", "rpo", "scalability"},
            "Service-level and capacity requirements are incomplete.",
        ),
        _answer(
            "security_compliance",
            "What security, compliance, encryption, and residency constraints apply?",
            claims,
            lambda c: c.attribute
            in {"compliance", "encryption", "data_residency", "security"},
            "Security and compliance constraints are incomplete.",
        ),
        _answer(
            "migration_blockers",
            "What constraints, gaps, and technical debt may block migration?",
            claims,
            lambda c: c.attribute in {"constraint", "gap", "tech_debt", "blocker"},
            "No evidenced blockers were extracted; stakeholder validation is required.",
        ),
    ]
    dependency_answer = next(a for a in answers if a["id"] == "dependencies")
    if edges:
        dependency_answer.update(
            {
                "answer": "; ".join(
                    f"{e.source_type}:{e.source_key} {e.rel_type} "
                    f"{e.target_type}:{e.target_key}"
                    for e in edges
                ),
                "facts": [
                    {
                        "source": f"{e.source_type}:{e.source_key}",
                        "relationship": e.rel_type,
                        "target": f"{e.target_type}:{e.target_key}",
                    }
                    for e in edges
                ],
                "confidence": round(sum(e.confidence for e in edges) / len(edges), 2),
                "supported": all(bool(e.evidence_refs) for e in edges),
                "needs_human_review": any(e.needs_human_review for e in edges)
                or any(not e.evidence_refs for e in edges),
                "evidence_refs": list(
                    dict.fromkeys(ref for e in edges for ref in (e.evidence_refs or []))
                ),
                "assumptions": [],
                "answer_source": "template",
            }
        )
    return answers


def build_assessment_answers(
    db: Session,
    assessment_id: str,
    *,
    prose: GroundedProse | None = None,
    retriever: Retriever | None = None,
) -> dict[str, Any]:
    claims = _selected_claims(db, assessment_id)
    edges = (
        db.query(DependencyEdge)
        .filter(DependencyEdge.assessment_id == assessment_id, not_rejected_edge())
        .all()
    )
    answers = _standard_answers(claims, edges)
    custom_rows = (
        db.query(EngagementQuestion)
        .filter(EngagementQuestion.assessment_id == assessment_id)
        .order_by(EngagementQuestion.created_at.asc())
        .all()
    )
    questionnaire_document_ids = _questionnaire_document_ids(db, assessment_id)
    for row in custom_rows:
        answers.append(
            answer_custom_question(
                db,
                assessment_id,
                question_id=row.id,
                question=row.question,
                origin=row.origin.value,
                claims=claims,
                retriever=retriever,
                questionnaire_document_ids=questionnaire_document_ids,
            )
        )
    _attach_evidence(db, answers, claims)
    (prose or get_grounded_prose()).rewrite_question_answers(answers)
    return {
        "question_set": QUESTION_SET_VERSION,
        "answers": answers,
        "complete": all(a["supported"] for a in answers),
        "review_required": any(a["needs_human_review"] for a in answers),
    }


def state_fingerprint(db: Session, assessment: Assessment, *, include_questions: bool = False) -> str:
    """Changes whenever an answer could: a new pipeline run, any review decision, and
    (optionally) a new engagement question. Answers are a pure function of this state."""
    count, latest = (
        db.query(func.count(ReviewDecision.id), func.max(ReviewDecision.updated_at))
        .filter(ReviewDecision.assessment_id == assessment.id)
        .one()
    )
    finished = assessment.pipeline_finished_at.isoformat() if assessment.pipeline_finished_at else "-"
    parts = [finished, str(count), latest.isoformat() if latest else "-"]
    if include_questions:
        q_count, q_latest = (
            db.query(func.count(EngagementQuestion.id), func.max(EngagementQuestion.created_at))
            .filter(EngagementQuestion.assessment_id == assessment.id)
            .one()
        )
        parts += [str(q_count), q_latest.isoformat() if q_latest else "-"]
    return "|".join(parts)


def cached_assessment_answers(
    db: Session, assessment: Assessment, *, retriever: Retriever | None = None
) -> dict[str, Any]:
    """Answers for the Questions tab. The page re-requests them on every poll and page
    load; recomputing each time repeated the retrieval and the LLM prose rewrite for
    identical output. A completed run's answers are computed once per state fingerprint;
    while a run is in flight (or before one has completed) they are template-only — the
    claims are being rebuilt, so neither retrieval nor the LLM is worth paying for."""
    if assessment.status != PipelineStatus.completed:
        return build_assessment_answers(
            db, assessment.id, prose=GroundedProse(DisabledChatCompleter()), retriever=None
        )
    fingerprint = state_fingerprint(db, assessment, include_questions=True)
    if assessment.answers_cache is not None and assessment.answers_fingerprint == fingerprint:
        return assessment.answers_cache
    result = json.loads(json.dumps(build_assessment_answers(db, assessment.id, retriever=retriever), default=str))
    assessment.answers_cache = result
    assessment.answers_fingerprint = fingerprint
    db.commit()
    return result


def persist_ad_hoc_question(db: Session, assessment_id: str, question: str) -> EngagementQuestion:
    text = (question or "").strip()
    if not text:
        raise ValueError("question is required")
    if len(text) > ASK_MAX_CHARS:
        raise ValueError(f"question must be at most {ASK_MAX_CHARS} characters")
    key = normalize_question_key(text)
    if len(key) < 8:
        raise ValueError("question is too short")
    existing = (
        db.query(EngagementQuestion)
        .filter(
            EngagementQuestion.assessment_id == assessment_id,
            EngagementQuestion.question_key == key,
        )
        .one_or_none()
    )
    if existing:
        return existing
    row = EngagementQuestion(
        assessment_id=assessment_id,
        origin=QuestionOrigin.ad_hoc,
        question=text if text.endswith("?") else f"{text}?",
        question_key=key,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def plan_dynamic_questions(db: Session, assessment_id: str) -> list[EngagementQuestion]:
    """Generate estate-specific supplementary questions (QuestionPlanner) and persist
    them as `origin=dynamic`, deduped by `question_key` exactly like uploaded/ad-hoc
    questions. The base question set (`_standard_answers`) is untouched — comparability
    across assessments is preserved; these are additive and tagged distinctly."""
    settings = get_settings()
    if not settings.question_planner_enabled:
        return []

    from app.services.providers import get_question_planner
    from app.services.question_planning import build_inventory_summary

    summary = build_inventory_summary(db, assessment_id)
    proposed = get_question_planner().plan(summary)[: settings.question_planner_max_questions]
    if not proposed:
        return []

    existing = {
        row.question_key
        for row in db.query(EngagementQuestion)
        .filter(EngagementQuestion.assessment_id == assessment_id)
        .all()
    }
    rows: list[EngagementQuestion] = []
    for planned in proposed:
        text = (planned.question or "").strip()
        if not text:
            continue
        question_text = text if text.endswith("?") else f"{text}?"
        key = normalize_question_key(question_text)
        if len(key) < 8 or key in existing:
            continue
        existing.add(key)
        row = EngagementQuestion(
            assessment_id=assessment_id,
            origin=QuestionOrigin.dynamic,
            question=question_text,
            question_key=key,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows
