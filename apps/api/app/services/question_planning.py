"""QuestionPlanner implementations (Strategy pattern) + the InventorySummary builder
that feeds them. `HeuristicQuestionPlanner` is the deterministic default (graceful
degradation — works with no chat LLM configured); `LlmQuestionPlanner` proposes
estate-specific follow-ups and falls back to it when disabled/unavailable/fails;
`NoOpQuestionPlanner` is the Null Object used when the feature is turned off."""

from __future__ import annotations

import json

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.schemas.questions import InventorySummary, PlannedQuestion
from app.services.compat import call_if_supported
from app.services.inventory import load_inventory
from app.services.ports import ChatCompleter, QuestionPlanner

_NFR_LIKE_ATTRS = {
    "sla",
    "availability",
    "latency",
    "rto",
    "rpo",
    "scalability",
    "compliance",
    "encryption",
    "data_residency",
}


def build_inventory_summary(db: Session, assessment_id: str) -> InventorySummary:
    snap = load_inventory(db, assessment_id, claims=True, conflicts=True, recommendations=True)
    unsupported = [
        r.server_key for r in snap.recommendations if (r.result or {}).get("sku_decision") == "blocked"
    ]
    open_conflicts = sum(1 for c in snap.conflicts if c.status.value == "open")
    covered = {c.attribute for c in snap.claims if c.is_selected and c.attribute in _NFR_LIKE_ATTRS}
    missing_nfr = sorted(_NFR_LIKE_ATTRS - covered)
    return InventorySummary(
        application_count=len(snap.applications),
        server_count=len(snap.servers),
        database_count=len(snap.databases),
        unsupported_os_servers=unsupported,
        open_conflict_count=open_conflicts,
        nfr_gaps=missing_nfr,
    )


class NoOpQuestionPlanner:
    """Null Object — used when `question_planner_enabled=False`."""

    def plan(self, summary: InventorySummary) -> list[PlannedQuestion]:
        return []


class HeuristicQuestionPlanner:
    """Deterministic default: templated questions from clear signals already in the
    reconciled data. No LLM required."""

    def plan(self, summary: InventorySummary) -> list[PlannedQuestion]:
        out: list[PlannedQuestion] = []
        if summary.unsupported_os_servers:
            servers = ", ".join(summary.unsupported_os_servers[:5])
            out.append(
                PlannedQuestion(
                    question=(
                        f"{len(summary.unsupported_os_servers)} server(s) ({servers}) have no "
                        "Azure-supported target — what is the replatform plan?"
                    ),
                    rationale="Sizing found unsupported OS(es) with no compatible SKU",
                )
            )
        if summary.open_conflict_count:
            out.append(
                PlannedQuestion(
                    question=(
                        f"There are {summary.open_conflict_count} unresolved conflicting fact(s) "
                        "across source documents — who can confirm the correct values?"
                    ),
                    rationale="Open conflicts remain after precedence-based reconciliation",
                )
            )
        if summary.nfr_gaps:
            out.append(
                PlannedQuestion(
                    question=(
                        f"No evidence was found for: {', '.join(summary.nfr_gaps)} — are these "
                        "requirements applicable, and if so what are the target values?"
                    ),
                    rationale="No selected claims found for these NFR/compliance attributes",
                )
            )
        return out


class _PlannedQuestionsResponse(BaseModel):
    questions: list[PlannedQuestion] = []


class LlmQuestionPlanner:
    """Proposes estate-specific supplementary questions via structured output."""

    def __init__(self, completer: ChatCompleter, fallback: QuestionPlanner | None = None) -> None:
        self._completer = completer
        self._fallback = fallback or HeuristicQuestionPlanner()

    def plan(self, summary: InventorySummary) -> list[PlannedQuestion]:
        if not self._completer.enabled:
            return self._fallback.plan(summary)
        system = (
            "You propose estate-specific supplementary cloud migration assessment "
            "questions for a human stakeholder, based ONLY on the supplied summary. "
            "Do not invent facts not present in the summary. Propose at most 5 short, "
            "concrete questions, each with a one-sentence rationale."
        )
        user = json.dumps(summary.model_dump())
        content = call_if_supported(
            self._completer.complete,
            system,
            user,
            temperature=0.3,
            json_mode=True,
            response_schema=_PlannedQuestionsResponse,
        )
        if not content:
            return self._fallback.plan(summary)
        try:
            parsed = _PlannedQuestionsResponse.model_validate(json.loads(content))
        except Exception:
            return self._fallback.plan(summary)
        return parsed.questions or self._fallback.plan(summary)
