#!/usr/bin/env python3
"""Smoke test: sample-data → pipeline → RAG metrics + Neo4j graph."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"
SAMPLE = ROOT / "sample-data"


def main() -> int:
    client = httpx.Client(timeout=60.0)
    health = client.get(f"{API}/health").json()
    print("health:", health)
    if not health.get("rag"):
        print("WARN: RAG disabled")

    files = [
        ("files", ("architecture-overview.docx", (SAMPLE / "architecture-overview.docx").read_bytes())),
        ("files", ("cmdb-inventory.xlsx", (SAMPLE / "cmdb-inventory.xlsx").read_bytes())),
        ("files", ("assessment-questionnaire.docx", (SAMPLE / "assessment-questionnaire.docx").read_bytes())),
    ]
    resp = client.post(
        f"{API}/assessments",
        data={"name": "RAG Neo4j Smoke"},
        files=files,
    )
    resp.raise_for_status()
    assessment = resp.json()
    aid = assessment["id"]
    print("assessment:", aid)

    for i in range(40):
        a = client.get(f"{API}/assessments/{aid}").json()
        print(f"poll {i}: {a['status']}")
        if a["status"] in {"completed", "failed"}:
            break
        time.sleep(1)
    else:
        print("TIMEOUT")
        return 1

    if a["status"] != "completed":
        print("FAILED", a.get("error_message"))
        return 1

    metrics = a.get("metrics") or {}
    print("metrics:", metrics)
    assert int(metrics.get("chunks_retrieved") or 0) > 0, "expected RAG retrieval"
    assert int(metrics.get("rag_queries") or 0) > 0, "expected rag_queries"
    assert int(metrics.get("embeddings_indexed") or 0) > 0, "expected embeddings"

    graph = client.get(f"{API}/assessments/{aid}/graph").json()
    print("graph nodes", len(graph["nodes"]), "edges", len(graph["edges"]))
    assert len(graph["nodes"]) >= 3, "expected graph nodes"

    # pick an application node for blast radius
    center = next((n["id"] for n in graph["nodes"] if n["type"] == "application"), None)
    assert center, "expected an application node"
    blast = client.get(
        f"{API}/assessments/{aid}/graph/blast-radius",
        params={"node": center, "depth": 2},
    ).json()
    print("blast:", blast["source"], "nodes", len(blast["nodes"]), "center", blast["center"])
    assert blast["center"] == center
    assert len(blast["nodes"]) >= 1

    claims = client.get(f"{API}/assessments/{aid}/claims").json()
    cited = sum(1 for c in claims if c.get("evidence_refs"))
    print(f"claims={len(claims)} cited={cited}")
    assert cited > 0, "expected cited claims"

    if health.get("neo4j"):
        assert metrics.get("neo4j_synced") in (True, 1, "true"), "expected neo4j sync when available"
        print("Neo4j sync OK")
    else:
        print("Neo4j down — Postgres graph fallback used (acceptable)")

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
