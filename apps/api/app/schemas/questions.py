from __future__ import annotations

from pydantic import BaseModel


class InventorySummary(BaseModel):
    """Thin, cheap derived view of an assessment's reconciled inventory, used to
    seed the QuestionPlanner — not a new heavy type, just the fields planners need."""

    application_count: int = 0
    server_count: int = 0
    database_count: int = 0
    unsupported_os_servers: list[str] = []
    open_conflict_count: int = 0
    nfr_gaps: list[str] = []


class PlannedQuestion(BaseModel):
    question: str
    rationale: str = ""
