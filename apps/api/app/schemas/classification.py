from __future__ import annotations

from pydantic import BaseModel

from app.models.entities import DocumentType


class Classification(BaseModel):
    doc_type: DocumentType
    confidence: float = 0.5
    rationale: str = ""
