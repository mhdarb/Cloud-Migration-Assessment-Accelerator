from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _enum(enum_cls):
    return Enum(enum_cls, values_callable=lambda x: [e.value for e in x], native_enum=False)


def _uuid() -> str:
    return str(uuid.uuid4())


class PipelineStatus(str, enum.Enum):
    pending = "pending"
    ingesting = "ingesting"
    extracting = "extracting"
    reconciling = "reconciling"
    building_graph = "building_graph"
    generating_report = "generating_report"
    completed = "completed"
    failed = "failed"


class WorkflowStage(str, enum.Enum):
    research = "research"
    plan = "plan"
    implement = "implement"
    review = "review"
    follow_up = "follow_up"


class DocumentType(str, enum.Enum):
    architecture = "architecture"
    inventory = "inventory"
    questionnaire = "questionnaire"
    runbook = "runbook"
    code_snapshot = "code_snapshot"
    requirements = "requirements"
    unknown = "unknown"


class ReviewStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    overridden = "overridden"
    rejected = "rejected"
    # A conflict candidate that lost because the reviewer accepted/overrode a sibling.
    superseded = "superseded"
    # A conflict candidate the reviewer set aside by dismissing the whole conflict
    # ("none of these values is right — treat the attribute as unknown").
    dismissed = "dismissed"


class ConflictStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


class QuestionOrigin(str, enum.Enum):
    uploaded = "uploaded"
    ad_hoc = "ad_hoc"
    dynamic = "dynamic"
    # From a client questionnaire uploaded to be *answered* (Questions tab), as opposed to
    # `uploaded` — questions parsed out of a questionnaire ingested as evidence.
    questionnaire = "questionnaire"


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[PipelineStatus] = mapped_column(
        _enum(PipelineStatus), default=PipelineStatus.pending
    )
    workflow_stage: Mapped[WorkflowStage] = mapped_column(
        _enum(WorkflowStage), default=WorkflowStage.research
    )
    plan_checklist: Mapped[dict | None] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    # Wall-clock bounds of the most recent pipeline run (naive UTC, like the other
    # timestamps). Nullable so existing DBs pick them up via `_add_missing_columns`.
    pipeline_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pipeline_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def runtime_seconds(self) -> float | None:
        """Duration of the latest pipeline run: final once it has finished, live (so far)
        while it is still running, None if it has never run. Computed on the server so the
        UI never has to subtract its own clock from a server timestamp (clock skew, and
        naive-UTC strings that browsers parse as local time)."""
        if self.pipeline_started_at is None:
            return None
        end = self.pipeline_finished_at or datetime.utcnow()
        return max(0.0, (end - self.pipeline_started_at).total_seconds())

    documents: Mapped[list[Document]] = relationship(back_populates="assessment")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="assessment")
    claims: Mapped[list[Claim]] = relationship(back_populates="assessment")
    applications: Mapped[list[Application]] = relationship(back_populates="assessment")
    servers: Mapped[list[Server]] = relationship(back_populates="assessment")
    databases: Mapped[list[DatabaseEntity]] = relationship(back_populates="assessment")
    interfaces: Mapped[list[Interface]] = relationship(back_populates="assessment")
    edges: Mapped[list[DependencyEdge]] = relationship(back_populates="assessment")
    conflicts: Mapped[list[Conflict]] = relationship(back_populates="assessment")
    recommendations: Mapped[list[InfrastructureRecommendation]] = relationship(
        back_populates="assessment"
    )
    output: Mapped[AssessmentOutput | None] = relationship(
        back_populates="assessment", uselist=False
    )
    engagement_questions: Mapped[list[EngagementQuestion]] = relationship(
        back_populates="assessment"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    doc_type: Mapped[DocumentType] = mapped_column(
        _enum(DocumentType), default=DocumentType.unknown
    )
    storage_path: Mapped[str] = mapped_column(String(1024))
    precedence: Mapped[int] = mapped_column(Integer, default=50)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_summary: Mapped[dict | None] = mapped_column(JSON, default=dict)
    doc_type_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    doc_type_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessment: Mapped[Assessment] = relationship(back_populates="documents")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="document")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offset_start: Mapped[int] = mapped_column(Integer, default=0)
    offset_end: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    search_indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="chunks")
    document: Mapped[Document] = relationship(back_populates="chunks")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_key: Mapped[str] = mapped_column(String(255))
    attribute: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    unsupported: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[ReviewStatus] = mapped_column(
        _enum(ReviewStatus), default=ReviewStatus.pending
    )
    override_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="claims")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    name: Mapped[str] = mapped_column(String(255))
    normalized_key: Mapped[str] = mapped_column(String(255))
    attributes: Mapped[dict | None] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    assessment: Mapped[Assessment] = relationship(back_populates="applications")


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    name: Mapped[str] = mapped_column(String(255))
    normalized_key: Mapped[str] = mapped_column(String(255))
    attributes: Mapped[dict | None] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    assessment: Mapped[Assessment] = relationship(back_populates="servers")


class DatabaseEntity(Base):
    __tablename__ = "databases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    name: Mapped[str] = mapped_column(String(255))
    normalized_key: Mapped[str] = mapped_column(String(255))
    attributes: Mapped[dict | None] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    assessment: Mapped[Assessment] = relationship(back_populates="databases")


class Interface(Base):
    __tablename__ = "interfaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    name: Mapped[str] = mapped_column(String(255))
    normalized_key: Mapped[str] = mapped_column(String(255))
    attributes: Mapped[dict | None] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    assessment: Mapped[Assessment] = relationship(back_populates="interfaces")


class DependencyEdge(Base):
    __tablename__ = "dependency_edges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    source_type: Mapped[str] = mapped_column(String(64))
    source_key: Mapped[str] = mapped_column(String(255))
    target_type: Mapped[str] = mapped_column(String(64))
    target_key: Mapped[str] = mapped_column(String(255))
    rel_type: Mapped[str] = mapped_column(String(128), default="depends_on")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    rationale: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Nullable so pre-existing rows (added via `_add_missing_columns`) read as pending.
    review_status: Mapped[ReviewStatus | None] = mapped_column(
        _enum(ReviewStatus), nullable=True, default=ReviewStatus.pending
    )
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="edges")


class Conflict(Base):
    __tablename__ = "conflicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_key: Mapped[str] = mapped_column(String(255))
    attribute: Mapped[str] = mapped_column(String(128))
    claim_ids: Mapped[list] = mapped_column(JSON, default=list)
    selected_claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[ConflictStatus] = mapped_column(
        _enum(ConflictStatus), default=ConflictStatus.open
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="conflicts")


class InfrastructureRecommendation(Base):
    __tablename__ = "infrastructure_recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    server_key: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(32), default="azure")
    region: Mapped[str] = mapped_column(String(64), default="eastus")
    recommended_sku: Mapped[str] = mapped_column(String(128))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    review_status: Mapped[ReviewStatus | None] = mapped_column(
        _enum(ReviewStatus), nullable=True, default=ReviewStatus.pending
    )
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="recommendations")


class ReviewDecision(Base):
    """A reviewer's decision, stored independently of the rows it was made on.

    Claims, conflicts, edges and recommendations are all *derived* data — every pipeline
    run deletes and re-extracts them. Decisions live here instead (never touched by
    `clear_derived`) and are re-applied after each run, matched by a stable key:

      claim          entity_type|entity_key|attribute|<unit-normalized value>
      conflict       entity_type|entity_key|attribute
      edge           source_type|source_key|rel_type|target_type|target_key
      recommendation server_key|recommended_sku

    A decision whose key no longer matches (the source evidence changed, or sizing now
    recommends a different SKU) simply doesn't apply — the new item needs review, which
    is the correct outcome for changed evidence. This table is also the audit trail.
    """

    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_key: Mapped[str] = mapped_column(String(1024))
    action: Mapped[str] = mapped_column(String(32))
    override_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class EngagementQuestion(Base):
    __tablename__ = "engagement_questions"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id", "question_key", name="uq_engagement_question_key"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    origin: Mapped[QuestionOrigin] = mapped_column(
        _enum(QuestionOrigin), default=QuestionOrigin.ad_hoc
    )
    question: Mapped[str] = mapped_column(Text)
    question_key: Mapped[str] = mapped_column(String(240))
    source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_chunk_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessment: Mapped[Assessment] = relationship(back_populates="engagement_questions")


class Questionnaire(Base):
    """A client questionnaire uploaded to be answered (not ingested as evidence). The file
    is kept so answers can be written back into it on download."""

    __tablename__ = "questionnaires"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024))
    file_format: Mapped[str] = mapped_column(String(16))  # xlsx | csv | docx | txt | md | pdf
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Answers are expensive (retrieval + LLM per question), so the last computed set is
    # kept with a fingerprint of the state it was computed from (pipeline run + review
    # decisions); previewing and then downloading costs one computation, not two.
    answers_cache: Mapped[list | None] = mapped_column(JSON, nullable=True)
    answers_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)


class QuestionnaireItem(Base):
    """One question in an uploaded questionnaire, plus where it sits in the file so the
    answer can be written back beside it. Deliberately *not* an `EngagementQuestion`: a
    client's 200-question sheet must not flood the Questions tab or the report, and it is
    answered on demand rather than on every pipeline run."""

    __tablename__ = "questionnaire_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    questionnaire_id: Mapped[str] = mapped_column(ForeignKey("questionnaires.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    question_key: Mapped[str] = mapped_column(String(240))
    locator: Mapped[dict] = mapped_column(JSON, default=dict)


class AssessmentOutput(Base):
    __tablename__ = "assessment_outputs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"), unique=True)
    readiness_summary: Mapped[str] = mapped_column(Text, default="")
    gaps: Mapped[list] = mapped_column(JSON, default=list)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    inventory: Mapped[dict | None] = mapped_column(JSON, default=dict)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    evidence_appendix: Mapped[list] = mapped_column(JSON, default=list)
    report_json: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessment: Mapped[Assessment] = relationship(back_populates="output")


class PipelineLockRow(Base):
    """Multi-worker-safe pipeline lock (used when `pipeline_lock_backend=db`).

    A row's presence means the assessment's pipeline is running. Acquire is a plain
    insert (fails on the primary-key conflict if already locked); release deletes it.
    """

    __tablename__ = "pipeline_locks"

    assessment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    locked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    worker_id: Mapped[str] = mapped_column(String(128), default="")
