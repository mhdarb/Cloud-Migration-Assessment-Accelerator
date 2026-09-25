from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.entities import Assessment, PipelineStatus, WorkflowStage
from app.services.assessment_questions import plan_dynamic_questions
from app.services.extraction_merge import merge_extractions
from app.services.ingest import clear_derived, ingest_documents
from app.services.llm_reasoning import get_grounded_prose
from app.services.pipeline_lock import Heartbeat, release, try_acquire
from app.services.ports import ClaimExtractor, Embedder, Retriever, VectorIndex
from app.services.questionnaire_extract import sync_uploaded_questions
from app.services.reconciliation import (
    persist_extraction,
    persist_inferred_relationships,
    rematerialize_entities,
)
from app.services.report import generate_report
from app.services.review import (
    reapply_claim_decisions,
    reapply_edge_decisions,
    reapply_recommendation_decisions,
)
from app.services.search import ChunkIndexer
from app.services.sizing import generate_recommendations
from app.services.workflow import append_follow_up

logger = logging.getLogger(__name__)

NFR_ATTRIBUTES = {
    "sla",
    "rto",
    "rpo",
    "availability",
    "latency",
    "compliance",
    "data_residency",
    "encryption",
    "scalability",
    "nfr",
}


@dataclass
class PipelineServices:
    embedder: Embedder
    indexes: Sequence[VectorIndex]
    extractor: ClaimExtractor
    retriever: Retriever
    storage_dir: str
    session_factory: Callable[[], Session]


class AssessmentPipeline:
    def __init__(self, services: PipelineServices) -> None:
        self._services = services
        self._indexer = ChunkIndexer(services.embedder, list(services.indexes))

    def run(self, assessment_id: str) -> None:
        if not try_acquire(assessment_id):
            logger.warning("Pipeline already running for %s", assessment_id)
            return
        db = self._services.session_factory()
        try:
            with Heartbeat(assessment_id, self._services.session_factory):
                self._execute(db, assessment_id)
        except Exception as exc:
            logger.exception("Pipeline failed for %s", assessment_id)
            db.rollback()
            assessment = (
                db.query(Assessment).filter(Assessment.id == assessment_id).one_or_none()
            )
            if assessment:
                assessment.status = PipelineStatus.failed
                assessment.error_message = str(exc)
                assessment.pipeline_finished_at = datetime.utcnow()  # freeze the timer
                db.commit()
        finally:
            db.close()
            release(assessment_id)

    def _execute(self, db: Session, assessment_id: str) -> None:
        assessment = db.query(Assessment).filter(Assessment.id == assessment_id).one()
        assessment.status = PipelineStatus.ingesting
        assessment.workflow_stage = WorkflowStage.research
        assessment.error_message = None
        # Start the runtime clock for this run (a re-run resets it).
        assessment.pipeline_started_at = datetime.utcnow()
        assessment.pipeline_heartbeat_at = assessment.pipeline_started_at
        assessment.pipeline_finished_at = None
        # A new run produces new findings, so any earlier "review completed" sign-off no
        # longer applies; the reviewer re-confirms after decisions are re-applied below.
        if (assessment.metrics or {}).get("review_completed_at"):
            append_follow_up(
                assessment,
                event="review_reopened",
                detail={"note": "Pipeline re-ran; review decisions re-applied, sign-off reset"},
            )
            metrics = dict(assessment.metrics or {})
            metrics.pop("review_completed_at", None)
            assessment.metrics = metrics
        db.commit()

        clear_derived(
            db,
            assessment_id,
            list(self._services.indexes),
            self._services.storage_dir,
        )
        ingested = ingest_documents(db, assessment_id)
        index_meta = self._indexer.embed_and_index(db, assessment_id)

        assessment.status = PipelineStatus.extracting
        assessment.workflow_stage = WorkflowStage.implement
        db.commit()

        extraction, rag_metrics = self._services.extractor.extract_assessment(
            db, assessment_id
        )
        extraction = merge_extractions([extraction, *ingested.manifest_extractions])

        assessment.status = PipelineStatus.reconciling
        db.commit()
        prose = get_grounded_prose()
        persist_extraction(db, assessment_id, extraction, prose=prose)
        # Re-apply the reviewer's earlier decisions to the freshly extracted claims (and
        # conflict dismissals) before anything downstream reads them.
        reapplied = reapply_claim_decisions(db, assessment_id)
        if reapplied:
            rematerialize_entities(db, assessment_id)
        db.commit()
        sync_uploaded_questions(db, assessment_id)
        plan_dynamic_questions(db, assessment_id)

        assessment.status = PipelineStatus.building_graph
        db.commit()
        relationship_meta = persist_inferred_relationships(db, assessment_id)
        reapplied += reapply_edge_decisions(db, assessment_id)
        db.commit()

        assessment.status = PipelineStatus.generating_report
        assessment.workflow_stage = WorkflowStage.review
        db.commit()
        recommendations = generate_recommendations(db, assessment_id)
        reapplied += reapply_recommendation_decisions(db, assessment_id)
        db.commit()
        generate_report(
            db,
            assessment_id,
            extraction,
            prose=prose,
            retriever=self._services.retriever,
            rag_metrics=rag_metrics,
        )

        nfr_claim_count = sum(
            1
            for c in extraction.claims
            if c.attribute in NFR_ATTRIBUTES or c.entity_type == "business"
        )
        metrics = dict(assessment.metrics or {})
        metrics.update(
            {
                "rag_queries": rag_metrics.get("rag_queries", 0),
                "chunks_retrieved": rag_metrics.get("chunks_retrieved", 0),
                "retrieval_mode": rag_metrics.get("retrieval_mode", "none"),
                "agent_harness": rag_metrics.get("agent_harness", "none"),
                "agent_retries": rag_metrics.get("agent_retries", 0),
                "agent_nodes": rag_metrics.get("agent_nodes", 0),
                "guardrails": rag_metrics.get("guardrails", False),
                "guardrail_blocks": rag_metrics.get("guardrail_blocks", 0),
                "guardrail_sanitizes": rag_metrics.get("guardrail_sanitizes", 0),
                "guardrail_events": rag_metrics.get("guardrail_events", 0),
                "embeddings_indexed": index_meta.get("indexed", 0),
                "embeddings_backend": index_meta.get("backend", "none"),
                "index_counts": index_meta.get("per_index", {}),
                "inferred_edges": relationship_meta.get("inferred_edges", 0),
                "code_snapshot_count": ingested.code_snapshot_count,
                "manifest_files_parsed": ingested.manifest_files_parsed,
                "nfr_claim_count": nfr_claim_count,
                "recommendation_count": len(recommendations),
                "review_decisions_reapplied": reapplied,
            }
        )
        assessment.pipeline_finished_at = datetime.utcnow()
        metrics["pipeline_runtime_seconds"] = round(assessment.runtime_seconds or 0.0, 1)
        assessment.metrics = metrics
        assessment.status = PipelineStatus.completed
        db.commit()
        logger.info("Pipeline completed for assessment %s", assessment_id)


def build_pipeline() -> AssessmentPipeline:
    from app.services.providers import build_pipeline_services

    return AssessmentPipeline(build_pipeline_services())


def run_pipeline(assessment_id: str) -> None:
    """Composition-root entry for FastAPI background tasks."""
    build_pipeline().run(assessment_id)
