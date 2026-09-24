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
from app.services.eval_datasets import CONTOSO
from app.services.eval_harness import evaluate, run_pipeline
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
# Contoso regression gate (deterministic mock/heuristic pipeline)
# --------------------------------------------------------------------------- #
@pytest.mark.eval
def test_contoso_eval_meets_regression_floors(db_session, assessment, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("LOCAL_EMBEDDINGS", "true")
    get_settings.cache_clear()

    for filename, content_type in CONTOSO.files:
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

    retriever = run_pipeline(db_session, assessment.id, use_mock=True)
    report = evaluate(db_session, assessment.id, CONTOSO, retriever, top_k=get_settings().rag_top_k)

    # Print the full report so `pytest -s -m eval` doubles as the measurement view.
    print("\n" + report.format_table())

    ret = report.retrieval
    ext = report.extraction
    # Floors are set just below what the deterministic path currently achieves — a
    # regression tripwire, not an aspiration. Raise them as quality improves.
    assert ret["mean_context_recall"] >= 0.80, ret
    assert ret["mean_reciprocal_rank"] >= 0.80, ret
    assert ext["server_f1"] >= 0.90, ext
    assert ext["sizing_field_accuracy"] >= 0.85, ext
    assert ext["grounding_rate"] >= 0.60, ext
    assert ext["nfr_coverage"] >= 0.85, ext
    assert ext["sizing_coverage"] >= 0.75, ext
    # application/database F1 are capped (~0.67 / ~0.75) by known noise entities the
    # heuristic extractor emits (e.g. the "db-and" pseudo-app from a case-insensitive
    # regex). The harness now *quantifies* that precision gap — a prime candidate for the
    # next quality pass. These floors guard against it getting worse.
    assert ext["application_f1"] >= 0.60, ext
    assert ext["database_f1"] >= 0.70, ext
