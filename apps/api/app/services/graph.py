from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.api import BlastRadiusOut, GraphEdge, GraphNode, GraphOut
from app.services.graph_analytics import compute_centrality
from app.services.inventory import InventorySnapshot, load_inventory


def build_graph(db: Session, assessment_id: str) -> GraphOut:
    """The estate's dependency graph, built live from the reconciled Postgres entities/
    edges -- there's no separate graph database to sync or fall back from."""
    graph = _graph_from(load_inventory(db, assessment_id))
    return _with_centrality(graph)


def get_blast_radius(
    db: Session, assessment_id: str, node: str, depth: int = 2
) -> BlastRadiusOut:
    full = _graph_from(load_inventory(db, assessment_id))
    return _bfs_blast(full, node, depth)


def _with_centrality(graph: GraphOut) -> GraphOut:
    scores = compute_centrality(graph)
    if not scores:
        return graph
    return GraphOut(
        nodes=[n.model_copy(update={"centrality": scores.get(n.id, 0.0)}) for n in graph.nodes],
        edges=graph.edges,
    )


def _graph_from(snap: InventorySnapshot) -> GraphOut:
    apps = snap.applications
    servers = snap.servers
    databases = snap.databases
    interfaces = snap.interfaces
    edges = snap.edges

    nodes: list[GraphNode] = []
    node_ids: set[str] = set()

    def add_node(etype: str, key: str, label: str, confidence: float, attrs: dict) -> None:
        nid = f"{etype}:{key}"
        if nid in node_ids:
            return
        node_ids.add(nid)
        nodes.append(
            GraphNode(
                id=nid,
                type=etype,
                label=label,
                confidence=confidence,
                attributes=attrs or {},
            )
        )

    for a in apps:
        add_node("application", a.normalized_key, a.name, a.confidence, a.attributes or {})
    for s in servers:
        add_node("server", s.normalized_key, s.name, s.confidence, s.attributes or {})
    for d in databases:
        add_node("database", d.normalized_key, d.name, d.confidence, d.attributes or {})
    for i in interfaces:
        add_node("interface", i.normalized_key, i.name, i.confidence, i.attributes or {})

    graph_edges: list[GraphEdge] = []
    for e in edges:
        source = f"{e.source_type}:{e.source_key}"
        target = f"{e.target_type}:{e.target_key}"
        if source not in node_ids:
            add_node(e.source_type, e.source_key, e.source_key, e.confidence, {})
        if target not in node_ids:
            add_node(e.target_type, e.target_key, e.target_key, e.confidence, {})
        graph_edges.append(
            GraphEdge(
                id=e.id,
                source=source,
                target=target,
                relationship=e.rel_type,
                confidence=e.confidence,
                needs_human_review=e.needs_human_review,
                rationale=e.rationale,
            )
        )

    return GraphOut(nodes=nodes, edges=graph_edges)


def _bfs_blast(full: GraphOut, center: str, depth: int) -> BlastRadiusOut:
    adj: dict[str, set[str]] = {n.id: set() for n in full.nodes}
    edge_map: list[GraphEdge] = full.edges
    for e in edge_map:
        adj.setdefault(e.source, set()).add(e.target)
        adj.setdefault(e.target, set()).add(e.source)

    keep = {center}
    frontier = {center}
    for _ in range(depth):
        nxt: set[str] = set()
        for node in frontier:
            nxt |= adj.get(node, set())
        nxt -= keep
        keep |= nxt
        frontier = nxt

    nodes = [n for n in full.nodes if n.id in keep]
    edges = [e for e in edge_map if e.source in keep and e.target in keep]
    return BlastRadiusOut(
        center=center, depth=depth, nodes=nodes, edges=edges, source="postgres"
    )
