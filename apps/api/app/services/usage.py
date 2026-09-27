"""LLM and embedding usage metering: calls, tokens and estimated cost per assessment run.

Every chat completion and embedding request in the app goes through exactly two places —
`OpenAICompatibleCompleter._call` and `AzureEmbedder.embed_texts` — and both report to
the *active meter*. A meter is bound with `metered(...)`:

  * once per pipeline run (scope "pipeline"; the pipeline also labels the current
    stage, so cost can be broken down: classify / extract / reconcile / graph / report),
  * once per API request on an assessment (scope "interactive": review clicks,
    questions, questionnaire answers — usage *after* a run).

The meter lives in a ContextVar. LangGraph runs graph nodes on worker threads but copies
the context into them, so calls made anywhere inside a run are attributed to it.

On exit the meter writes one `LlmUsage` row per (stage, kind, model), priced with the
configured rates at that moment — so a past run keeps the cost it was charged at even
if the rates are changed later.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_ACTIVE: contextvars.ContextVar[UsageMeter | None] = contextvars.ContextVar("usage_meter", default=None)


@dataclass
class _Counter:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_calls: int = 0  # provider returned no usage; tokens were estimated


@dataclass
class UsageMeter:
    assessment_id: str
    scope: str  # "pipeline" | "interactive"
    run_started_at: str | None = None
    stage: str = "other"
    # (stage, kind, provider, model) -> counter; kind = "chat" | "embedding"
    counters: dict[tuple[str, str, str, str], _Counter] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _bump(self, kind: str, provider: str, model: str, inp: int, out: int, estimated: bool) -> None:
        with self._lock:
            counter = self.counters.setdefault((self.stage, kind, provider, model), _Counter())
            counter.calls += 1
            counter.input_tokens += inp
            counter.output_tokens += out
            counter.estimated_calls += int(estimated)

    @property
    def empty(self) -> bool:
        return not self.counters


def current() -> UsageMeter | None:
    return _ACTIVE.get()


@contextmanager
def metered(assessment_id: str, scope: str, run_started_at: str | None = None) -> Iterator[UsageMeter]:
    meter = UsageMeter(assessment_id=assessment_id, scope=scope, run_started_at=run_started_at)
    token = _ACTIVE.set(meter)
    try:
        yield meter
    finally:
        _ACTIVE.reset(token)


def set_stage(stage: str) -> None:
    meter = _ACTIVE.get()
    if meter is not None:
        meter.stage = stage


# --------------------------------------------------------------------------- #
# Recording (called from the two call sites)
# --------------------------------------------------------------------------- #
def _estimate_tokens(text: str) -> int:
    try:
        from app.services.chunkers import _count_tokens, _get_encoding

        return int(_count_tokens(text, _get_encoding()))
    except Exception:
        return max(1, len(text) // 4)


def record_chat(provider: str, model: str, response: Any, messages: list[dict[str, Any]]) -> None:
    meter = _ACTIVE.get()
    if meter is None:
        return
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    estimated = prompt is None or completion is None
    if estimated:
        prompt = sum(_estimate_tokens(str(m.get("content") or "")) for m in messages)
        try:
            completion = _estimate_tokens(response.choices[0].message.content or "")
        except Exception:
            completion = 0
    meter._bump("chat", provider, model, int(prompt or 0), int(completion or 0), estimated)


def record_embedding(provider: str, model: str, response: Any, texts: list[str]) -> None:
    meter = _ACTIVE.get()
    if meter is None:
        return
    usage = getattr(response, "usage", None) if response is not None else None
    tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "total_tokens", None)
    estimated = tokens is None and provider != "local"
    if tokens is None:
        tokens = 0 if provider == "local" else sum(_estimate_tokens(t) for t in texts)
    meter._bump("embedding", provider, model, int(tokens), 0, estimated)


# --------------------------------------------------------------------------- #
# Pricing + persistence
# --------------------------------------------------------------------------- #
def price(kind: str, provider: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated cost with the configured per-million-token rates. Local models are free."""
    if provider == "local":
        return 0.0
    s = get_settings()
    if kind == "chat":
        return (input_tokens * s.llm_price_input_per_million + output_tokens * s.llm_price_output_per_million) / 1e6
    return input_tokens * s.embedding_price_per_million / 1e6


def flush(meter: UsageMeter, session_factory: Callable[[], Any] | None = None) -> int:
    """Write the meter's counters as `LlmUsage` rows (own session). Returns rows written.
    Never raises: losing a usage row must not fail the request or run it describes."""
    if meter.empty:
        return 0
    from app.models.entities import LlmUsage

    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal
    currency = get_settings().usage_currency
    db = session_factory()
    try:
        for (stage, kind, provider, model), c in meter.counters.items():
            db.add(
                LlmUsage(
                    assessment_id=meter.assessment_id,
                    scope=meter.scope,
                    run_started_at=meter.run_started_at,
                    stage=stage,
                    kind=kind,
                    provider=provider,
                    model=model,
                    calls=c.calls,
                    input_tokens=c.input_tokens,
                    output_tokens=c.output_tokens,
                    estimated_calls=c.estimated_calls,
                    cost=round(price(kind, provider, c.input_tokens, c.output_tokens), 6),
                    currency=currency,
                )
            )
        db.commit()
        return len(meter.counters)
    except Exception:
        db.rollback()
        logger.exception("Could not record LLM usage for assessment %s", meter.assessment_id)
        return 0
    finally:
        db.close()


def flush_interactive(meter: UsageMeter) -> int:
    """Attribute a request's usage to the assessment's current run, then persist it."""
    if meter.empty:
        return 0
    from app.database import SessionLocal
    from app.models.entities import Assessment

    db = SessionLocal()
    try:
        started = db.query(Assessment.pipeline_started_at).filter(Assessment.id == meter.assessment_id).scalar()
    except Exception:
        # Like `flush`: recording usage must never fail the request it describes.
        logger.exception("Could not attribute LLM usage for assessment %s", meter.assessment_id)
        return 0
    finally:
        db.close()
    meter.run_started_at = started.isoformat() if started else None
    return flush(meter)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _totals(rows: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "chat": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0},
        "embeddings": {"calls": 0, "tokens": 0, "cost": 0.0},
        "total_cost": 0.0,
        "estimated": False,
    }
    for r in rows:
        if r.kind == "chat":
            out["chat"]["calls"] += r.calls
            out["chat"]["input_tokens"] += r.input_tokens
            out["chat"]["output_tokens"] += r.output_tokens
            out["chat"]["cost"] += r.cost
        else:
            out["embeddings"]["calls"] += r.calls
            out["embeddings"]["tokens"] += r.input_tokens
            out["embeddings"]["cost"] += r.cost
        out["total_cost"] += r.cost
        out["estimated"] = out["estimated"] or r.estimated_calls > 0
    for part in (out["chat"], out["embeddings"]):
        part["cost"] = round(part["cost"], 4)
    out["total_cost"] = round(out["total_cost"], 4)
    return out


STAGE_LABELS = {
    "ingesting": "Classify documents",
    "indexing": "Embed & index",
    "extracting": "Extract facts",
    "reconciling": "Reconcile & plan questions",
    "building_graph": "Infer relationships",
    "generating_report": "Sizing & report",
    "other": "Other",
}


def usage_summary(db: Any, assessment: Any, *, history: int = 10) -> dict[str, Any]:
    from app.models.entities import LlmUsage

    s = get_settings()
    rows = db.query(LlmUsage).filter(LlmUsage.assessment_id == assessment.id).all()
    current_run = assessment.pipeline_started_at.isoformat() if assessment.pipeline_started_at else None
    run_rows = [r for r in rows if r.scope == "pipeline" and r.run_started_at == current_run]
    after_rows = [r for r in rows if r.scope == "interactive" and r.run_started_at == current_run]

    stages: dict[str, list[Any]] = {}
    for r in run_rows:
        stages.setdefault(r.stage, []).append(r)
    by_stage = [
        {"stage": stage, "label": STAGE_LABELS.get(stage, stage), **_totals(items)}
        for stage, items in sorted(
            stages.items(), key=lambda kv: list(STAGE_LABELS).index(kv[0]) if kv[0] in STAGE_LABELS else 99
        )
    ]
    runs: dict[str, list[Any]] = {}
    for r in rows:
        if r.scope == "pipeline" and r.run_started_at:
            runs.setdefault(r.run_started_at, []).append(r)
    past = [
        {"run_started_at": started, **_totals(items)} for started, items in sorted(runs.items(), reverse=True)[:history]
    ]
    return {
        "currency": s.usage_currency,
        "rates": {
            "llm_input_per_million": s.llm_price_input_per_million,
            "llm_output_per_million": s.llm_price_output_per_million,
            "embedding_per_million": s.embedding_price_per_million,
        },
        "models": sorted({f"{r.kind}: {r.model}" for r in rows}),
        "latest_run": {"run_started_at": current_run, **_totals(run_rows), "by_stage": by_stage},
        "since_run": _totals(after_rows),
        "all_time": _totals(rows),
        "runs": past,
    }
