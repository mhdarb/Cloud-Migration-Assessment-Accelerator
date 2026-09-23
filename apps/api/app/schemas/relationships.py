from __future__ import annotations

from pydantic import BaseModel


class InferredRelationship(BaseModel):
    """A relationship a `RelationshipInferencer` proposes without direct extracted
    evidence -- always persisted as a low-confidence `DependencyEdge` flagged for human
    review, never silently merged as fact."""

    source_type: str
    source_key: str
    target_type: str
    target_key: str
    relationship: str = "possible_dependency"
    confidence: float
    rationale: str
