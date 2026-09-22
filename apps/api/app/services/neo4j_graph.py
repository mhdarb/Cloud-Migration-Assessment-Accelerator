from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.schemas.api import BlastRadiusOut, GraphEdge, GraphNode, GraphOut
from app.services.inventory import load_inventory

logger = logging.getLogger(__name__)

_NEO4J_CACHE: tuple[float, bool] | None = None
_NEO4J_CACHE_TTL = 15.0

REL_MAP = {
    "hosted_on": "HOSTED_ON",
    "uses": "USES",
    "depends_on": "DEPENDS_ON",
    "calls": "CALLS",
    "integrates_with": "CALLS",
}

LABEL_MAP = {
    "application": "Application",
    "server": "Server",
    "database": "Database",
    "interface": "Interface",
}


def purge_assessment(assessment_id: str) -> None:
    """Remove graph nodes for an assessment. No-op if Neo4j is down."""
    if not neo4j_available():
        return
    driver = _driver()
    if driver is None:
        return
    try:
        with driver.session() as session:
            session.execute_write(_clear_assessment, assessment_id)
    except Exception:
        logger.exception("Neo4j purge failed for %s", assessment_id)
    finally:
        try:
            driver.close()
        except Exception:
            pass


def neo4j_available() -> bool:
    global _NEO4J_CACHE
    now = time.time()
    if _NEO4J_CACHE and now - _NEO4J_CACHE[0] < _NEO4J_CACHE_TTL:
        return _NEO4J_CACHE[1]

    driver = _driver()
    if driver is None:
        _NEO4J_CACHE = (now, False)
        return False
    try:
        driver.verify_connectivity()
        _NEO4J_CACHE = (now, True)
        return True
    except Exception:
        logger.warning("Neo4j not reachable")
        _NEO4J_CACHE = (now, False)
        return False
    finally:
        try:
            driver.close()
        except Exception:
            pass


def _driver():
    settings = get_settings()
    if not settings.neo4j_uri or not settings.neo4j_password:
        return None
    try:
        from neo4j import GraphDatabase

        return GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=2.0,
            max_connection_lifetime=30,
            connection_acquisition_timeout=3.0,
            max_transaction_retry_time=0.0,
        )
    except Exception:
        logger.exception("Failed to create Neo4j driver")
        return None


def sync_assessment(db: Session, assessment_id: str) -> dict[str, Any]:
    """Idempotently sync reconciled Postgres entities into Neo4j."""
    if not neo4j_available():
        return {"synced": False, "reason": "neo4j_unavailable"}
    driver = _driver()
    if driver is None:
        return {"synced": False, "reason": "driver_unavailable"}

    snap = load_inventory(
        db, assessment_id, documents=True
    )
    apps = snap.applications
    servers = snap.servers
    databases = snap.databases
    interfaces = snap.interfaces
    edges = snap.edges
    documents = snap.documents

    try:
        with driver.session() as session:
            session.execute_write(_clear_assessment, assessment_id)
            for doc in documents:
                session.execute_write(
                    _upsert_document,
                    assessment_id,
                    doc.id,
                    doc.filename,
                    doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type),
                )
            for a in apps:
                session.execute_write(
                    _upsert_entity,
                    assessment_id,
                    "Application",
                    a.normalized_key,
                    a.name,
                    a.confidence,
                    a.attributes or {},
                )
            for s in servers:
                session.execute_write(
                    _upsert_entity,
                    assessment_id,
                    "Server",
                    s.normalized_key,
                    s.name,
                    s.confidence,
                    s.attributes or {},
                )
            for d in databases:
                session.execute_write(
                    _upsert_entity,
                    assessment_id,
                    "Database",
                    d.normalized_key,
                    d.name,
                    d.confidence,
                    d.attributes or {},
                )
            for i in interfaces:
                session.execute_write(
                    _upsert_entity,
                    assessment_id,
                    "Interface",
                    i.normalized_key,
                    i.name,
                    i.confidence,
                    i.attributes or {},
                )
            for e in edges:
                session.execute_write(
                    _upsert_edge,
                    assessment_id,
                    e.source_type,
                    e.source_key,
                    e.target_type,
                    e.target_key,
                    e.rel_type,
                    e.confidence,
                    e.evidence_refs or [],
                )
        return {
            "synced": True,
            "nodes": len(apps) + len(servers) + len(databases) + len(interfaces),
            "edges": len(edges),
        }
    except Exception as exc:
        logger.exception("Neo4j sync failed")
        return {"synced": False, "reason": str(exc)}
    finally:
        driver.close()


def get_graph(assessment_id: str) -> GraphOut | None:
    if not neo4j_available():
        return None
    driver = _driver()
    if driver is None:
        return None
    try:
        with driver.session() as session:
            node_rows = list(
                session.run(
                    """
                    MATCH (n)
                    WHERE n.assessment_id = $aid
                      AND (n:Application OR n:Server OR n:Database OR n:Interface)
                    RETURN n
                    """,
                    aid=assessment_id,
                )
            )
            nodes: list[GraphNode] = []
            for row in node_rows:
                n = row["n"]
                etype = _node_type(n)
                key = n.get("key")
                nodes.append(
                    GraphNode(
                        id=f"{etype}:{key}",
                        type=etype,
                        label=n.get("name") or key,
                        confidence=float(n.get("confidence") or 0.5),
                        attributes=_parse_attrs(n.get("attributes")),
                    )
                )

            edge_rows = list(
                session.run(
                    """
                    MATCH (a)-[r]->(b)
                    WHERE r.assessment_id = $aid
                    RETURN a, r, b, type(r) AS rel_type
                    """,
                    aid=assessment_id,
                )
            )
            edges: list[GraphEdge] = []
            for row in edge_rows:
                a, r, b = row["a"], row["r"], row["b"]
                edges.append(
                    GraphEdge(
                        id=str(r.element_id),
                        source=f"{_node_type(a)}:{a.get('key')}",
                        target=f"{_node_type(b)}:{b.get('key')}",
                        relationship=str(row["rel_type"]).lower(),
                        confidence=float(r.get("confidence") or 0.5),
                        needs_human_review=float(r.get("confidence") or 0.5) < 0.7,
                    )
                )
            return GraphOut(nodes=nodes, edges=edges)
    except Exception:
        logger.exception("Neo4j get_graph failed")
        return None
    finally:
        driver.close()


def blast_radius(assessment_id: str, node: str, depth: int = 2) -> BlastRadiusOut | None:
    """Expand neighborhood around node id like application:billing-service."""
    if not neo4j_available():
        return None
    driver = _driver()
    if driver is None:
        return None
    if ":" not in node:
        return None
    etype, key = node.split(":", 1)
    label = LABEL_MAP.get(etype.lower())
    if not label:
        return None
    depth = max(1, min(int(depth), 4))
    try:
        with driver.session() as session:
            # Collect neighborhood node keys via variable-length path
            q = f"""
            MATCH (center:{label} {{key: $key, assessment_id: $aid}})
            OPTIONAL MATCH (center)-[*1..{depth}]-(n)
            WHERE n.assessment_id = $aid
              AND (n:Application OR n:Server OR n:Database OR n:Interface)
            WITH center, collect(DISTINCT n) AS neighbors
            RETURN center, neighbors
            """
            rec = session.run(q, key=key, aid=assessment_id).single()
            if not rec or rec["center"] is None:
                return BlastRadiusOut(
                    center=node, depth=depth, nodes=[], edges=[], source="neo4j"
                )

            nodes: list[GraphNode] = []
            seen: set[str] = set()
            for n in [rec["center"], *(rec["neighbors"] or [])]:
                if n is None:
                    continue
                nt = _node_type(n)
                nid = f"{nt}:{n.get('key')}"
                if nid in seen:
                    continue
                seen.add(nid)
                nodes.append(
                    GraphNode(
                        id=nid,
                        type=nt,
                        label=n.get("name") or n.get("key"),
                        confidence=float(n.get("confidence") or 0.5),
                        attributes=_parse_attrs(n.get("attributes")),
                    )
                )

            keys = [n.id.split(":", 1)[1] for n in nodes]
            edge_rows = session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE r.assessment_id = $aid
                  AND a.key IN $keys AND b.key IN $keys
                RETURN a, r, b, type(r) AS rel_type
                """,
                aid=assessment_id,
                keys=keys,
            )
            edges: list[GraphEdge] = []
            for row in edge_rows:
                a, r, b = row["a"], row["r"], row["b"]
                edges.append(
                    GraphEdge(
                        id=str(r.element_id),
                        source=f"{_node_type(a)}:{a.get('key')}",
                        target=f"{_node_type(b)}:{b.get('key')}",
                        relationship=str(row["rel_type"]).lower(),
                        confidence=float(r.get("confidence") or 0.5),
                        needs_human_review=False,
                    )
                )
            return BlastRadiusOut(
                center=node, depth=depth, nodes=nodes, edges=edges, source="neo4j"
            )
    except Exception:
        logger.exception("Neo4j blast_radius failed")
        return None
    finally:
        driver.close()


def _clear_assessment(tx, assessment_id: str) -> None:
    tx.run(
        "MATCH (n {assessment_id: $aid}) DETACH DELETE n",
        aid=assessment_id,
    )


def _upsert_document(tx, assessment_id: str, doc_id: str, filename: str, doc_type: str) -> None:
    tx.run(
        """
        MERGE (d:Document {id: $id, assessment_id: $aid})
        SET d.filename = $filename, d.doc_type = $doc_type
        """,
        id=doc_id,
        aid=assessment_id,
        filename=filename,
        doc_type=doc_type,
    )


def _upsert_entity(
    tx,
    assessment_id: str,
    label: str,
    key: str,
    name: str,
    confidence: float,
    attributes: dict,
) -> None:
    tx.run(
        f"""
        MERGE (n:{label} {{key: $key, assessment_id: $aid}})
        SET n.name = $name,
            n.confidence = $confidence,
            n.attributes = $attributes
        """,
        key=key,
        aid=assessment_id,
        name=name,
        confidence=confidence,
        attributes=json.dumps(attributes),
    )


def _upsert_edge(
    tx,
    assessment_id: str,
    source_type: str,
    source_key: str,
    target_type: str,
    target_key: str,
    rel_type: str,
    confidence: float,
    evidence_refs: list,
) -> None:
    src_label = LABEL_MAP.get(source_type.lower(), "Application")
    tgt_label = LABEL_MAP.get(target_type.lower(), "Application")
    rel = REL_MAP.get(rel_type.lower(), "DEPENDS_ON")
    tx.run(
        f"""
        MERGE (a:{src_label} {{key: $sk, assessment_id: $aid}})
        ON CREATE SET a.name = $sk, a.confidence = $confidence
        MERGE (b:{tgt_label} {{key: $tk, assessment_id: $aid}})
        ON CREATE SET b.name = $tk, b.confidence = $confidence
        MERGE (a)-[r:{rel} {{assessment_id: $aid}}]->(b)
        SET r.confidence = $confidence,
            r.evidence_refs = $evidence,
            r.rel_type = $rel_type
        """,
        sk=source_key,
        tk=target_key,
        aid=assessment_id,
        confidence=confidence,
        evidence=evidence_refs,
        rel_type=rel_type,
    )


def _node_type(n) -> str:
    labels = list(n.labels)
    for lb in labels:
        low = lb.lower()
        if low in LABEL_MAP:
            return low
    return labels[0].lower() if labels else "entity"


def _parse_attrs(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}
