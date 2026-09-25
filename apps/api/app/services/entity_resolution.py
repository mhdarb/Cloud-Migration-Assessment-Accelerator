"""Entity resolution for extraction output — the de-noising step for LLM extraction.

A chat model fills each of ~12 skill queries independently, so the same real entity comes
back spelled several ways ("Billing Service", "billing_service", "BillingService",
"app-bill-01.contoso.local" vs "APP-BILL-01"), with drifting types ("vm", "db", "service")
and attribute names ("vCPU", "RAM (GB)", "Operating System"), plus placeholder entities
("the application", "N/A"). Without resolution each spelling becomes its own entity, facts
split across attribute names, dependency edges dangle, and synonym-typed facts are dropped.

`normalize_extraction` fixes that deterministically and idempotently:

1. types      -> canonical five (synonyms mapped, never silently dropped)
2. keys       -> canonical form (servers: case + FQDN folded; others: slug, article dropped)
3. attributes -> canonical names (the same vocabulary sizing and reconciliation use)
4. placeholders ("unknown", "the-application", ...) removed
5. spelling variants of one non-server entity merged: keys that differ only in separators
   ("billing-service" / "billingservice") collapse to the key derived from the entity's own
   `name` claim when there is one — the same rule the deterministic extractor uses — else
   the most frequently used variant. Servers are NOT merged this way: hostnames differing
   only in separators ("web-1-2" vs "web-12") can genuinely be different machines.

It runs inside `guardrails.sanitize_extraction_output` (per query) and
`extraction_merge.dedupe_extraction` (on the merged result of all queries + code manifests),
so both the LLM and heuristic paths go through it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.services.normalization import (
    canonical_attribute,
    canonical_entity_key,
    canonical_entity_type,
    is_generic_entity_key,
    slug,
)

_MERGEABLE_TYPES = frozenset({"application", "database", "interface"})


@dataclass
class ResolutionStats:
    types_mapped: int = 0
    keys_rewritten: int = 0
    attributes_mapped: int = 0
    generic_dropped: int = 0
    variants_merged: int = 0
    merged: dict[str, list[str]] = field(default_factory=dict)  # canonical -> variants

    def as_dict(self) -> dict[str, int]:
        return {
            "types_mapped": self.types_mapped,
            "keys_rewritten": self.keys_rewritten,
            "attributes_mapped": self.attributes_mapped,
            "generic_dropped": self.generic_dropped,
            "variants_merged": self.variants_merged,
        }


def _compact(key: str) -> str:
    return key.replace("-", "")


def _type(raw: str, stats: ResolutionStats) -> str:
    canonical = canonical_entity_type(raw)
    if canonical is None:
        # Unrecognized: leave as-is so the guardrail allowlist decides (it drops it).
        return (raw or "").strip().lower()
    if canonical != (raw or "").strip().lower():
        stats.types_mapped += 1
    return canonical


def _key(entity_type: str, raw: str, stats: ResolutionStats) -> str:
    key = canonical_entity_key(entity_type, raw)
    if key != raw:
        stats.keys_rewritten += 1
    return key


def _variant_map(
    claims: list[ExtractedClaim], deps: list[ExtractedDependency], stats: ResolutionStats
) -> dict[tuple[str, str], str]:
    """(type, key) -> canonical key, for keys that are separator-variants of one another."""
    usage: Counter[tuple[str, str]] = Counter()
    name_keys: dict[tuple[str, str], set[str]] = defaultdict(set)
    for c in claims:
        usage[(c.entity_type, c.entity_key)] += 1
        if c.attribute == "name" and c.value:
            name_keys[(c.entity_type, c.entity_key)].add(slug(c.value))
    for d in deps:
        usage[(d.source_type, d.source_key)] += 1
        usage[(d.target_type, d.target_key)] += 1

    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for etype, key in usage:
        if etype in _MERGEABLE_TYPES:
            groups[(etype, _compact(key))].append(key)

    mapping: dict[tuple[str, str], str] = {}
    for (etype, compact), variants in groups.items():
        if len(variants) < 2:
            continue
        # Prefer the key derived from the entity's own display name, if one agrees.
        from_names = {
            nk
            for v in variants
            for nk in name_keys.get((etype, v), ())
            if _compact(nk) == compact
        }
        if len(from_names) == 1:
            canonical = next(iter(from_names))
        else:
            canonical = max(
                variants, key=lambda v: (usage[(etype, v)], v.count("-"), v)
            )
        stats.merged[f"{etype}:{canonical}"] = sorted(v for v in variants if v != canonical)
        for v in variants:
            if v != canonical:
                mapping[(etype, v)] = canonical
                stats.variants_merged += 1
    return mapping


def normalize_extraction(result: ExtractionResult) -> tuple[ExtractionResult, ResolutionStats]:
    stats = ResolutionStats()

    claims: list[ExtractedClaim] = []
    for c in result.claims:
        etype = _type(c.entity_type, stats)
        key = _key(etype, c.entity_key, stats)
        if is_generic_entity_key(etype, key):
            stats.generic_dropped += 1
            continue
        attribute = canonical_attribute(c.attribute)
        if attribute != c.attribute:
            stats.attributes_mapped += 1
        claims.append(c.model_copy(update={"entity_type": etype, "entity_key": key, "attribute": attribute}))

    deps: list[ExtractedDependency] = []
    for d in result.dependencies:
        st = _type(d.source_type, stats)
        tt = _type(d.target_type, stats)
        sk = _key(st, d.source_key, stats)
        tk = _key(tt, d.target_key, stats)
        if is_generic_entity_key(st, sk) or is_generic_entity_key(tt, tk):
            stats.generic_dropped += 1
            continue
        deps.append(
            d.model_copy(update={"source_type": st, "source_key": sk, "target_type": tt, "target_key": tk})
        )

    mapping = _variant_map(claims, deps, stats)
    if mapping:
        claims = [
            c.model_copy(update={"entity_key": mapping.get((c.entity_type, c.entity_key), c.entity_key)})
            for c in claims
        ]
        deps = [
            d.model_copy(
                update={
                    "source_key": mapping.get((d.source_type, d.source_key), d.source_key),
                    "target_key": mapping.get((d.target_type, d.target_key), d.target_key),
                }
            )
            for d in deps
        ]

    return (
        ExtractionResult(
            claims=claims,
            dependencies=deps,
            gaps=list(result.gaps),
            assumptions=list(result.assumptions),
        ),
        stats,
    )
