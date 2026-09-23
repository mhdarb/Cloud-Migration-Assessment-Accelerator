"""RelationshipInferencer implementations (Strategy pattern): `NoOpRelationshipInferencer`
is the Null Object; `CoLocationInferencer` is the deterministic default (two applications
hosted on the same server, with no existing edge between them, plausibly share a blast
radius during migration even without a directly-extracted dependency); `LlmRelationshipInferencer`
asks the configured chat completer for additional plausible relationships and falls back
to `CoLocationInferencer` when disabled, unavailable, or its response is unusable.

Every proposed relationship is a guess, not a finding — callers persist it as a
low-confidence `DependencyEdge` with no evidence_refs, flagged for human review, so it
goes through the exact same review workflow as everything else rather than being quietly
treated as fact.
"""

from __future__ import annotations

import itertools
import json
import logging

from app.schemas.relationships import InferredRelationship
from app.services.compat import call_if_supported
from app.services.inventory import InventorySnapshot
from app.services.ports import ChatCompleter, RelationshipInferencer

logger = logging.getLogger(__name__)

_MAX_LLM_PROPOSALS = 5


class NoOpRelationshipInferencer:
    def infer(self, snapshot: InventorySnapshot) -> list[InferredRelationship]:
        return []


class CoLocationInferencer:
    """Two applications hosted on the same server, with no existing edge between them,
    plausibly affect each other during a migration cutover (shared blast radius) even
    though nothing directly extracted a dependency between them."""

    def infer(self, snapshot: InventorySnapshot) -> list[InferredRelationship]:
        existing_pairs = {
            frozenset((e.source_key, e.target_key))
            for e in snapshot.edges
            if e.source_type == "application" and e.target_type == "application"
        }
        by_server: dict[str, set[str]] = {}
        for e in snapshot.edges:
            if (
                e.rel_type == "hosted_on"
                and e.source_type == "application"
                and e.target_type == "server"
            ):
                by_server.setdefault(e.target_key, set()).add(e.source_key)

        proposed: list[InferredRelationship] = []
        seen: set[frozenset[str]] = set()
        for server_key, app_keys in by_server.items():
            for a, b in itertools.combinations(sorted(app_keys), 2):
                pair = frozenset((a, b))
                if pair in seen or pair in existing_pairs:
                    continue
                seen.add(pair)
                proposed.append(
                    InferredRelationship(
                        source_type="application",
                        source_key=a,
                        target_type="application",
                        target_key=b,
                        relationship="possible_dependency",
                        confidence=0.35,
                        rationale=(
                            f"Both hosted on server '{server_key}'; no direct dependency "
                            "was extracted, but shared infrastructure often means a "
                            "shared blast radius during migration."
                        ),
                    )
                )
        return proposed


class LlmRelationshipInferencer:
    def __init__(
        self, completer: ChatCompleter, fallback: RelationshipInferencer | None = None
    ) -> None:
        self._completer = completer
        self._fallback = fallback or CoLocationInferencer()

    def infer(self, snapshot: InventorySnapshot) -> list[InferredRelationship]:
        if not self._completer.enabled or len(snapshot.applications) < 2:
            return self._fallback.infer(snapshot)
        apps = [
            {"key": a.normalized_key, "name": a.name, "attributes": a.attributes or {}}
            for a in snapshot.applications
        ]
        existing = [
            {
                "source": f"{e.source_type}:{e.source_key}",
                "target": f"{e.target_type}:{e.target_key}",
                "relationship": e.rel_type,
            }
            for e in snapshot.edges
        ]
        system = (
            "Given a cloud migration estate's applications and their already-known "
            "relationships, propose additional PLAUSIBLE relationships between "
            "applications that a document extractor may have missed (implied by naming, "
            "shared domain, or typical architecture patterns). Only propose relationships "
            "between applications in the given list, never a new one. Return a JSON object "
            "with a 'relationships' list, each item having source_key, target_key, "
            "relationship, confidence (0-1, always below 0.6 — this is a guess, not a "
            f"finding), and rationale. Propose at most {_MAX_LLM_PROPOSALS}. Return an "
            "empty list if nothing plausible comes to mind."
        )
        user = json.dumps({"applications": apps, "existing_relationships": existing})
        content = call_if_supported(
            self._completer.complete, system, user, temperature=0.2, json_mode=True
        )
        if not content:
            return self._fallback.infer(snapshot)
        try:
            raw = json.loads(content)
            app_keys = {a["key"] for a in apps}
            proposed: list[InferredRelationship] = []
            for item in raw.get("relationships", [])[:_MAX_LLM_PROPOSALS]:
                source_key = item.get("source_key")
                target_key = item.get("target_key")
                if source_key not in app_keys or target_key not in app_keys or source_key == target_key:
                    continue
                proposed.append(
                    InferredRelationship(
                        source_type="application",
                        source_key=source_key,
                        target_type="application",
                        target_key=target_key,
                        relationship=item.get("relationship") or "possible_dependency",
                        confidence=min(0.55, max(0.1, float(item.get("confidence", 0.4)))),
                        rationale=item.get("rationale") or "LLM-inferred from estate context",
                    )
                )
            return proposed
        except Exception:
            logger.exception("LLM relationship inference returned an unusable response; falling back")
            return self._fallback.infer(snapshot)
