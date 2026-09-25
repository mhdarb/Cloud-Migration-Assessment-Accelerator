"""Dedupe extracted claims/dependencies (shared by RAG, LangGraph, merge)."""

from __future__ import annotations

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.entity_resolution import normalize_extraction
from app.services.normalization import comparison_value


def dedupe_extraction(
    result: ExtractionResult,
    *,
    default_assumption: str | None = None,
) -> ExtractionResult:
    # Resolve across *all* queries (and code-manifest facts) before deduping: this is where
    # "billing-service" from one skill call and "BillingService" from another become one.
    result, _ = normalize_extraction(result)
    best_claims: dict[tuple, ExtractedClaim] = {}
    for claim in result.claims:
        # Unit-aware value identity, so "8", "8.0" and "8 vCPU" are one fact, not three.
        value = comparison_value(claim.attribute, claim.value)
        key = (claim.entity_type, claim.entity_key, claim.attribute, value)
        prev = best_claims.get(key)
        if prev is None or claim.confidence > prev.confidence:
            best_claims[key] = claim
    seen: set[tuple] = set()
    deps: list[ExtractedDependency] = []
    for dep in result.dependencies:
        dep_key = (
            dep.source_type,
            dep.source_key,
            dep.target_type,
            dep.target_key,
            dep.relationship,
        )
        if dep_key in seen:
            continue
        seen.add(dep_key)
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
