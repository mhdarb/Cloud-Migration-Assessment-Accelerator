from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.neo4j_graph import neo4j_available, sync_assessment


class Neo4jGraphSink:
    def available(self) -> bool:
        return neo4j_available()

    def sync(self, db: Session, assessment_id: str) -> dict[str, Any]:
        return sync_assessment(db, assessment_id)


class NullGraphSink:
    def available(self) -> bool:
        return False

    def sync(self, db: Session, assessment_id: str) -> dict[str, Any]:
        return {"synced": False, "reason": "graph_sink_disabled"}
