from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _as_utc(value: datetime | None) -> datetime | None:
    """The DB stores naive UTC; tag it so it serializes as "...+00:00". A bare ISO string
    without an offset is parsed by browsers as *local* time, skewing any client math."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class _PipelineTiming(BaseModel):
    """Runtime of the latest pipeline run. `runtime_seconds` is computed server-side (live
    while running, final once finished) and is what the UI timer is anchored on."""

    pipeline_started_at: datetime | None = None
    pipeline_finished_at: datetime | None = None
    runtime_seconds: float | None = None

    @field_validator("pipeline_started_at", "pipeline_finished_at", mode="after")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value)


class AssessmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class AssessmentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class DocumentOut(BaseModel):
    id: str
    filename: str
    content_type: str
    doc_type: str
    precedence: int
    page_count: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AssessmentOut(_PipelineTiming):
    id: str
    name: str
    status: str
    workflow_stage: str
    error_message: str | None = None
    metrics: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    documents: list[DocumentOut] = []

    model_config = {"from_attributes": True}


class AssessmentListOut(_PipelineTiming):
    id: str
    name: str
    status: str
    workflow_stage: str
    created_at: datetime
    updated_at: datetime
    document_count: int = 0

    model_config = {"from_attributes": True}


class EvidenceOut(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    doc_type: str
    page: int | None = None
    # Format-aware position ("rows 21–40", 'sheet "Servers"', "Q3", "p. 4" for PDFs only).
    # Preferred over `page` for display; `page` is kept for backward compatibility.
    locator: str | None = None
    quote: str | None = None


class ClaimOut(BaseModel):
    id: str
    entity_type: str
    entity_key: str
    attribute: str
    value: str
    confidence: float
    evidence_refs: list[Any] = []
    evidence_quote: str | None = None
    source_document_id: str | None = None
    evidence: list[EvidenceOut] = []
    needs_human_review: bool
    unsupported: bool
    review_status: str
    override_value: str | None = None
    review_notes: str | None = None
    is_selected: bool

    model_config = {"from_attributes": True}


class ClaimReviewRequest(BaseModel):
    action: str  # accept | override | reject
    override_value: str | None = None
    notes: str | None = None


class EntityOut(BaseModel):
    id: str
    name: str
    normalized_key: str
    attributes: dict[str, Any] | None = None
    confidence: float
    entity_type: str

    model_config = {"from_attributes": True}


class GraphNode(BaseModel):
    id: str
    type: str
    label: str
    confidence: float
    attributes: dict[str, Any] = {}
    centrality: float = 0.0


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relationship: str
    confidence: float
    needs_human_review: bool = False
    rationale: str | None = None
    evidence_quote: str | None = None


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ConflictOut(BaseModel):
    id: str
    entity_type: str
    entity_key: str
    attribute: str
    claim_ids: list[str]
    selected_claim_id: str | None = None
    status: str
    resolution_notes: str | None = None

    model_config = {"from_attributes": True}


class AssessmentAnswersOut(BaseModel):
    question_set: str
    answers: list[dict[str, Any]]
    complete: bool
    review_required: bool


class AskQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class InfrastructureRecommendationOut(BaseModel):
    id: str
    server_key: str
    provider: str
    region: str
    recommended_sku: str
    result: dict[str, Any]
    confidence: float
    needs_human_review: bool

    model_config = {"from_attributes": True}


class ReportOut(BaseModel):
    assessment_id: str
    readiness_summary: str
    gaps: list[Any]
    assumptions: list[Any]
    inventory: dict[str, Any]
    dependencies: list[Any]
    evidence_appendix: list[Any]
    report_json: dict[str, Any]
    metrics: dict[str, Any] = {}


class FollowUpNoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=4000)
    tags: list[str] | None = None


class HealthOut(BaseModel):
    status: str
    mock_llm: bool
    azure_openai: bool
    azure_search: bool
    database: str
    rag: bool = False
    embeddings: str = "none"
    vector_index: str = "none"
    guardrails: bool = True
    content_safety: bool = False
    app_profile: str = "local"
    identity_mode: str = "none"


class BlastRadiusOut(BaseModel):
    center: str
    depth: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    source: str = "postgres"


# --- LLM extraction schemas ---


class ExtractedClaim(BaseModel):
    entity_type: str
    entity_key: str
    attribute: str
    value: str
    confidence: float = 0.7
    evidence_quote: str | None = None
    chunk_ids: list[str] = []


class ExtractedDependency(BaseModel):
    source_type: str
    source_key: str
    target_type: str
    target_key: str
    relationship: str = "depends_on"
    confidence: float = 0.7
    evidence_quote: str | None = None
    chunk_ids: list[str] = []


class ExtractionResult(BaseModel):
    claims: list[ExtractedClaim] = []
    dependencies: list[ExtractedDependency] = []
    gaps: list[str] = []
    assumptions: list[str] = []
