"""Query expansion: bridge abstract assessment concepts to the concrete words documents use.

Hybrid retrieval (dense vectors + BM25, fused by RRF) handles *synonyms* reasonably, but
not an *abstraction gap*. A query for "technical debt / blockers" shares no words with a
document that says "PCI re-certification", "manual failover procedure" or "lowest
coupling" — those are *instances* of the concept, not synonyms for it. BM25 therefore
scores those chunks zero, and a small embedding model ranks them near the bottom.

`expand_query` returns the original query plus concrete sub-queries for any assessment
concept it mentions. `MergingRetriever` runs each sub-query through dense search, runs
BM25 over the union of their terms, and fuses every ranked list with RRF (expansions
weighted below the original so they add recall without drowning its precision).

Modes (`RETRIEVAL_QUERY_EXPANSION`):
  off      — original query only.
  lexicon  — deterministic concept lexicon below (default; free, reproducible).
  llm      — lexicon plus model-written concrete rephrasings for concepts the lexicon
             doesn't know (one cached chat call per distinct query; falls back to the
             lexicon if no chat model is configured).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from pydantic import BaseModel

logger = logging.getLogger(__name__)

MAX_EXPANSIONS = 4


@dataclass(frozen=True)
class Concept:
    name: str
    triggers: tuple[str, ...]
    expansions: tuple[str, ...]


# Each expansion is phrased the way assessment documents actually talk about the concept.
CONCEPTS: tuple[Concept, ...] = (
    Concept(
        "technical_debt",
        ("technical debt", "tech debt", "legacy", "modernization", "modernisation"),
        (
            "legacy end-of-life unsupported version upgrade required deprecated",
            "manual process manual failover workaround hand-run procedure",
            "tight coupling tightly coupled monolith shared database coupling",
            "hardcoded configuration single point of failure brittle",
        ),
    ),
    Concept(
        "blockers",
        ("blocker", "blockers", "impediment", "impediments", "constraint", "constraints",
         "migration risk", "migration risks", "cannot migrate", "showstopper"),
        (
            "cannot move before must remain freeze window batch window",
            "re-certification certification audit required before migration",
            "migration sequencing moves first moves last prerequisite order",
            "coupling dependency must migrate together licensing contract end date",
        ),
    ),
    Concept(
        "resilience",
        ("high availability", "resilience", "resiliency", "disaster recovery",
         "business continuity", "failover"),
        (
            "failover standby replica manual failover procedure",
            "rto rpo backup restore recovery time",
            "redundancy zone-redundant single point of failure",
        ),
    ),
    Concept(
        "compliance",
        ("compliance", "regulatory", "security requirements", "audit"),
        (
            "PCI PCI-DSS HIPAA SOX GDPR controls",
            "encryption at rest in transit TLS AES",
            "data residency in-region re-certification",
        ),
    ),
    Concept(
        "capacity",
        ("performance", "capacity", "scalability", "peak load"),
        (
            "latency throughput requests per second concurrent users",
            "cpu memory utilization headroom peak month-end",
        ),
    ),
    Concept(
        "dependencies",
        ("dependencies", "dependency", "integration", "integrations", "interfaces"),
        (
            "depends on calls integrates with upstream downstream",
            "api queue kafka event hub message topic",
        ),
    ),
)


def _words(text: str) -> str:
    """Lowercase with punctuation folded to single spaces, so "tech-debt", "tech_debt" and
    "Tech Debt" all read as "tech debt"."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())) + " "


def _mentions(query_words: str, trigger: str) -> bool:
    return f" {_words(trigger).strip()} " in query_words


def lexicon_expansions(query: str) -> list[str]:
    """Concrete sub-queries for every lexicon concept the query mentions (bounded).

    Interleaved round-robin across the matched concepts, so a query naming several
    ("technical debt / blockers") gets expansions for each of them rather than the first
    concept consuming the whole budget."""
    q = _words(query)
    matched = [c for c in CONCEPTS if any(_mentions(q, t) for t in c.triggers)]
    out: list[str] = []
    for depth in range(max((len(c.expansions) for c in matched), default=0)):
        for concept in matched:
            if depth < len(concept.expansions) and concept.expansions[depth] not in out:
                out.append(concept.expansions[depth])
    return out[:MAX_EXPANSIONS]


class _Rephrasings(BaseModel):
    queries: list[str] = []


@lru_cache(maxsize=512)
def _llm_expansions(query: str) -> tuple[str, ...]:
    from app.services.compat import call_if_supported
    from app.services.llm_clients import get_chat_completer

    completer = get_chat_completer()
    if not completer.enabled:
        return ()
    system = (
        "You help a retrieval system for cloud-migration assessment documents. Rewrite the "
        "user's search query as up to 3 short queries using the concrete, specific wording "
        "such documents actually use for that topic (named processes, controls, symptoms), "
        "not the abstract category name. Return JSON: {\"queries\": [\"...\"]}."
    )
    content = call_if_supported(
        completer.complete, system, query, temperature=0.2, json_mode=True, response_schema=_Rephrasings
    )
    if not content:
        return ()
    try:
        parsed = _Rephrasings.model_validate(json.loads(content))
    except Exception:
        logger.warning("Query expansion: model returned unparseable JSON")
        return ()
    return tuple(q.strip() for q in parsed.queries if q and q.strip())[:3]


def expand_query(query: str, mode: str = "lexicon") -> list[str]:
    """[original, *expansions]; the original always comes first."""
    if mode == "off" or not (query or "").strip():
        return [query]
    expansions = lexicon_expansions(query)
    if mode == "llm":
        for phrase in _llm_expansions(query):
            if phrase not in expansions:
                expansions.append(phrase)
    return [query, *expansions[: MAX_EXPANSIONS + (3 if mode == "llm" else 0)]]
