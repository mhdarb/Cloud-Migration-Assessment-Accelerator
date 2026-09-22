from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import (
    Assessment,
    Claim,
    Conflict,
    ConflictStatus,
    ReviewStatus,
    WorkflowStage,
)


def review_queue_clear(db: Session, assessment_id: str) -> tuple[int, int]:
    pending = (
        db.query(Claim)
        .filter(
            Claim.assessment_id == assessment_id,
            Claim.needs_human_review.is_(True),
        )
        .count()
    )
    open_conflicts = (
        db.query(Conflict)
        .filter(
            Conflict.assessment_id == assessment_id,
            Conflict.status == ConflictStatus.open,
        )
        .count()
    )
    return pending, open_conflicts


def complete_review(db: Session, assessment: Assessment) -> Assessment:
    """Advance to follow_up when review queue and open conflicts are clear."""
    settings = get_settings()
    pending, open_conflicts = review_queue_clear(db, assessment.id)
    if settings.enforce_review and (pending or open_conflicts):
        raise ValueError(
            f"Review incomplete: {pending} claim(s) need review, "
            f"{open_conflicts} open conflict(s). Resolve them before marking ready."
        )
    assessment.workflow_stage = WorkflowStage.follow_up
    metrics = dict(assessment.metrics or {})
    metrics["review_completed_at"] = datetime.now(UTC).isoformat()
    assessment.metrics = metrics
    return assessment


def append_follow_up(
    assessment: Assessment,
    *,
    event: str,
    detail: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    metrics = dict(assessment.metrics or {})
    log = list(metrics.get("follow_up_log") or [])
    entry = {
        "at": datetime.now(UTC).isoformat(),
        "event": event,
        "detail": detail or {},
    }
    log.append(entry)
    metrics["follow_up_log"] = log[-200:]
    assessment.metrics = metrics
    return metrics["follow_up_log"]


def capture_review_learning(
    assessment: Assessment,
    claim: Claim,
    action: str,
) -> None:
    """Follow-up: record rejects/overrides as reusable learning signals."""
    if action not in {"reject", "override"}:
        return
    append_follow_up(
        assessment,
        event=f"claim_{action}",
        detail={
            "claim_id": claim.id,
            "entity_type": claim.entity_type,
            "entity_key": claim.entity_key,
            "attribute": claim.attribute,
            "value": claim.value,
            "override_value": claim.override_value,
            "review_status": (
                claim.review_status.value
                if isinstance(claim.review_status, ReviewStatus)
                else str(claim.review_status)
            ),
            "notes": claim.review_notes,
        },
    )
