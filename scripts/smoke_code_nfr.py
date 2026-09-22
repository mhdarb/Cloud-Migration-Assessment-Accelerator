#!/usr/bin/env python3
"""Smoke test: inventory + requirements NFR + sample-app.zip → runtime/NFR claims."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"
SAMPLE = ROOT / "sample-data"


def main() -> int:
    client = httpx.Client(timeout=90.0)
    health = client.get(f"{API}/health").json()
    print("health:", health)

    files = [
        ("files", ("cmdb-inventory.xlsx", (SAMPLE / "cmdb-inventory.xlsx").read_bytes())),
        ("files", ("requirements-nfr.docx", (SAMPLE / "requirements-nfr.docx").read_bytes())),
        ("files", ("sample-app.zip", (SAMPLE / "sample-app.zip").read_bytes())),
        ("files", ("architecture-overview.docx", (SAMPLE / "architecture-overview.docx").read_bytes())),
    ]
    resp = client.post(
        f"{API}/assessments",
        data={"name": "Code+NFR Smoke"},
        files=files,
    )
    resp.raise_for_status()
    aid = resp.json()["id"]
    print("assessment:", aid)

    for i in range(60):
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
    assert int(metrics.get("code_snapshot_count") or 0) >= 1, "expected code snapshot"
    assert int(metrics.get("manifest_files_parsed") or 0) >= 1, "expected manifests"
    assert int(metrics.get("nfr_claim_count") or 0) >= 1, "expected NFR claims"

    claims = client.get(f"{API}/assessments/{aid}/claims").json()
    runtimes = [c for c in claims if c["attribute"] == "runtime"]
    frameworks = [c for c in claims if c["attribute"] == "framework"]
    nfr = [
        c
        for c in claims
        if c["entity_type"] == "business"
        or c["attribute"] in {"sla", "availability", "compliance", "rto", "rpo"}
    ]
    print("runtime claims:", [(c["entity_key"], c["value"]) for c in runtimes])
    print("framework claims:", [(c["entity_key"], c["value"]) for c in frameworks])
    print("nfr sample:", [(c["attribute"], c["value"]) for c in nfr[:8]])
    assert any(c["value"] == "node" for c in runtimes), "expected node runtime from package.json"
    assert nfr, "expected NFR/compliance claims from requirements doc"

    # infra deps from manifests
    ents = client.get(f"{API}/assessments/{aid}/entities").json()
    keys = {e["normalized_key"] for e in ents}
    print("entity keys sample:", sorted(list(keys))[:20])
    assert any(k in keys for k in ("postgres", "redis", "kafka")), "expected infra from manifests"

    graph = client.get(f"{API}/assessments/{aid}/graph").json()
    assert len(graph["nodes"]) >= 5
    print("graph", len(graph["nodes"]), "nodes", len(graph["edges"]), "edges")
    if health.get("neo4j"):
        assert metrics.get("neo4j_synced") in (True, 1, "true")
        print("Neo4j sync OK")

    report = client.get(f"{API}/assessments/{aid}/report").json()
    nfr_section = (report.get("inventory") or {}).get("nfr_and_runtime") or []
    print("report nfr_and_runtime count:", len(nfr_section))
    assert nfr_section, "expected nfr_and_runtime in report inventory"

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
