from __future__ import annotations

from app.models.entities import Application, DependencyEdge, Server
from app.services.graph import build_graph, get_blast_radius


def _seed_estate(db_session, assessment):
    apps = [
        Application(assessment_id=assessment.id, normalized_key=k, name=k, confidence=0.8)
        for k in ("customer-portal", "billing-service", "identity-gateway")
    ]
    servers = [
        Server(assessment_id=assessment.id, normalized_key=k, name=k, confidence=0.8)
        for k in ("app-portal-01", "app-bill-01", "app-id-01")
    ]
    db_session.add_all(apps + servers)
    db_session.flush()
    edges = [
        DependencyEdge(
            assessment_id=assessment.id,
            source_type="application",
            source_key="customer-portal",
            target_type="server",
            target_key="app-portal-01",
            rel_type="hosted_on",
            confidence=0.8,
        ),
        DependencyEdge(
            assessment_id=assessment.id,
            source_type="application",
            source_key="billing-service",
            target_type="server",
            target_key="app-bill-01",
            rel_type="hosted_on",
            confidence=0.8,
        ),
        DependencyEdge(
            assessment_id=assessment.id,
            source_type="application",
            source_key="identity-gateway",
            target_type="server",
            target_key="app-id-01",
            rel_type="hosted_on",
            confidence=0.8,
        ),
        DependencyEdge(
            assessment_id=assessment.id,
            source_type="application",
            source_key="customer-portal",
            target_type="application",
            target_key="billing-service",
            rel_type="depends_on",
            confidence=0.9,
            evidence_quote="Customer Portal depends on Billing Service.",
        ),
        DependencyEdge(
            assessment_id=assessment.id,
            source_type="application",
            source_key="billing-service",
            target_type="application",
            target_key="identity-gateway",
            rel_type="calls",
            confidence=0.9,
        ),
    ]
    db_session.add_all(edges)
    db_session.commit()


def test_build_graph_returns_nodes_and_edges_from_postgres(db_session, assessment):
    _seed_estate(db_session, assessment)
    graph = build_graph(db_session, assessment.id)
    node_ids = {n.id for n in graph.nodes}
    assert {
        "application:customer-portal",
        "application:billing-service",
        "application:identity-gateway",
        "server:app-portal-01",
        "server:app-bill-01",
        "server:app-id-01",
    } <= node_ids
    assert len(graph.edges) == 5


def test_build_graph_attaches_nonzero_centrality_to_connected_nodes(db_session, assessment):
    _seed_estate(db_session, assessment)
    graph = build_graph(db_session, assessment.id)
    by_id = {n.id: n.centrality for n in graph.nodes}
    assert by_id["application:billing-service"] > 0


def test_build_graph_empty_estate_returns_empty_graph(db_session, assessment):
    graph = build_graph(db_session, assessment.id)
    assert graph.nodes == []
    assert graph.edges == []


def test_get_blast_radius_depth_1_includes_only_direct_neighbors(db_session, assessment):
    _seed_estate(db_session, assessment)
    result = get_blast_radius(db_session, assessment.id, "application:billing-service", depth=1)
    node_ids = {n.id for n in result.nodes}
    assert node_ids == {
        "application:billing-service",
        "application:customer-portal",
        "application:identity-gateway",
        "server:app-bill-01",
    }
    assert result.source == "postgres"


def test_get_blast_radius_depth_2_reaches_second_hop(db_session, assessment):
    _seed_estate(db_session, assessment)
    result = get_blast_radius(db_session, assessment.id, "application:customer-portal", depth=2)
    node_ids = {n.id for n in result.nodes}
    # customer-portal -> billing-service (1 hop) -> identity-gateway/app-bill-01 (2 hops)
    assert "application:identity-gateway" in node_ids
    assert "server:app-bill-01" in node_ids


def test_get_blast_radius_unknown_node_returns_no_nodes(db_session, assessment):
    _seed_estate(db_session, assessment)
    result = get_blast_radius(db_session, assessment.id, "application:nonexistent", depth=2)
    assert [n.id for n in result.nodes] == []


def test_build_graph_surfaces_dependency_evidence_quote(db_session, assessment):
    """DependencyEdge.evidence_quote (previously nonexistent) must reach the API response
    -- confidence alone doesn't tell a reviewer *why* a dependency was extracted."""
    _seed_estate(db_session, assessment)
    graph = build_graph(db_session, assessment.id)
    edge = next(
        e
        for e in graph.edges
        if e.source == "application:customer-portal" and e.target == "application:billing-service"
    )
    assert edge.evidence_quote == "Customer Portal depends on Billing Service."
    # Inferred edges (no direct evidence, only a rationale) must not be confused with this.
    hosted_edge = next(e for e in graph.edges if e.relationship == "hosted_on")
    assert hosted_edge.evidence_quote is None
