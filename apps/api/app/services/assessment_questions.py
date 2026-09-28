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
    complete_quote,
    grounded_quote_for_chunk,
    label_table_row,
    passage_terms,
    public_evidence_fields,
    relevant_passage,
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
        (
            "sla",
            "rto",
            "rpo",
            "latency",
            "availab",
            "scale",
            "service level",
            "performance",
            "recovery",
            "non-functional",
            "nfr",
        ),
        {"sla", "availability", "latency", "rto", "rpo", "scalability"},
    ),
]


def _selected_claims(db: Session, assessment_id: str) -> list[Claim]:
    return db.query(Claim).filter(Claim.assessment_id == assessment_id, Claim.is_selected.is_(True)).all()


def _question_tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "") if t.lower() not in _STOP and len(t) > 2}


def _hinted_attributes(question: str) -> set[str]:
    lower = (question or "").lower()
    attrs: set[str] = set()
    for needles, names in _HINTS:
        if any(n in lower for n in needles):
            attrs |= names
    return attrs


# Words that appear in almost every assessment question/fact and so can't show that a
# fact is about the question ("migration" matched every business:migration-requirements
# NFR fact, whatever was asked).
_GENERIC = {
    "migration",
    "migrate",
    "service",
    "services",
    "application",
    "applications",
    "app",
    "apps",
    "system",
    "systems",
    "use",
    "used",
    "uses",
    "using",
    "current",
    "currently",
    "require",
    "required",
    "requirement",
    "requirements",
    "list",
    "provide",
    "describe",
    "please",
    "estate",
}
# Attribute names a question can ask for directly. When a question names attributes, only
# those are answered ("RTO and RPO" -> rto, rpo; not every service-level fact).
_ATTRIBUTE_WORDS: dict[str, set[str]] = {
    "rto": {"rto"},
    "rpo": {"rpo"},
    "sla": {"sla", "slas"},
    "latency": {"latency", "response"},
    "availability": {"availability", "available", "uptime"},
    "scalability": {"scalability", "scale", "scaling", "concurrent", "peak"},
    "encryption": {"encryption", "encrypted", "encrypt"},
    "data_residency": {"residency", "sovereignty"},
    "compliance": {"compliance", "compliant", "pci", "hipaa", "gdpr", "sox"},
    "os": {"os", "operating"},
    "runtime": {"runtime", "runtimes"},
    "framework": {"framework", "frameworks"},
    "business_criticality": {"critical", "criticality"},
    "vcpus": {"vcpu", "vcpus", "cpu", "cpus", "cores"},
    "memory_gb": {"memory", "ram"},
    "disk_gb": {"disk", "storage"},
}
_RELATION_WORDS = {
    "depend",
    "depends",
    "dependency",
    "dependencies",
    "uses",
    "use",
    "used",
    "using",
    "connect",
    "connects",
    "connected",
    "call",
    "calls",
    "integrate",
    "integrates",
    "integration",
    "talk",
    "talks",
    "database",
    "databases",
    "db",
    "hosted",
    "host",
    "hosts",
    "runs",
}
_DB_WORDS = {"database", "databases", "db", "datastore"}
_HOST_WORDS = {"hosted", "host", "hosts", "server", "servers", "runs"}


def _words(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "")}


def _key_words(entity_key: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", entity_key.lower()) if len(t) >= 2}


def _named_entities(words: set[str], keys: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Entities the question names: every word of the entity's key appears in it
    ("billing service" -> application:billing-service; "portal" alone does NOT name
    server:app-portal-01)."""
    named = set()
    for etype, ekey in keys:
        kw = _key_words(ekey) - {"01", "02"}
        if kw and kw <= words and not kw <= _GENERIC:
            named.add((etype, ekey))
    return named


def _named_attributes(words: set[str]) -> set[str]:
    return {attr for attr, needles in _ATTRIBUTE_WORDS.items() if words & needles}


def match_claims_to_question(question: str, claims: list[Claim]) -> list[Claim]:
    words = _words(question)
    attrs = _named_attributes(words) or _hinted_attributes(question)
    named = _named_entities(words, {(c.entity_type, c.entity_key) for c in claims})
    phrases = [" ".join(sorted(_key_words(k), key=k.find)) for _t, k in named]

    def mentions_named(claim: Claim) -> bool:
        value = (claim.override_value or claim.value or "").lower()
        return (claim.entity_type, claim.entity_key) in named or any(p and p in value for p in phrases)

    if attrs:
        candidates = [c for c in claims if c.attribute in attrs]
        if named:
            scoped = [c for c in candidates if mentions_named(c)]
            if scoped:
                return scoped
        # The subject may only appear inside values ("RTO: 4 hours for Billing Service"
        # under a business entity): narrow by the question's remaining words when any fit.
        attribute_words = set().union(*(_ATTRIBUTE_WORDS.get(a, set()) for a in attrs))
        subject = _question_tokens(question) - _GENERIC - attribute_words
        if subject:
            scoped = [c for c in candidates if subject & _question_tokens(c.override_value or c.value)]
            if scoped:
                return scoped
        return candidates
    if named:
        own = [c for c in claims if (c.entity_type, c.entity_key) in named]
        asked = _question_tokens(question) - _GENERIC - set().union(*(_key_words(k) for _t, k in named))
        if not asked:
            return own  # "tell me about the billing service"
        # Something specific about a named entity: only facts that speak to it. None may
        # (e.g. "which database does X use" is a relationship) — then edges/retrieval answer.
        return [c for c in own if asked & _question_tokens(f"{c.attribute} {c.override_value or c.value}")]
    tokens = _question_tokens(question) - _GENERIC
    return [
        c
        for c in claims
        if tokens & _question_tokens(f"{c.entity_type} {c.entity_key} {c.attribute} {c.override_value or c.value}")
    ]


def match_edges_to_question(question: str, claims: list[Claim], edges: list[DependencyEdge]) -> list[DependencyEdge]:
    """Relationship questions ("which database does the customer portal use?") are
    answered from the dependency graph — that link is an edge, never a claim."""
    words = _words(question)
    if not words & _RELATION_WORDS:
        return []
    keys = {(c.entity_type, c.entity_key) for c in claims}
    keys |= {(e.source_type, e.source_key) for e in edges} | {(e.target_type, e.target_key) for e in edges}
    named = _named_entities(words, keys)
    if not named:
        return []
    related = [e for e in edges if (e.source_type, e.source_key) in named or (e.target_type, e.target_key) in named]
    if words & _DB_WORDS:
        related = [e for e in related if "database" in (e.source_type, e.target_type)]
    elif words & _HOST_WORDS:
        related = [e for e in related if "server" in (e.source_type, e.target_type)]
    return related


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
    answer = "; ".join(f"{item['entity']} {item['attribute']}={item['value']}" for item in values) if values else empty
    confidence = round(sum(c.confidence for c in matches) / len(matches), 2) if matches else 0.0
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
        "evidence_refs": list(dict.fromkeys(ref for c in supported for ref in (c.evidence_refs or []))),
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
    edges: list[DependencyEdge] | None = None,
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
    if edges is None:
        edges = (
            db.query(DependencyEdge).filter(DependencyEdge.assessment_id == assessment_id, not_rejected_edge()).all()
        )
    related = match_edges_to_question(question, claims, edges)
    if related:
        _add_edge_facts(payload, related)
    retrieved_ids: list[str] = []
    if retriever is not None:
        try:
            # Over-fetch and filter rather than requesting exactly 6 -- excluding
            # questionnaire chunks after the fact shouldn't leave fewer than 6 real
            # candidates just because a questionnaire chunk happened to rank near the top.
            candidates = retriever.retrieve(db, assessment_id, question, top_k=12)
        except Exception:
            candidates = []
        chunks = [c for c in candidates if c.document_id not in questionnaire_document_ids][:6]
        # Only chunks that actually contain something the question asks about are cited;
        # the rest of the top-k is ranking noise, and citing it made answers look
        # unsupported by their own sources.
        terms = passage_terms(question) - _GENERIC
        # One shared common word ("customer") isn't relevance; require two of the
        # question's terms (or its only one).
        needed = min(2, len(terms)) or 1
        scored = [(relevant_passage(c.text or "", terms), c) for c in chunks]
        relevant = [(passage, c) for (passage, score), c in scored if score >= needed]
        retrieved_ids = [c.id for _p, c in relevant]
        if not payload["facts"] and relevant:
            passages = [passage for passage, _c in relevant]
            payload["answer"] = " … ".join(passages)[:600]
            payload["supported"] = True
            payload["needs_human_review"] = False
            payload["assumptions"] = []
            payload["confidence"] = 0.55
    payload["evidence_refs"] = list(dict.fromkeys([*payload["evidence_refs"], *retrieved_ids]))
    return payload


def _add_edge_facts(payload: dict[str, Any], edges: list[DependencyEdge]) -> None:
    facts = [
        {
            "entity": f"{e.source_type}:{e.source_key}",
            "attribute": e.rel_type,
            "value": f"{e.target_type}:{e.target_key}",
        }
        for e in edges
    ]
    no_claim_facts = not payload["facts"]
    payload["facts"] = [*payload["facts"], *facts]
    text = "; ".join(f"{f['entity']} {f['attribute']} {f['value']}" for f in facts)
    payload["answer"] = text if no_claim_facts else f"{payload['answer']}; {text}"
    refs = [ref for e in edges for ref in (e.evidence_refs or [])]
    payload["evidence_refs"] = list(dict.fromkeys([*payload["evidence_refs"], *refs]))
    edge_conf = sum(e.confidence for e in edges) / len(edges)
    payload["confidence"] = round(edge_conf if no_claim_facts else (payload["confidence"] + edge_conf) / 2, 2)
    grounded = all(e.evidence_refs for e in edges)
    payload["supported"] = grounded if no_claim_facts else payload["supported"] and grounded
    payload["needs_human_review"] = (
        (not grounded or any(e.needs_human_review for e in edges))
        if no_claim_facts
        else payload["needs_human_review"] or not grounded
    )
    if no_claim_facts:
        payload["assumptions"] = []
    payload["edge_ids"] = [e.id for e in edges]


MAX_QUOTES_PER_SOURCE = 4


def _attach_evidence(db: Session, answers: list[dict[str, Any]], claims: list[Claim]) -> None:
    """Resolve each answer's evidence refs to sources, quoting what supports THIS answer.

    Per answer and per cited chunk, one quote for every fact of the answer that cites the
    chunk: the fact's own quote when it's really in the chunk (`grounded_quote_for_chunk`),
    else the chunk's passage that best matches that fact. So an answer built from two
    facts in the same section shows both supporting sentences, not just the first. A
    chunk no fact explains (a retrieved one) is quoted by its passage that best matches
    the question. `quote` keeps the first quote for callers that show a single line.
    """
    evidence_by_chunk = resolve_evidence_map(db, [ref for answer in answers for ref in answer["evidence_refs"]])
    claims_by_id = {c.id: c for c in claims}
    edge_ids = [eid for answer in answers for eid in answer.get("edge_ids", [])]
    edges_by_id = (
        {e.id: e for e in db.query(DependencyEdge).filter(DependencyEdge.id.in_(edge_ids))} if edge_ids else {}
    )
    for answer in answers:
        # (refs, own quote, words describing the fact) for every fact behind this answer.
        facts: list[tuple[list[str], str | None, set[str]]] = []
        for cid in answer.get("claim_ids", []):
            claim = claims_by_id.get(cid)
            if claim is not None:
                value = claim.override_value or claim.value or ""
                words = passage_terms(claim.attribute.replace("_", " "), value) - _GENERIC
                facts.append((claim.evidence_refs or [], claim.evidence_quote, words))
        for eid in answer.get("edge_ids", []):
            edge = edges_by_id.get(eid)
            if edge is not None:
                words = passage_terms(edge.source_key, edge.target_key) - _GENERIC
                facts.append((edge.evidence_refs or [], edge.evidence_quote, words))
        question_terms = (
            passage_terms(
                answer["question"], *(f"{f.get('entity', '')} {f.get('value', '')}" for f in answer.get("facts", []))
            )
            - _GENERIC
        )
        evidence = []
        for ref in answer["evidence_refs"]:
            entry = evidence_by_chunk.get(ref)
            if entry is None:
                continue
            text = entry.get("text", "")
            quotes: list[str] = []
            for refs, own_quote, words in facts:
                if ref not in refs:
                    continue
                quote = grounded_quote_for_chunk(entry, own_quote)
                if quote:
                    quote = label_table_row(text, complete_quote(text, quote))
                elif words:
                    passage, score = relevant_passage(text, words)
                    quote = passage if score > 0 else None
                if quote and quote not in quotes:
                    quotes.append(quote)
            if not quotes:
                passage, score = relevant_passage(text, question_terms)
                if score > 0:
                    quotes.append(passage)
            quotes = quotes[:MAX_QUOTES_PER_SOURCE]
            evidence.append({**public_evidence_fields(entry), "quote": quotes[0] if quotes else None, "quotes": quotes})
        answer["evidence"] = evidence
        answer.pop("_retrieved_quotes", None)


def _standard_answers(claims: list[Claim], edges: list[DependencyEdge]) -> list[dict[str, Any]]:
    answers = [
        _answer(
            "estate_inventory",
            "What applications, servers, and databases are in scope?",
            claims,
            lambda c: c.attribute == "name" and c.entity_type in {"application", "server", "database"},
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
            lambda c: c.attribute in {"sla", "availability", "latency", "rto", "rpo", "scalability"},
            "Service-level and capacity requirements are incomplete.",
        ),
        _answer(
            "security_compliance",
            "What security, compliance, encryption, and residency constraints apply?",
            claims,
            lambda c: c.attribute in {"compliance", "encryption", "data_residency", "security"},
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
                    f"{e.source_type}:{e.source_key} {e.rel_type} {e.target_type}:{e.target_key}" for e in edges
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
                "evidence_refs": list(dict.fromkeys(ref for e in edges for ref in (e.evidence_refs or []))),
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
    edges = db.query(DependencyEdge).filter(DependencyEdge.assessment_id == assessment_id, not_rejected_edge()).all()
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
                edges=edges,
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


def state_fingerprint(
    db: Session,
    assessment: Assessment,
    *,
    include_questions: bool = False,
    exclude_question_id: str | None = None,
) -> str:
    """Changes whenever an answer could: a new pipeline run, any review decision, and
    (optionally) a new engagement question. Answers are a pure function of this state.
    `exclude_question_id` gives the fingerprint as it was before that question existed."""
    count, latest = (
        db.query(func.count(ReviewDecision.id), func.max(ReviewDecision.updated_at))
        .filter(ReviewDecision.assessment_id == assessment.id)
        .one()
    )
    finished = assessment.pipeline_finished_at.isoformat() if assessment.pipeline_finished_at else "-"
    parts = [finished, str(count), latest.isoformat() if latest else "-"]
    if include_questions:
        query = db.query(func.count(EngagementQuestion.id), func.max(EngagementQuestion.created_at)).filter(
            EngagementQuestion.assessment_id == assessment.id
        )
        if exclude_question_id:
            query = query.filter(EngagementQuestion.id != exclude_question_id)
        q_count, q_latest = query.one()
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
        return build_assessment_answers(db, assessment.id, prose=GroundedProse(DisabledChatCompleter()), retriever=None)
    fingerprint = state_fingerprint(db, assessment, include_questions=True)
    if assessment.answers_cache is not None and assessment.answers_fingerprint == fingerprint:
        return assessment.answers_cache
    result = json.loads(json.dumps(build_assessment_answers(db, assessment.id, retriever=retriever), default=str))
    assessment.answers_cache = result
    assessment.answers_fingerprint = fingerprint
    db.commit()
    return result


def answer_added_question(
    db: Session,
    assessment: Assessment,
    row: EngagementQuestion,
    *,
    retriever: Retriever | None = None,
    prose: GroundedProse | None = None,
) -> dict[str, Any]:
    """Answer ONE newly added question and merge it into the stored answers.

    Asking a question used to regenerate the whole report with LLM prose (every answer
    rewritten, plus the readiness summary) and then rebuild every answer again for the
    Questions tab — minutes with a real LLM, for a change that only adds one answer.
    Other answers can't change when a question is added, so when the stored answers were
    current just before this question existed, only the new one is computed: one
    retrieval and one small rewrite call. The report's Q&A section is updated to match
    without touching the rest of the report.
    """
    current = state_fingerprint(db, assessment, include_questions=True)
    if assessment.answers_cache is not None and assessment.answers_fingerprint == current:
        return assessment.answers_cache  # e.g. the same question asked again
    before = state_fingerprint(db, assessment, include_questions=True, exclude_question_id=row.id)
    if (
        assessment.status != PipelineStatus.completed
        or assessment.answers_cache is None
        or assessment.answers_fingerprint != before
    ):
        return cached_assessment_answers(db, assessment, retriever=retriever)  # stale: full rebuild

    claims = _selected_claims(db, assessment.id)
    answer = answer_custom_question(
        db,
        assessment.id,
        question_id=row.id,
        question=row.question,
        origin=row.origin.value,
        claims=claims,
        retriever=retriever,
        questionnaire_document_ids=_questionnaire_document_ids(db, assessment.id),
    )
    _attach_evidence(db, [answer], claims)
    (prose or get_grounded_prose()).rewrite_question_answers([answer])

    stored = json.loads(json.dumps(assessment.answers_cache))
    stored["answers"] = [a for a in stored.get("answers", []) if a.get("id") != row.id]
    stored["answers"].append(json.loads(json.dumps(answer, default=str)))
    stored["complete"] = all(a.get("supported") for a in stored["answers"])
    stored["review_required"] = any(a.get("needs_human_review") for a in stored["answers"])
    assessment.answers_cache = stored
    assessment.answers_fingerprint = current

    from app.models.entities import AssessmentOutput

    output = db.query(AssessmentOutput).filter(AssessmentOutput.assessment_id == assessment.id).one_or_none()
    if output is not None:
        report_json = dict(output.report_json or {})
        report_json["assessment_questions"] = stored
        output.report_json = report_json
    db.commit()
    return stored


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
        for row in db.query(EngagementQuestion).filter(EngagementQuestion.assessment_id == assessment_id).all()
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
