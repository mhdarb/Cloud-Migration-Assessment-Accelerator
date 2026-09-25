"""Eval harness: pure-metric unit tests + a Contoso regression gate.

The integration test runs the deterministic mock/heuristic pipeline over the committed
`sample-data/` estate and asserts the eval report stays above regression floors — so a
change that quietly degrades retrieval or extraction quality fails CI, and the same
harness (pointed at a real LLM) is how you measure whether the reranker / a stronger
embedder / contextual retrieval actually help.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.models.entities import Document, DocumentType
from app.services.eval_datasets import ALL_ESTATES
from app.services.eval_harness import EstateLabels, evaluate, run_pipeline
from app.services.eval_metrics import mean, precision_at_k, prf1, recall_at_k, reciprocal_rank

SAMPLE_DATA = Path(__file__).resolve().parents[3] / "sample-data"


# --------------------------------------------------------------------------- #
# Pure metrics
# --------------------------------------------------------------------------- #
def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], {"a", "c"}) == 1.0
    assert recall_at_k(["a", "b", "c"], {"a", "z"}) == 0.5
    assert recall_at_k(["a", "b"], {"a", "z"}, k=1) == 0.5
    assert recall_at_k([], {"a"}) == 0.0
    assert recall_at_k([], set()) == 1.0  # nothing relevant -> vacuously perfect


def test_precision_at_k():
    assert precision_at_k(["a", "b", "c", "d"], {"a", "b"}) == 0.5
    assert precision_at_k(["a", "b"], {"a", "b"}) == 1.0
    assert precision_at_k([], {"a"}) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["x", "a", "b"], {"a"}) == 0.5
    assert reciprocal_rank(["a", "b"], {"a"}) == 1.0
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_prf1():
    p, r, f = prf1({"a", "b"}, {"a", "b", "c"})
    assert p == 1.0
    assert r == pytest.approx(2 / 3)
    assert f == pytest.approx(0.8)
    assert prf1(set(), set()) == (1.0, 1.0, 1.0)
    assert prf1({"a"}, set())[2] == 0.0


def test_mean():
    assert mean([1.0, 2.0, 3.0]) == 2.0
    assert mean([]) == 0.0


# --------------------------------------------------------------------------- #
# Multi-estate regression gate (deterministic mock/heuristic pipeline)
# --------------------------------------------------------------------------- #
def _ingest_estate(db_session, assessment_id: str, labels: EstateLabels) -> None:
    base = SAMPLE_DATA / labels.subdir if labels.subdir else SAMPLE_DATA
    for filename, content_type in labels.files:
        path = base / filename
        assert path.exists(), f"missing eval fixture: {labels.subdir}/{filename}"
        db_session.add(
            Document(
                assessment_id=assessment_id,
                filename=filename,
                content_type=content_type,
                storage_path=str(path),
                doc_type=DocumentType.unknown,
            )
        )
    db_session.commit()


@pytest.mark.eval
@pytest.mark.parametrize("estate_name", sorted(ALL_ESTATES))
def test_estate_eval_meets_regression_floors(estate_name, db_session, assessment, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("LOCAL_EMBEDDINGS", "true")
    get_settings.cache_clear()

    labels = ALL_ESTATES[estate_name]
    _ingest_estate(db_session, assessment.id, labels)

    retriever = run_pipeline(db_session, assessment.id, use_mock=True)
    report = evaluate(db_session, assessment.id, labels, retriever, top_k=get_settings().rag_top_k)

    # Print each report so `pytest -s -m eval` doubles as the measurement view.
    print("\n" + report.format_table())

    # Tight per-estate floors just below observed — a regression tripwire, not an
    # aspiration. Contoso's app/db F1 are lower because of known heuristic-extractor noise
    # (the "db-and" pseudo-app from a case-insensitive regex); the cleaner hostname-first
    # (meridian) and discovery-export (atlas) estates hit 1.0, so they're guarded tightly.
    floors = dict(_DEFAULT_FLOORS)
    floors.update(_ESTATE_FLOORS.get(estate_name, {}))
    scores = {**report.retrieval, **report.extraction}
    for metric, floor in floors.items():
        assert scores[metric] >= floor, (estate_name, metric, scores[metric], floor)


# We gate on RECALL (did we find every known-true entity) and the quality metrics; entity
# *precision* is reported but floored only loosely, because a pipeline that also discovers
# real entities the labels don't enumerate (code-snapshot services/infra) is not wrong.
_DEFAULT_FLOORS = {
    "mean_context_recall": 0.90,
    "mean_reciprocal_rank": 0.80,
    "server_recall": 1.00,
    "application_recall": 1.00,
    "database_recall": 1.00,
    "server_precision": 0.90,
    "application_precision": 0.90,
    "database_precision": 0.90,
    "sizing_field_accuracy": 0.90,
    "grounding_rate": 0.90,
    "nfr_coverage": 0.90,
    "sizing_coverage": 0.70,
}
# Per-estate overrides where a metric is legitimately lower than the strict default:
#  - contoso: its code snapshot (sample-app.zip) legitimately yields entities the
#    doc-derived labels don't list — the `contoso-billing-api` package and postgres/redis
#    deps — so precision sits below 1.0 (recall stays 1.0). Placeholder names from the
#    manifest parsers ("Container App", compose "api", "snapshot-app") are now folded into
#    the concrete service; the application floor is set to catch them if they return.
#  - orion: 3 of 8 hosts are intentionally unsizable (AIX + Solaris unsupported, plus a
#    16-vCPU DB host exceeding the local catalog), so sizing_coverage floors at 0.625.
_ESTATE_FLOORS = {
    "contoso": {"application_precision": 0.75, "database_precision": 0.50},
    "orion": {"sizing_coverage": 0.60},
}
