"""Human review: decisions, the conflict lifecycle, durability across re-runs, and the
"migration-ready" gate.

Everything a reviewer acts on — claims, conflicts, dependency edges, sizing
recommendations — is *derived* data that each pipeline run deletes and rebuilds. So a
decision is applied to the live row **and** recorded as a `ReviewDecision` keyed by a
stable identity; after every run the recorded decisions are re-applied to the freshly
extracted rows. See `ReviewDecision` for the key formats.

Conflict lifecycle (claims that disagree about one attribute):

  accept/override a candidate -> conflict `resolved`, that claim selected, the other
                                 candidates `superseded` (no longer in the queue)
  reject the *selected* claim  -> conflict re-`open`ed, nothing selected, superseded
                                 candidates put back in the queue
  reject another candidate     -> that claim only; the conflict still needs a decision
  dismiss the conflict         -> `dismissed`: no value selected, the attribute is
                                 deliberately unknown, every candidate `dismissed`
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import (
    Claim,
    Conflict,
    ConflictStatus,
    DependencyEdge,
    InfrastructureRecommendation,
    ReviewDecision,
    ReviewStatus,
)
from app.services.normalization import MEASURED_FIELDS, comparison_value, is_plausible, to_canonical

CLAIM_ACTIONS = frozenset({"accept", "override", "reject"})
DECISION_ACTIONS = frozenset({"accept", "reject"})  # edges and recommendations


# --------------------------------------------------------------------------- #
# Stable keys (how a decision finds "the same item" after a re-run)
# --------------------------------------------------------------------------- #
def claim_key(claim: Claim) -> str:
    # Unit-normalized, so "16 GB" and "16384 MB" re-extracted later match one decision.
    value = comparison_value(claim.attribute, claim.value)
    return f"{claim.entity_type}|{claim.entity_key}|{claim.attribute}|{value}"


def conflict_key(conflict: Conflict) -> str:
    return f"{conflict.entity_type}|{conflict.entity_key}|{conflict.attribute}"


def edge_key(edge: DependencyEdge) -> str:
    return "|".join(
        (edge.source_type, edge.source_key, edge.rel_type, edge.target_type, edge.target_key)
    )


def recommendation_key(rec: InfrastructureRecommendation) -> str:
    # Includes the SKU: if new evidence changes the sizing, the old sign-off no longer
    # applies and the recommendation goes back into the queue.
    return f"{rec.server_key}|{rec.recommended_sku}"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_claim_action(claim: Claim, action: str, override_value: str | None) -> str:
    action = (action or "").strip().lower()
    if action not in CLAIM_ACTIONS:
        raise ValueError("action must be accept, override, or reject")
    if action == "override":
        if not override_value or not override_value.strip():
            raise ValueError("override_value required for override action")
        if claim.attribute in MEASURED_FIELDS:
            number = to_canonical(claim.attribute, override_value)
            if number is None:
                raise ValueError(
                    f"'{override_value}' is not a number for {claim.attribute} "
                    "(units such as 'GB', 'MB', 'TB' or '%' are fine)"
                )
            if not is_plausible(claim.attribute, number):
                raise ValueError(
                    f"'{override_value}' is outside the plausible range for {claim.attribute}"
                )
    return action


def validate_decision_action(action: str) -> str:
    action = (action or "").strip().lower()
    if action not in DECISION_ACTIONS:
        raise ValueError("action must be accept or reject")
    return action


# --------------------------------------------------------------------------- #
# Recording decisions (the durable audit trail)
# --------------------------------------------------------------------------- #
def record_decision(
    db: Session,
    assessment_id: str,
    target_type: str,
    target_key: str,
    action: str,
    *,
    override_value: str | None = None,
    notes: str | None = None,
    reviewer: str | None = None,
) -> ReviewDecision:
    """Upsert: the latest decision on an item replaces the earlier one."""
    row = (
        db.query(ReviewDecision)
        .filter(
            ReviewDecision.assessment_id == assessment_id,
            ReviewDecision.target_type == target_type,
            ReviewDecision.target_key == target_key,
        )
        .one_or_none()
    )
    if row is None:
        row = ReviewDecision(
            assessment_id=assessment_id, target_type=target_type, target_key=target_key
        )
        db.add(row)
    row.action = action
    row.override_value = override_value
    row.notes = notes
    row.reviewed_by = reviewer
    row.updated_at = datetime.utcnow()
    return row


# --------------------------------------------------------------------------- #
# Applying decisions to live rows (no commit — callers own the transaction)
# --------------------------------------------------------------------------- #
def _conflicts_containing(db: Session, claim: Claim) -> list[Conflict]:
    rows = db.query(Conflict).filter(Conflict.assessment_id == claim.assessment_id).all()
    return [c for c in rows if claim.id in (c.claim_ids or [])]


def _claims_by_id(db: Session, ids: list[str]) -> list[Claim]:
    return db.query(Claim).filter(Claim.id.in_(ids)).all() if ids else []


def apply_claim_decision(
    db: Session,
    claim: Claim,
    action: str,
    *,
    override_value: str | None = None,
    notes: str | None = None,
    reviewer: str | None = None,
    at: datetime | None = None,
) -> Claim:
    at = at or datetime.utcnow()
    if action == "accept":
        claim.review_status = ReviewStatus.accepted
        claim.is_selected = True
    elif action == "override":
        claim.review_status = ReviewStatus.overridden
        claim.override_value = override_value
        claim.is_selected = True
    elif action == "reject":
        claim.review_status = ReviewStatus.rejected
        claim.is_selected = False
    else:
        raise ValueError("action must be accept, override, or reject")
    claim.needs_human_review = False
    claim.review_notes = notes
    claim.reviewed_at = at
    claim.reviewed_by = reviewer

    for conflict in _conflicts_containing(db, claim):
        siblings = [c for c in _claims_by_id(db, conflict.claim_ids or []) if c.id != claim.id]
        if action in {"accept", "override"}:
            conflict.status = ConflictStatus.resolved
            conflict.selected_claim_id = claim.id
            conflict.resolution_notes = notes or f"Resolved via human {action}"
            for sib in siblings:
                sib.is_selected = False
                sib.needs_human_review = False
                if sib.review_status != ReviewStatus.rejected:
                    sib.review_status = ReviewStatus.superseded
        elif conflict.selected_claim_id == claim.id:
            # The value this conflict settled on was just rejected: the conflict is no
            # longer resolved. Reopen it and put the other candidates back in the queue so
            # the attribute can't silently end up empty while the gate reads "clear".
            conflict.status = ConflictStatus.open
            conflict.selected_claim_id = None
            conflict.resolution_notes = "Reopened: the selected value was rejected"
            for sib in siblings:
                if sib.review_status in {
                    ReviewStatus.superseded,
                    ReviewStatus.dismissed,
                    ReviewStatus.pending,
                }:
                    sib.review_status = ReviewStatus.pending
                    sib.needs_human_review = True
                    sib.is_selected = False
    return claim


def apply_conflict_dismissal(
    db: Session,
    conflict: Conflict,
    *,
    notes: str | None = None,
    reviewer: str | None = None,
    at: datetime | None = None,
) -> Conflict:
    at = at or datetime.utcnow()
    conflict.status = ConflictStatus.dismissed
    conflict.selected_claim_id = None
    conflict.resolution_notes = notes or "Dismissed by reviewer: no candidate value is correct"
    for claim in _claims_by_id(db, conflict.claim_ids or []):
        claim.is_selected = False
        claim.needs_human_review = False
        if claim.review_status != ReviewStatus.rejected:
            claim.review_status = ReviewStatus.dismissed
        claim.reviewed_at = at
        claim.reviewed_by = reviewer
    return conflict


def apply_item_decision(
    item: DependencyEdge | InfrastructureRecommendation,
    action: str,
    *,
    notes: str | None = None,
    reviewer: str | None = None,
    at: datetime | None = None,
) -> Any:
    item.review_status = ReviewStatus.accepted if action == "accept" else ReviewStatus.rejected
    item.needs_human_review = False
    item.review_notes = notes
    item.reviewed_at = at or datetime.utcnow()
    item.reviewed_by = reviewer
    return item


# --------------------------------------------------------------------------- #
# Re-applying recorded decisions after a pipeline run
# --------------------------------------------------------------------------- #
def _decisions(db: Session, assessment_id: str, *types: str) -> list[ReviewDecision]:
    return (
        db.query(ReviewDecision)
        .filter(ReviewDecision.assessment_id == assessment_id, ReviewDecision.target_type.in_(types))
        .order_by(ReviewDecision.updated_at, ReviewDecision.created_at)
        .all()
    )


def reapply_claim_decisions(db: Session, assessment_id: str) -> int:
    """Re-apply claim and conflict decisions to freshly extracted claims, oldest first so
    the reviewer's most recent intent wins. Returns how many rows were touched."""
    decisions = _decisions(db, assessment_id, "claim", "conflict")
    if not decisions:
        return 0
    claims_by_key: dict[str, list[Claim]] = {}
    for claim in db.query(Claim).filter(Claim.assessment_id == assessment_id).all():
        claims_by_key.setdefault(claim_key(claim), []).append(claim)
    conflicts_by_key: dict[str, list[Conflict]] = {}
    for conflict in db.query(Conflict).filter(Conflict.assessment_id == assessment_id).all():
        conflicts_by_key.setdefault(conflict_key(conflict), []).append(conflict)

    applied = 0
    for d in decisions:
        if d.target_type == "claim":
            for claim in claims_by_key.get(d.target_key, []):
                apply_claim_decision(
                    db, claim, d.action, override_value=d.override_value,
                    notes=d.notes, reviewer=d.reviewed_by, at=d.updated_at,
                )
                applied += 1
        else:  # conflict dismissal
            for conflict in conflicts_by_key.get(d.target_key, []):
                apply_conflict_dismissal(
                    db, conflict, notes=d.notes, reviewer=d.reviewed_by, at=d.updated_at
                )
                applied += 1
        db.flush()
    return applied


def reapply_edge_decisions(db: Session, assessment_id: str) -> int:
    decisions = {d.target_key: d for d in _decisions(db, assessment_id, "edge")}
    if not decisions:
        return 0
    applied = 0
    for edge in db.query(DependencyEdge).filter(DependencyEdge.assessment_id == assessment_id).all():
        d = decisions.get(edge_key(edge))
        if d:
            apply_item_decision(edge, d.action, notes=d.notes, reviewer=d.reviewed_by, at=d.updated_at)
            applied += 1
    return applied


def reapply_recommendation_decisions(db: Session, assessment_id: str) -> int:
    decisions = {d.target_key: d for d in _decisions(db, assessment_id, "recommendation")}
    if not decisions:
        return 0
    applied = 0
    rows = (
        db.query(InfrastructureRecommendation)
        .filter(InfrastructureRecommendation.assessment_id == assessment_id)
        .all()
    )
    for rec in rows:
        d = decisions.get(recommendation_key(rec))
        if d:
            apply_item_decision(rec, d.action, notes=d.notes, reviewer=d.reviewed_by, at=d.updated_at)
            applied += 1
    return applied


# --------------------------------------------------------------------------- #
# The "migration-ready" gate
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReviewQueueStatus:
    pending_claims: int
    open_conflicts: int
    pending_edges: int
    pending_recommendations: int

    @property
    def clear(self) -> bool:
        return not (
            self.pending_claims or self.open_conflicts or self.pending_edges or self.pending_recommendations
        )

    def describe(self) -> str:
        parts = [
            (self.pending_claims, "claim(s) need review"),
            (self.open_conflicts, "open conflict(s)"),
            (self.pending_edges, "dependency edge(s) need review"),
            (self.pending_recommendations, "sizing recommendation(s) need review"),
        ]
        return ", ".join(f"{n} {label}" for n, label in parts if n) or "nothing pending"


def review_queue_status(db: Session, assessment_id: str) -> ReviewQueueStatus:
    return ReviewQueueStatus(
        pending_claims=db.query(Claim)
        .filter(Claim.assessment_id == assessment_id, Claim.needs_human_review.is_(True))
        .count(),
        open_conflicts=db.query(Conflict)
        .filter(Conflict.assessment_id == assessment_id, Conflict.status == ConflictStatus.open)
        .count(),
        pending_edges=db.query(DependencyEdge)
        .filter(DependencyEdge.assessment_id == assessment_id, DependencyEdge.needs_human_review.is_(True))
        .count(),
        pending_recommendations=db.query(InfrastructureRecommendation)
        .filter(
            InfrastructureRecommendation.assessment_id == assessment_id,
            InfrastructureRecommendation.needs_human_review.is_(True),
        )
        .count(),
    )
