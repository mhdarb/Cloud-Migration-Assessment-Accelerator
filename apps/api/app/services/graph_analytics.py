"""Graph analytics computed live over an already-built `GraphOut` -- no separate graph
database or persisted state; the estate's dependency graph already lives in Postgres
(`graph.py`), and this is a pure function of it, cheap enough to run on every read at the
sizes a single assessment's estate actually reaches."""

from __future__ import annotations

import networkx as nx

from app.schemas.api import GraphOut


def compute_centrality(graph: GraphOut) -> dict[str, float]:
    """PageRank over the dependency graph, treated as undirected -- a component many
    others connect to (in either direction) is higher-risk to touch during migration
    regardless of which way an extracted edge happened to point."""
    if not graph.nodes:
        return {}
    g = nx.Graph()
    g.add_nodes_from(n.id for n in graph.nodes)
    g.add_edges_from((e.source, e.target) for e in graph.edges if e.source != e.target)
    if g.number_of_edges() == 0:
        # PageRank on an edgeless graph is a uniform, uninformative score for every node --
        # 0.0 signals "nothing to rank" more honestly than a flat non-zero value.
        return dict.fromkeys(g.nodes, 0.0)
    try:
        return nx.pagerank(g)
    except nx.PowerIterationFailedConvergence:
        return dict.fromkeys(g.nodes, 0.0)


def top_risk_nodes(graph: GraphOut, limit: int = 5) -> list[dict]:
    """The `limit` highest-centrality nodes -- the components most worth planning
    migration sequencing around first, since they touch the most of the estate."""
    scores = compute_centrality(graph)
    by_id = {n.id: n for n in graph.nodes}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    out: list[dict] = []
    for node_id, score in ranked[:limit]:
        if score <= 0:
            continue
        node = by_id.get(node_id)
        if not node:
            continue
        out.append(
            {"id": node.id, "label": node.label, "type": node.type, "centrality": round(score, 4)}
        )
    return out
