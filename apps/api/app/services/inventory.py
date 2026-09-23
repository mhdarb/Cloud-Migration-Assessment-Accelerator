"""Shared inventory queries for report, graph, and entity list."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.entities import (
    Application,
    Claim,
    Conflict,
    DatabaseEntity,
    DependencyEdge,
    Document,
    InfrastructureRecommendation,
    Interface,
    Server,
)


@dataclass
class InventorySnapshot:
    applications: list[Application] = field(default_factory=list)
    servers: list[Server] = field(default_factory=list)
    databases: list[DatabaseEntity] = field(default_factory=list)
    interfaces: list[Interface] = field(default_factory=list)
    edges: list[DependencyEdge] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    recommendations: list[InfrastructureRecommendation] = field(default_factory=list)


def load_inventory(
    db: Session,
    assessment_id: str,
    *,
    documents: bool = False,
    claims: bool = False,
    conflicts: bool = False,
    recommendations: bool = False,
) -> InventorySnapshot:
    snap = InventorySnapshot(
        applications=db.query(Application).filter(Application.assessment_id == assessment_id).all(),
        servers=db.query(Server).filter(Server.assessment_id == assessment_id).all(),
        databases=db.query(DatabaseEntity)
        .filter(DatabaseEntity.assessment_id == assessment_id)
        .all(),
        interfaces=db.query(Interface).filter(Interface.assessment_id == assessment_id).all(),
        edges=db.query(DependencyEdge)
        .filter(DependencyEdge.assessment_id == assessment_id)
        .all(),
    )
    if documents:
        snap.documents = db.query(Document).filter(Document.assessment_id == assessment_id).all()
    if claims:
        snap.claims = db.query(Claim).filter(Claim.assessment_id == assessment_id).all()
    if conflicts:
        snap.conflicts = db.query(Conflict).filter(Conflict.assessment_id == assessment_id).all()
    if recommendations:
        snap.recommendations = (
            db.query(InfrastructureRecommendation)
            .filter(InfrastructureRecommendation.assessment_id == assessment_id)
            .all()
        )
    return snap
