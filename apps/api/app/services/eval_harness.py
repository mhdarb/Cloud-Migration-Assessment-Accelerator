"""RAG + extraction evaluation harness.

Turns "did quality change?" from a vibe into numbers. Given a labeled estate
(`EstateLabels`), it runs the real pipeline and scores two things against ground truth:

  * Retrieval  — for each probe query, does the retriever surface the chunks that
    actually contain the expected facts? (context-recall, precision, MRR)
  * Extraction — do the materialized entities/attributes/sizing match the planted
    facts? (entity P/R/F1, sizing-field accuracy, grounding rate, NFR coverage,
    sizing coverage)

The default run uses the deterministic mock/heuristic path (`use_mock=True`) so it is a
stable CI regression gate; pointed at a real LLM + embeddings it becomes the measurement
you use to decide whether the reranker, a stronger embedder, or contextual retrieval
actually help — flip one variable, re-run, compare the report.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session

from app.models.entities import (
    Application,
    Claim,
    DatabaseEntity,
    InfrastructureRecommendation,
    Server,
)
from app.services.eval_metrics import mean, prf1, reciprocal_rank
from app.services.normalization import to_canonical
from app.services.ports import Retriever


@dataclass(frozen=True)
class SizingFact:
    """A planted sizing fact for one server; None means the source omits it on purpose."""

    server_key: str
    vcpus: float | None = None
    memory_gb: float | None = None
    disk_gb: float | None = None
    os: str | None = None


@dataclass(frozen=True)
class RetrievalProbe:
    """A query plus the evidence substrings that *should* appear in the retrieved chunks."""

    query: str
    must_surface: tuple[str, ...]


@dataclass(frozen=True)
class EstateLabels:
    name: str
    files: tuple[tuple[str, str], ...]  # (filename, content_type)
    subdir: str = ""  # path under sample-data/ where this estate's files live ("" = root)
    expected_servers: frozenset[str] = frozenset()
    expected_applications: frozenset[str] = frozenset()
    expected_databases: frozenset[str] = frozenset()
    sizing: tuple[SizingFact, ...] = ()
    expected_nfr_attributes: frozenset[str] = frozenset()
    retrieval_probes: tuple[RetrievalProbe, ...] = ()


@dataclass
class EvalReport:
    estate: str
    retrieval: dict[str, float] = field(default_factory=dict)
    extraction: dict[str, float] = field(default_factory=dict)
    per_probe: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def format_table(self) -> str:
        lines = [f"Eval: {self.estate}", "-" * 48, "RETRIEVAL"]
        for key, value in self.retrieval.items():
            lines.append(f"  {key:<26} {value:.3f}")
        lines.append("EXTRACTION")
        for key, value in self.extraction.items():
            lines.append(f"  {key:<26} {value:.3f}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Retrieval scoring
# --------------------------------------------------------------------------- #
def score_retrieval(
    db: Session,
    assessment_id: str,
    retriever: Retriever,
    probes: tuple[RetrievalProbe, ...],
    *,
    top_k: int,
) -> tuple[dict[str, float], list[dict[str, object]]]:
    context_recalls: list[float] = []
    precisions: list[float] = []
    rrs: list[float] = []
    per_probe: list[dict[str, object]] = []
    for probe in probes:
        hits = retriever.retrieve(db, assessment_id, probe.query, top_k=top_k)
        texts = [(h.id, (h.text or "")) for h in hits]
        # A chunk is relevant if it contains any of the probe's expected evidence tokens.
        relevant_ids = [cid for cid, text in texts if any(tok in text for tok in probe.must_surface)]
        # Context recall: of the expected tokens, how many appear somewhere in the top-k.
        found = sum(
            1 for tok in probe.must_surface if any(tok in text for _, text in texts)
        )
        recall = found / len(probe.must_surface) if probe.must_surface else 1.0
        precision = (len(relevant_ids) / len(texts)) if texts else 0.0
        rr = reciprocal_rank([cid for cid, _ in texts], relevant_ids)
        context_recalls.append(recall)
        precisions.append(precision)
        rrs.append(rr)
        per_probe.append(
            {
                "query": probe.query[:60],
                "context_recall": round(recall, 3),
                "precision": round(precision, 3),
                "reciprocal_rank": round(rr, 3),
                "hits": len(texts),
            }
        )
    return (
        {
            "mean_context_recall": round(mean(context_recalls), 3),
            "mean_precision": round(mean(precisions), 3),
            "mean_reciprocal_rank": round(mean(rrs), 3),
            "probes": float(len(probes)),
        },
        per_probe,
    )


# --------------------------------------------------------------------------- #
# Extraction scoring
# --------------------------------------------------------------------------- #
def _entity_keys(db: Session, assessment_id: str, model) -> set[str]:
    return {
        e.normalized_key
        for e in db.query(model).filter(model.assessment_id == assessment_id).all()
    }


def _values_match(field: str, expected: float | str, actual: object) -> bool:
    if isinstance(expected, str):
        return expected.lower() in str(actual or "").lower()
    exp_num = to_canonical(field, expected)
    act_num = to_canonical(field, actual)
    return exp_num is not None and act_num is not None and abs(exp_num - act_num) < 0.5


def score_extraction(db: Session, assessment_id: str, labels: EstateLabels) -> dict[str, float]:
    servers = _entity_keys(db, assessment_id, Server)
    apps = _entity_keys(db, assessment_id, Application)
    dbs = _entity_keys(db, assessment_id, DatabaseEntity)

    _, _, server_f1 = prf1(servers, labels.expected_servers)
    _, _, app_f1 = prf1(apps, labels.expected_applications)
    _, _, db_f1 = prf1(dbs, labels.expected_databases)

    # Sizing-field accuracy: over every planted (server, field, value), was the
    # materialized attribute correct once normalized to canonical units?
    server_rows = {
        s.normalized_key: (s.attributes or {})
        for s in db.query(Server).filter(Server.assessment_id == assessment_id).all()
    }
    checks = 0
    correct = 0
    for fact in labels.sizing:
        attrs = server_rows.get(fact.server_key, {})
        for field_name, expected in (
            ("vcpus", fact.vcpus),
            ("memory_gb", fact.memory_gb),
            ("disk_gb", fact.disk_gb),
            ("os", fact.os),
        ):
            if expected is None:
                continue
            checks += 1
            if _values_match(field_name, expected, attrs.get(field_name)):
                correct += 1
    sizing_accuracy = (correct / checks) if checks else 1.0

    claims = db.query(Claim).filter(Claim.assessment_id == assessment_id).all()
    grounded = sum(1 for c in claims if c.evidence_refs)
    grounding_rate = (grounded / len(claims)) if claims else 0.0

    nfr_attrs = {
        c.attribute for c in claims if c.is_selected and c.entity_type == "business"
    }
    _, nfr_recall, _ = prf1(nfr_attrs & labels.expected_nfr_attributes, labels.expected_nfr_attributes)

    recs = {
        r.server_key: r
        for r in db.query(InfrastructureRecommendation)
        .filter(InfrastructureRecommendation.assessment_id == assessment_id)
        .all()
    }
    sized = sum(
        1
        for key in labels.expected_servers
        if key in recs and (recs[key].recommended_sku or "none") != "none"
    )
    sizing_coverage = (sized / len(labels.expected_servers)) if labels.expected_servers else 1.0

    return {
        "server_f1": round(server_f1, 3),
        "application_f1": round(app_f1, 3),
        "database_f1": round(db_f1, 3),
        "sizing_field_accuracy": round(sizing_accuracy, 3),
        "grounding_rate": round(grounding_rate, 3),
        "nfr_coverage": round(nfr_recall, 3),
        "sizing_coverage": round(sizing_coverage, 3),
    }


def run_pipeline(db: Session, assessment_id: str, *, use_mock: bool = True):
    """Run the real ingest → index → extract → reconcile → size pipeline in-process for
    an assessment whose `Document` rows are already attached. Mirrors the pipeline's
    `_execute`; returns the retriever so the caller can score retrieval with it."""
    from app.services.extraction import AssessmentClaimExtractor
    from app.services.extraction_merge import merge_extractions
    from app.services.ingest import ingest_documents
    from app.services.llm_reasoning import get_grounded_prose
    from app.services.providers import get_embedder, get_llm_extractor, get_retriever, get_vector_indexes
    from app.services.questionnaire_extract import sync_uploaded_questions
    from app.services.reconciliation import persist_extraction, persist_inferred_relationships
    from app.services.search import ChunkIndexer
    from app.services.sizing import generate_recommendations

    ingested = ingest_documents(db, assessment_id)
    embedder = get_embedder()
    indexes = get_vector_indexes()
    ChunkIndexer(embedder, indexes).embed_and_index(db, assessment_id)
    retriever = get_retriever(embedder, indexes)
    llm = get_llm_extractor()
    extractor = AssessmentClaimExtractor(retriever, llm, rag_enabled=True, use_mock=use_mock)
    extraction, _ = extractor.extract_assessment(db, assessment_id)
    extraction = merge_extractions([extraction, *ingested.manifest_extractions])
    persist_extraction(db, assessment_id, extraction, prose=get_grounded_prose())
    sync_uploaded_questions(db, assessment_id)
    persist_inferred_relationships(db, assessment_id)
    generate_recommendations(db, assessment_id)
    return retriever


def evaluate(
    db: Session,
    assessment_id: str,
    labels: EstateLabels,
    retriever: Retriever,
    *,
    top_k: int = 8,
) -> EvalReport:
    retrieval, per_probe = score_retrieval(
        db, assessment_id, retriever, labels.retrieval_probes, top_k=top_k
    )
    return EvalReport(
        estate=labels.name,
        retrieval=retrieval,
        extraction=score_extraction(db, assessment_id, labels),
        per_probe=per_probe,
    )
