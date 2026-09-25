from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import (
    Application,
    Claim,
    Conflict,
    ConflictStatus,
    DatabaseEntity,
    DependencyEdge,
    Document,
    Interface,
    ReviewStatus,
    Server,
)
from app.schemas.api import ExtractionResult
from app.services.inventory import load_inventory
from app.services.llm_reasoning import GroundedProse, get_grounded_prose
from app.services.normalization import comparison_value


def persist_extraction(
    db: Session,
    assessment_id: str,
    result: ExtractionResult,
    *,
    prose: GroundedProse | None = None,
) -> None:
    settings = get_settings()
    docs = {
        d.id: d
        for d in db.query(Document).filter(Document.assessment_id == assessment_id).all()
    }
    chunk_to_doc = {}
    from app.models.entities import Chunk

    for ch in db.query(Chunk).filter(Chunk.assessment_id == assessment_id).all():
        chunk_to_doc[ch.id] = ch.document_id

    claim_rows: list[Claim] = []
    for extracted in result.claims:
        source_doc_id = None
        if extracted.chunk_ids:
            source_doc_id = chunk_to_doc.get(extracted.chunk_ids[0])
        unsupported = not extracted.chunk_ids
        confidence = extracted.confidence
        if unsupported:
            confidence = min(confidence, 0.4)

        needs_review = confidence < settings.confidence_review_threshold or unsupported
        claim = Claim(
            assessment_id=assessment_id,
            entity_type=extracted.entity_type,
            entity_key=extracted.entity_key,
            attribute=extracted.attribute,
            value=extracted.value,
            confidence=confidence,
            evidence_refs=extracted.chunk_ids,
            evidence_quote=extracted.evidence_quote,
            source_document_id=source_doc_id,
            needs_human_review=needs_review,
            unsupported=unsupported,
            review_status=ReviewStatus.pending,
            is_selected=True,
        )
        claim_rows.append(claim)
        db.add(claim)

    db.flush()

    # Group and reconcile conflicts
    groups: dict[tuple[str, str, str], list[Claim]] = defaultdict(list)
    for claim in claim_rows:
        groups[(claim.entity_type, claim.entity_key, claim.attribute)].append(claim)

    for (etype, ekey, attr), group in groups.items():
        # Compare on the canonical value so a measured field expressed differently across
        # sources ("16 GB" vs "16384 MB" vs "16.0") is recognized as agreement, not raised
        # as a spurious conflict — while a genuine magnitude difference still is.
        values = {comparison_value(attr, c.value) for c in group}
        if len(values) <= 1:
            continue

        # Prefer higher precedence document, then higher confidence
        def score(c: Claim) -> tuple[int, float]:
            prec = 50
            if c.source_document_id and c.source_document_id in docs:
                prec = docs[c.source_document_id].precedence
            return (prec, c.confidence)

        ranked = sorted(group, key=score, reverse=True)
        winner = ranked[0]
        for c in group:
            c.is_selected = c.id == winner.id
            if c.id != winner.id:
                c.needs_human_review = True

        fallback_notes = "Auto-selected by document precedence then confidence"
        notes_payload = {
            "entity": f"{etype}:{ekey}.{attr}",
            "winner_value": winner.override_value or winner.value,
            "values": [
                {
                    "value": c.override_value or c.value,
                    "confidence": c.confidence,
                    "quote": c.evidence_quote,
                    "filename": (
                        docs[c.source_document_id].filename
                        if c.source_document_id and c.source_document_id in docs
                        else None
                    ),
                    "selected": c.id == winner.id,
                }
                for c in ranked
            ],
        }
        conflict = Conflict(
            assessment_id=assessment_id,
            entity_type=etype,
            entity_key=ekey,
            attribute=attr,
            claim_ids=[c.id for c in group],
            selected_claim_id=winner.id,
            status=ConflictStatus.open,
            resolution_notes=(prose or get_grounded_prose()).explain_conflict(
                notes_payload, fallback_notes
            ),
        )
        db.add(conflict)

    # Materialize entities from selected name claims + attributes
    selected = [c for c in claim_rows if c.is_selected]
    _materialize_entities(db, assessment_id, selected)

    # Edges
    for dep in result.dependencies:
        edge = DependencyEdge(
            assessment_id=assessment_id,
            source_type=dep.source_type,
            source_key=dep.source_key,
            target_type=dep.target_type,
            target_key=dep.target_key,
            rel_type=dep.relationship,
            confidence=dep.confidence,
            evidence_refs=dep.chunk_ids,
            evidence_quote=dep.evidence_quote,
            needs_human_review=dep.confidence < settings.confidence_review_threshold,
        )
        db.add(edge)

    db.commit()


def persist_inferred_relationships(db: Session, assessment_id: str) -> dict[str, int]:
    """Run the configured `RelationshipInferencer` over this assessment's fully
    reconciled entities/edges and persist any proposed relationships as low-confidence
    `DependencyEdge` rows with no `evidence_refs` (there's no chunk backing a guess) --
    flagged for human review exactly like any other low-confidence edge, never silently
    treated as fact."""
    from app.services.providers import get_relationship_inferencer

    settings = get_settings()
    snapshot = load_inventory(db, assessment_id)
    proposed = get_relationship_inferencer().infer(snapshot)
    for rel in proposed:
        db.add(
            DependencyEdge(
                assessment_id=assessment_id,
                source_type=rel.source_type,
                source_key=rel.source_key,
                target_type=rel.target_type,
                target_key=rel.target_key,
                rel_type=rel.relationship,
                confidence=rel.confidence,
                evidence_refs=[],
                needs_human_review=rel.confidence < settings.confidence_review_threshold,
                rationale=rel.rationale,
            )
        )
    db.commit()
    return {"inferred_edges": len(proposed)}


def _materialize_entities(db: Session, assessment_id: str, claims: list[Claim]) -> None:
    by_entity: dict[tuple[str, str], dict] = defaultdict(dict)
    conf: dict[tuple[str, str], list[float]] = defaultdict(list)
    names: dict[tuple[str, str], str] = {}

    for c in claims:
        key = (c.entity_type, c.entity_key)
        by_entity[key][c.attribute] = c.override_value or c.value
        conf[key].append(c.confidence)
        if c.attribute == "name":
            names[key] = c.override_value or c.value

    for (etype, ekey), attrs in by_entity.items():
        name = names.get((etype, ekey), ekey)
        avg_conf = sum(conf[(etype, ekey)]) / max(len(conf[(etype, ekey)]), 1)
        if etype == "application":
            db.add(
                Application(
                    assessment_id=assessment_id,
                    name=name,
                    normalized_key=ekey,
                    attributes=attrs,
                    confidence=avg_conf,
                )
            )
        elif etype == "server":
            db.add(
                Server(
                    assessment_id=assessment_id,
                    name=name,
                    normalized_key=ekey,
                    attributes=attrs,
                    confidence=avg_conf,
                )
            )
        elif etype == "database":
            db.add(
                DatabaseEntity(
                    assessment_id=assessment_id,
                    name=name,
                    normalized_key=ekey,
                    attributes=attrs,
                    confidence=avg_conf,
                )
            )
        elif etype == "interface":
            db.add(
                Interface(
                    assessment_id=assessment_id,
                    name=name,
                    normalized_key=ekey,
                    attributes=attrs,
                    confidence=avg_conf,
                )
            )


def rematerialize_entities(db: Session, assessment_id: str) -> None:
    db.query(Application).filter(Application.assessment_id == assessment_id).delete()
    db.query(Server).filter(Server.assessment_id == assessment_id).delete()
    db.query(DatabaseEntity).filter(DatabaseEntity.assessment_id == assessment_id).delete()
    db.query(Interface).filter(Interface.assessment_id == assessment_id).delete()
    selected = (
        db.query(Claim)
        .filter(Claim.assessment_id == assessment_id, Claim.is_selected.is_(True))
        .all()
    )
    _materialize_entities(db, assessment_id, selected)
    db.commit()
