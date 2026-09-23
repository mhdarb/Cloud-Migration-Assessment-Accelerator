from __future__ import annotations

import json

from app.models.entities import Application, DependencyEdge
from app.services.inventory import InventorySnapshot
from app.services.relationship_inference import (
    CoLocationInferencer,
    LlmRelationshipInferencer,
    NoOpRelationshipInferencer,
)


def _app(key: str) -> Application:
    return Application(normalized_key=key, name=key, confidence=0.8, attributes={})


def _edge(source_type, source_key, target_type, target_key, rel_type) -> DependencyEdge:
    return DependencyEdge(
        source_type=source_type,
        source_key=source_key,
        target_type=target_type,
        target_key=target_key,
        rel_type=rel_type,
        confidence=0.8,
    )


class _StubCompleter:
    def __init__(self, content: str | None, *, enabled: bool = True) -> None:
        self._content = content
        self.enabled = enabled
        self.source = "stub"

    def complete(self, system, user, *, temperature=0.1, json_mode=False, response_schema=None):
        return self._content


def test_noop_relationship_inferencer_returns_empty():
    snapshot = InventorySnapshot(applications=[_app("a"), _app("b")])
    assert NoOpRelationshipInferencer().infer(snapshot) == []


def test_colocation_inferencer_proposes_edge_for_apps_sharing_a_server():
    snapshot = InventorySnapshot(
        applications=[_app("app-a"), _app("app-b")],
        edges=[
            _edge("application", "app-a", "server", "shared-01", "hosted_on"),
            _edge("application", "app-b", "server", "shared-01", "hosted_on"),
        ],
    )
    proposed = CoLocationInferencer().infer(snapshot)
    assert len(proposed) == 1
    edge = proposed[0]
    assert {edge.source_key, edge.target_key} == {"app-a", "app-b"}
    assert edge.relationship == "possible_dependency"
    assert edge.confidence < 0.5
    assert "shared-01" in edge.rationale


def test_colocation_inferencer_skips_pair_with_existing_edge():
    snapshot = InventorySnapshot(
        applications=[_app("app-a"), _app("app-b")],
        edges=[
            _edge("application", "app-a", "server", "shared-01", "hosted_on"),
            _edge("application", "app-b", "server", "shared-01", "hosted_on"),
            _edge("application", "app-a", "application", "app-b", "depends_on"),
        ],
    )
    assert CoLocationInferencer().infer(snapshot) == []


def test_colocation_inferencer_no_proposal_without_shared_server():
    snapshot = InventorySnapshot(
        applications=[_app("app-a"), _app("app-b")],
        edges=[
            _edge("application", "app-a", "server", "server-1", "hosted_on"),
            _edge("application", "app-b", "server", "server-2", "hosted_on"),
        ],
    )
    assert CoLocationInferencer().infer(snapshot) == []


def test_colocation_inferencer_dedupes_three_way_colocation_into_three_pairs():
    snapshot = InventorySnapshot(
        applications=[_app("a"), _app("b"), _app("c")],
        edges=[
            _edge("application", "a", "server", "shared", "hosted_on"),
            _edge("application", "b", "server", "shared", "hosted_on"),
            _edge("application", "c", "server", "shared", "hosted_on"),
        ],
    )
    proposed = CoLocationInferencer().infer(snapshot)
    pairs = {frozenset((p.source_key, p.target_key)) for p in proposed}
    assert pairs == {frozenset(("a", "b")), frozenset(("a", "c")), frozenset(("b", "c"))}


def test_llm_relationship_inferencer_falls_back_when_disabled():
    snapshot = InventorySnapshot(
        applications=[_app("app-a"), _app("app-b")],
        edges=[
            _edge("application", "app-a", "server", "shared-01", "hosted_on"),
            _edge("application", "app-b", "server", "shared-01", "hosted_on"),
        ],
    )
    inferencer = LlmRelationshipInferencer(_StubCompleter(None, enabled=False))
    proposed = inferencer.infer(snapshot)
    assert len(proposed) == 1  # falls back to CoLocationInferencer


def test_llm_relationship_inferencer_skips_when_fewer_than_two_apps():
    snapshot = InventorySnapshot(applications=[_app("solo")])
    inferencer = LlmRelationshipInferencer(_StubCompleter('{"relationships": []}'))
    assert inferencer.infer(snapshot) == []


def test_llm_relationship_inferencer_parses_structured_response():
    snapshot = InventorySnapshot(applications=[_app("app-a"), _app("app-b")])
    content = json.dumps(
        {
            "relationships": [
                {
                    "source_key": "app-a",
                    "target_key": "app-b",
                    "relationship": "possible_dependency",
                    "confidence": 0.4,
                    "rationale": "similar naming",
                }
            ]
        }
    )
    inferencer = LlmRelationshipInferencer(_StubCompleter(content))
    proposed = inferencer.infer(snapshot)
    assert len(proposed) == 1
    assert proposed[0].source_key == "app-a"
    assert proposed[0].target_key == "app-b"
    assert proposed[0].rationale == "similar naming"


def test_llm_relationship_inferencer_caps_confidence_below_0_6():
    snapshot = InventorySnapshot(applications=[_app("app-a"), _app("app-b")])
    content = json.dumps(
        {"relationships": [{"source_key": "app-a", "target_key": "app-b", "confidence": 5}]}
    )
    proposed = LlmRelationshipInferencer(_StubCompleter(content)).infer(snapshot)
    assert proposed[0].confidence < 0.6


def test_llm_relationship_inferencer_rejects_unknown_app_keys():
    snapshot = InventorySnapshot(applications=[_app("app-a"), _app("app-b")])
    content = json.dumps(
        {"relationships": [{"source_key": "app-a", "target_key": "not-a-real-app", "confidence": 0.3}]}
    )
    assert LlmRelationshipInferencer(_StubCompleter(content)).infer(snapshot) == []


def test_llm_relationship_inferencer_falls_back_on_malformed_response():
    snapshot = InventorySnapshot(
        applications=[_app("app-a"), _app("app-b")],
        edges=[
            _edge("application", "app-a", "server", "shared-01", "hosted_on"),
            _edge("application", "app-b", "server", "shared-01", "hosted_on"),
        ],
    )
    inferencer = LlmRelationshipInferencer(_StubCompleter("not json"))
    proposed = inferencer.infer(snapshot)
    assert len(proposed) == 1  # falls back to CoLocationInferencer


def test_llm_relationship_inferencer_uses_custom_fallback():
    snapshot = InventorySnapshot(applications=[_app("app-a"), _app("app-b")])

    class _AlwaysEmptyFallback:
        def infer(self, snapshot):
            return []

    inferencer = LlmRelationshipInferencer(
        _StubCompleter(None, enabled=False), fallback=_AlwaysEmptyFallback()
    )
    assert inferencer.infer(snapshot) == []


def test_get_relationship_inferencer_off_returns_noop(monkeypatch):
    from app.config import get_settings
    from app.services import providers

    monkeypatch.setenv("RELATIONSHIP_INFERENCER", "off")
    get_settings.cache_clear()
    assert isinstance(providers.get_relationship_inferencer(), NoOpRelationshipInferencer)


def test_get_relationship_inferencer_heuristic_returns_colocation(monkeypatch):
    from app.config import get_settings
    from app.services import providers

    monkeypatch.setenv("RELATIONSHIP_INFERENCER", "heuristic")
    get_settings.cache_clear()
    assert isinstance(providers.get_relationship_inferencer(), CoLocationInferencer)


def test_get_relationship_inferencer_llm_falls_back_to_colocation_under_mock_llm(monkeypatch):
    from app.config import get_settings
    from app.services import providers

    monkeypatch.setenv("RELATIONSHIP_INFERENCER", "llm")
    monkeypatch.setenv("MOCK_LLM", "true")
    get_settings.cache_clear()
    assert isinstance(providers.get_relationship_inferencer(), CoLocationInferencer)


def test_persist_inferred_relationships_creates_low_confidence_review_flagged_edge(
    db_session, assessment
):
    from app.services.reconciliation import persist_inferred_relationships

    apps = [
        Application(assessment_id=assessment.id, normalized_key=k, name=k, confidence=0.8)
        for k in ("app-a", "app-b")
    ]
    db_session.add_all(apps)
    db_session.flush()
    db_session.add_all(
        [
            DependencyEdge(
                assessment_id=assessment.id,
                source_type="application",
                source_key="app-a",
                target_type="server",
                target_key="shared-01",
                rel_type="hosted_on",
                confidence=0.8,
            ),
            DependencyEdge(
                assessment_id=assessment.id,
                source_type="application",
                source_key="app-b",
                target_type="server",
                target_key="shared-01",
                rel_type="hosted_on",
                confidence=0.8,
            ),
        ]
    )
    db_session.commit()

    result = persist_inferred_relationships(db_session, assessment.id)
    assert result["inferred_edges"] == 1

    inferred = (
        db_session.query(DependencyEdge)
        .filter(
            DependencyEdge.assessment_id == assessment.id,
            DependencyEdge.rel_type == "possible_dependency",
        )
        .one()
    )
    assert inferred.evidence_refs == []
    assert inferred.needs_human_review is True
    assert inferred.rationale is not None
