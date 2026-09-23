from __future__ import annotations

from app.schemas.api import GraphEdge, GraphNode, GraphOut
from app.services.graph_analytics import compute_centrality, top_risk_nodes


def _node(node_id: str, ntype: str = "application") -> GraphNode:
    return GraphNode(id=node_id, type=ntype, label=node_id, confidence=0.8)


def _edge(source: str, target: str, rel: str = "depends_on") -> GraphEdge:
    return GraphEdge(id=f"{source}->{target}", source=source, target=target, relationship=rel, confidence=0.8)


def test_compute_centrality_empty_graph_returns_empty_dict():
    assert compute_centrality(GraphOut(nodes=[], edges=[])) == {}


def test_compute_centrality_no_edges_returns_zero_for_every_node():
    graph = GraphOut(nodes=[_node("a"), _node("b")], edges=[])
    scores = compute_centrality(graph)
    assert scores == {"a": 0.0, "b": 0.0}


def test_compute_centrality_ranks_hub_node_higher_than_a_leaf():
    # "hub" is depended on by three other nodes; "leaf" only appears once.
    graph = GraphOut(
        nodes=[_node("hub"), _node("a"), _node("b"), _node("c"), _node("leaf")],
        edges=[
            _edge("a", "hub"),
            _edge("b", "hub"),
            _edge("c", "hub"),
            _edge("a", "leaf"),
        ],
    )
    scores = compute_centrality(graph)
    assert scores["hub"] > scores["leaf"]


def test_top_risk_nodes_orders_by_centrality_and_respects_limit():
    graph = GraphOut(
        nodes=[_node("hub"), _node("a"), _node("b"), _node("c")],
        edges=[_edge("a", "hub"), _edge("b", "hub"), _edge("c", "hub")],
    )
    ranked = top_risk_nodes(graph, limit=1)
    assert len(ranked) == 1
    assert ranked[0]["id"] == "hub"
    assert ranked[0]["centrality"] > 0


def test_top_risk_nodes_excludes_zero_score_nodes():
    graph = GraphOut(nodes=[_node("a"), _node("b")], edges=[])
    assert top_risk_nodes(graph) == []
