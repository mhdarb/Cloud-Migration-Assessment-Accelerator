"""Per-skill structured-output schemas (Adapter pattern).

Each `ExtractionTarget` subclass is a narrow, constrained shape a chat model is asked
to fill via structured outputs for one extraction skill (sizing, NFR, dependencies).
`to_extraction_result()` adapts that narrow shape back into the shared
`ExtractedClaim`/`ExtractedDependency` lists — the ONLY shape the deterministic gate
(citations.py -> guardrails.py -> reconciliation.py) ever sees. This is what lets
per-skill schemas exist without touching any of that unchanged, audited logic.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult


class ExtractionTarget(BaseModel):
    """Base for per-skill structured-output targets."""

    def to_extraction_result(self) -> ExtractionResult:
        raise NotImplementedError


class ServerClaim(BaseModel):
    server_key: str
    vcpu: int | None = None
    memory_gb: float | None = None
    os: str | None = None
    disk_gb: float | None = None
    disk_iops: int | None = None
    evidence_quote: str
    chunk_ids: list[str] = []
    confidence: float = 0.7


class ServerSizingTarget(ExtractionTarget):
    servers: list[ServerClaim] = []
    gaps: list[str] = []
    assumptions: list[str] = []

    def to_extraction_result(self) -> ExtractionResult:
        claims: list[ExtractedClaim] = []
        for server in self.servers:
            # Canonical attribute names (see normalization.MEASURED_FIELDS) so sizing,
            # conflict detection, and the eval harness all see one name per fact.
            for attribute, value in (
                ("vcpus", server.vcpu),
                ("memory_gb", server.memory_gb),
                ("os", server.os),
                ("disk_gb", server.disk_gb),
                ("disk_iops", server.disk_iops),
            ):
                if value is None:
                    continue
                claims.append(
                    ExtractedClaim(
                        entity_type="server",
                        entity_key=server.server_key,
                        attribute=attribute,
                        value=str(value),
                        confidence=server.confidence,
                        evidence_quote=server.evidence_quote,
                        chunk_ids=list(server.chunk_ids),
                    )
                )
        return ExtractionResult(claims=claims, gaps=list(self.gaps), assumptions=list(self.assumptions))


NFR_ATTRIBUTES = (
    "sla",
    "rto",
    "rpo",
    "availability",
    "latency",
    "compliance",
    "data_residency",
    "encryption",
    "scalability",
    "nfr",
)


class NfrClaim(BaseModel):
    attribute: str
    value: str
    evidence_quote: str
    chunk_ids: list[str] = []
    confidence: float = 0.7


class NfrTarget(ExtractionTarget):
    requirements: list[NfrClaim] = []
    gaps: list[str] = []
    assumptions: list[str] = []

    def to_extraction_result(self) -> ExtractionResult:
        claims = [
            ExtractedClaim(
                entity_type="business",
                entity_key="migration-requirements",
                attribute=req.attribute if req.attribute in NFR_ATTRIBUTES else "nfr",
                value=req.value,
                confidence=req.confidence,
                evidence_quote=req.evidence_quote,
                chunk_ids=list(req.chunk_ids),
            )
            for req in self.requirements
        ]
        return ExtractionResult(claims=claims, gaps=list(self.gaps), assumptions=list(self.assumptions))


class DependencyClaim(BaseModel):
    source_key: str
    source_type: str
    target_key: str
    target_type: str
    relationship: str = "depends_on"
    evidence_quote: str
    chunk_ids: list[str] = []
    confidence: float = 0.7


class DependencyTarget(ExtractionTarget):
    edges: list[DependencyClaim] = []
    gaps: list[str] = []
    assumptions: list[str] = []

    def to_extraction_result(self) -> ExtractionResult:
        deps = [
            ExtractedDependency(
                source_type=edge.source_type,
                source_key=edge.source_key,
                target_type=edge.target_type,
                target_key=edge.target_key,
                relationship=edge.relationship,
                confidence=edge.confidence,
                evidence_quote=edge.evidence_quote,
                chunk_ids=list(edge.chunk_ids),
            )
            for edge in self.edges
        ]
        return ExtractionResult(dependencies=deps, gaps=list(self.gaps), assumptions=list(self.assumptions))
