from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import Assessment, Claim, ReviewStatus, WorkflowStage
from app.services.review import review_queue_status


def complete_review(db: Session, assessment: Assessment) -> Assessment:
    """Advance to follow_up once nothing is pending review: claims, open conflicts,
    low-confidence dependency edges, and flagged sizing recommendations."""
    settings = get_settings()
    queue = review_queue_status(db, assessment.id)
    if settings.enforce_review and not queue.clear:
        raise ValueError(
            f"Review incomplete: {queue.describe()}. Resolve them before marking ready."
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
    """Follow-up feed entry for a claim decision. Every action is logged (accepts too) —
    the complete, uncapped audit trail lives in the `review_decisions` table; this capped
    log is the human-readable activity feed shown in the UI."""
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
            "reviewed_by": claim.reviewed_by,
        },
    )
