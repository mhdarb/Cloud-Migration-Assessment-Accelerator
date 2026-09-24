"""Pure, dependency-free scoring primitives for the RAG/extraction eval harness.

Kept separate from `eval_harness` (which touches the DB and pipeline) so these can be
unit-tested in isolation and reused for any labeled set. All functions are deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int | None = None) -> float:
    """Fraction of the relevant items that appear in the top-k retrieved. Vacuously 1.0
    when nothing is relevant (there was nothing to miss)."""
    rel = set(relevant)
    if not rel:
        return 1.0
    top = set(retrieved[:k] if k is not None else retrieved)
    return len(top & rel) / len(rel)


def precision_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int | None = None) -> float:
    """Fraction of the top-k retrieved that are relevant. 0.0 when nothing was retrieved."""
    top = list(retrieved[:k] if k is not None else retrieved)
    if not top:
        return 0.0
    rel = set(relevant)
    return sum(1 for item in top if item in rel) / len(top)


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str]) -> float:
    """1/rank of the first relevant hit (rank starts at 1); 0.0 if none is relevant."""
    rel = set(relevant)
    for i, item in enumerate(retrieved):
        if item in rel:
            return 1.0 / (i + 1)
    return 0.0


def prf1(predicted: Iterable[str], expected: Iterable[str]) -> tuple[float, float, float]:
    """(precision, recall, F1) between a predicted and an expected set of labels."""
    pred, exp = set(predicted), set(expected)
    if not pred and not exp:
        return (1.0, 1.0, 1.0)
    tp = len(pred & exp)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(exp) if exp else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return (precision, recall, f1)


def mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0
