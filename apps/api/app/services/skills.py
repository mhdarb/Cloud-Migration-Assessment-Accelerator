"""Declarative retrieval "skills" (one per RAG query/extraction intent) and the
RagPlanner implementations that select which ones run for a given corpus.

`HeuristicPlanner` is a real, deterministic filter (Strategy pattern — interchangeable
with `NoOpRagPlanner` behind the `RagPlanner` port): a skill's `applies_to` gate is a
declarative doc-type check computed cheaply from the DB, no LLM. Gates are deliberately
inclusive — a skill is only excluded when its signal genuinely can't come from the
present doc types — and are validated against `tests/test_golden_extraction.py` so a
too-aggressive gate shows up as a regression there, not silently in production.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.models.entities import DocumentType
from app.schemas.api import ExtractionResult
from app.schemas.extraction_targets import DependencyTarget, NfrTarget, ServerSizingTarget
from app.schemas.planning import CorpusProfile, PlannedQuery, QueryPlan

RAG_QUERIES = [
    "applications and services in the estate names tiers owners",
    "servers hostnames operating systems infrastructure inventory",
    "server vCPU memory utilization storage disk IOPS throughput region architecture environment",
    "databases data stores SQL Oracle PostgreSQL engines",
    "dependencies integrations hosted_on uses calls depends on interfaces",
    "business criticality compliance PCI constraints gaps assumptions",
    "non-functional requirements latency availability RTO RPO SLA uptime",
    "compliance security PCI HIPAA data residency encryption PII",
    "integration contracts APIs message queues external systems interfaces",
    "scalability capacity concurrency users peak load throughput",
    "constraints assumptions migration blockers tech debt must support",
]

_INVENTORY_LIKE = {DocumentType.inventory, DocumentType.code_snapshot}
_NARRATIVE_LIKE = {
    DocumentType.architecture,
    DocumentType.requirements,
    DocumentType.questionnaire,
    DocumentType.runbook,
}


def _has_any(doc_types: set[DocumentType], candidates: set[DocumentType]) -> bool:
    return bool(doc_types & candidates)


@dataclass(frozen=True)
class Skill:
    name: str
    query: str
    description: str
    applies_to: Callable[[set[DocumentType]], bool] | None = None
    target_schema: type[BaseModel] = ExtractionResult
    system_prompt: str | None = None
    # Retrieval policy (used by the LangGraph LLM path's `retrieve` node):
    #   top_k              -- override the global RAG_TOP_K candidate count for this skill.
    #   exhaustive_doc_types -- guarantee *every* chunk from these doc types is seen by
    #     this skill, not just the query's top-k hits. Essential for structured inventory:
    #     a generic keyword query + top-k would silently miss servers past the top chunks,
    #     so the sizing engine must extract over the whole inventory, not a sample.
    top_k: int | None = None
    exhaustive_doc_types: tuple[DocumentType, ...] = ()


SKILLS: list[Skill] = [
    Skill(
        "applications",
        RAG_QUERIES[0],
        "Applications and services in the estate",
        applies_to=lambda dt: _has_any(
            dt, _INVENTORY_LIKE | {DocumentType.architecture, DocumentType.questionnaire}
        ),
    ),
    Skill(
        "servers",
        RAG_QUERIES[1],
        "Server hostnames, OS, infrastructure inventory",
        applies_to=lambda dt: _has_any(dt, _INVENTORY_LIKE | {DocumentType.architecture}),
        exhaustive_doc_types=(DocumentType.inventory,),
    ),
    Skill(
        "sizing",
        RAG_QUERIES[2],
        "Server sizing: vCPU, memory, storage, IOPS",
        applies_to=lambda dt: _has_any(dt, _INVENTORY_LIKE),
        exhaustive_doc_types=(DocumentType.inventory,),
        target_schema=ServerSizingTarget,
        system_prompt=(
            "Emit ONLY numeric/OS sizing facts per server into the `servers` list "
            "(server_key, vcpu, memory_gb, os, disk_gb, disk_iops) — no application or "
            "dependency facts here."
        ),
    ),
    Skill(
        "databases",
        RAG_QUERIES[3],
        "Databases and data stores",
        applies_to=lambda dt: _has_any(dt, _INVENTORY_LIKE | {DocumentType.architecture}),
    ),
    Skill(
        "dependencies",
        RAG_QUERIES[4],
        "Dependencies and integrations between entities",
        applies_to=lambda dt: _has_any(dt, _INVENTORY_LIKE | {DocumentType.architecture}),
        target_schema=DependencyTarget,
        system_prompt=(
            "Emit ONLY dependency edges into the `edges` list (source/target key+type, "
            "relationship) — no standalone entity facts here."
        ),
    ),
    Skill(
        "business_constraints",
        RAG_QUERIES[5],
        "Business criticality, compliance, gaps",
        applies_to=lambda dt: _has_any(
            dt, {DocumentType.questionnaire, DocumentType.requirements, DocumentType.architecture}
        ),
    ),
    Skill(
        "nfr",
        RAG_QUERIES[6],
        "Non-functional requirements: latency, availability, RTO/RPO/SLA",
        applies_to=lambda dt: _has_any(dt, {DocumentType.requirements, DocumentType.architecture}),
        target_schema=NfrTarget,
        system_prompt=(
            "Emit ONLY non-functional-requirement facts into the `requirements` list "
            "(attribute one of sla/rto/rpo/availability/latency/compliance/data_residency/"
            "encryption/scalability/nfr)."
        ),
    ),
    Skill(
        "compliance",
        RAG_QUERIES[7],
        "Compliance and security: PCI, HIPAA, residency, encryption",
        applies_to=lambda dt: _has_any(dt, {DocumentType.requirements, DocumentType.questionnaire}),
        target_schema=NfrTarget,
        system_prompt=(
            "Emit ONLY compliance/security facts into the `requirements` list "
            "(attribute one of compliance/data_residency/encryption)."
        ),
    ),
    Skill(
        "integrations",
        RAG_QUERIES[8],
        "Integration contracts: APIs, message queues, external systems",
        applies_to=lambda dt: _has_any(dt, {DocumentType.architecture, DocumentType.code_snapshot}),
        target_schema=DependencyTarget,
        system_prompt="Emit ONLY integration/dependency edges into the `edges` list.",
    ),
    Skill(
        "scalability",
        RAG_QUERIES[9],
        "Scalability, capacity, concurrency, peak load",
        applies_to=lambda dt: _has_any(dt, {DocumentType.requirements, DocumentType.architecture}),
        target_schema=NfrTarget,
        system_prompt=(
            "Emit ONLY scalability/capacity facts into the `requirements` list "
            "(attribute=scalability)."
        ),
    ),
    Skill(
        "constraints",
        RAG_QUERIES[10],
        "Migration constraints, assumptions, blockers",
        applies_to=lambda dt: _has_any(dt, {DocumentType.questionnaire, DocumentType.requirements}),
    ),
]

SKILLS_BY_QUERY: dict[str, Skill] = {s.query: s for s in SKILLS}
SKILLS_BY_NAME: dict[str, Skill] = {s.name: s for s in SKILLS}


class HeuristicPlanner:
    """RagPlanner: declarative doc-type-gated filter over SKILLS (default)."""

    def __init__(self, skills: list[Skill] | None = None) -> None:
        self._skills = skills if skills is not None else SKILLS

    def plan(self, profile: CorpusProfile) -> QueryPlan:
        selected = [
            s for s in self._skills if s.applies_to is None or s.applies_to(profile.doc_types)
        ]
        return QueryPlan(queries=[PlannedQuery(skill_id=s.name, query=s.query) for s in selected])


class NoOpRagPlanner:
    """RagPlanner: always run every skill — reproduces pre-planner behavior exactly
    (`RAG_PLANNER=off`), useful as an instant rollback."""

    def __init__(self, skills: list[Skill] | None = None) -> None:
        self._skills = skills if skills is not None else SKILLS

    def plan(self, profile: CorpusProfile) -> QueryPlan:
        return QueryPlan(
            queries=[PlannedQuery(skill_id=s.name, query=s.query) for s in self._skills]
        )
