"""Dedupe extracted claims/dependencies (shared by RAG, LangGraph, merge)."""

from __future__ import annotations

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult


def dedupe_extraction(
    result: ExtractionResult,
    *,
    default_assumption: str | None = None,
) -> ExtractionResult:
    best_claims: dict[tuple, ExtractedClaim] = {}
    for claim in result.claims:
        key = (claim.entity_type, claim.entity_key, claim.attribute, claim.value.lower())
        prev = best_claims.get(key)
        if prev is None or claim.confidence > prev.confidence:
            best_claims[key] = claim
    seen: set[tuple] = set()
    deps: list[ExtractedDependency] = []
    for dep in result.dependencies:
        key = (
            dep.source_type,
            dep.source_key,
            dep.target_type,
            dep.target_key,
            dep.relationship,
        )
        if key in seen:
            continue
        seen.add(key)
        deps.append(dep)
    assumptions = list(dict.fromkeys(result.assumptions))
    if not assumptions and default_assumption:
        assumptions = [default_assumption]
    return ExtractionResult(
        claims=list(best_claims.values()),
        dependencies=deps,
        gaps=list(dict.fromkeys(result.gaps)),
        assumptions=assumptions,
    )


def merge_extractions(parts: list[ExtractionResult]) -> ExtractionResult:
    claims: list[ExtractedClaim] = []
    deps: list[ExtractedDependency] = []
    gaps: list[str] = []
    assumptions: list[str] = []
    for part in parts:
        claims.extend(part.claims)
        deps.extend(part.dependencies)
        gaps.extend(part.gaps)
        assumptions.extend(part.assumptions)
    return dedupe_extraction(
        ExtractionResult(
            claims=claims,
            dependencies=deps,
            gaps=gaps,
            assumptions=assumptions,
        )
    )
