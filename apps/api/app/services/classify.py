"""DocClassifier implementations (Strategy pattern): `KeywordClassifier` wraps the
existing filename/keyword ladder unchanged; `LlmClassifier` does zero-shot structured
classification and falls back to `KeywordClassifier` when disabled/unavailable/fails —
classification becomes dynamic, but `PRECEDENCE[doc_type]` stays the same fixed table."""

from __future__ import annotations

import json

from app.models.entities import DocumentType
from app.schemas.classification import Classification
from app.services.compat import call_if_supported
from app.services.parsers import infer_doc_type
from app.services.ports import ChatCompleter

_STRONG_NAME_EXTENSIONS = (".zip", ".xlsx", ".xls", ".csv", ".json", ".pdf", ".docx", ".doc")
_STRONG_NAME_KEYWORDS = (
    "inventory",
    "cmdb",
    "requirement",
    "nfr",
    "brd",
    "rfc",
    "questionnaire",
    "runbook",
    "sop",
    "architecture",
)

# `code_snapshot` is determined purely by file extension (a ZIP) in ingest.py, which
# only ever calls a DocClassifier for non-.zip files — it's never a valid content-based
# classification target, so it's excluded from what the LLM classifier is asked for.
_CLASSIFIABLE_TYPES = tuple(t for t in DocumentType if t != DocumentType.code_snapshot)


class KeywordClassifier:
    """Wraps `parsers.infer_doc_type` unchanged, grading confidence by match specificity."""

    def classify(self, filename: str, text_sample: str) -> Classification:
        doc_type = infer_doc_type(filename, text_sample)
        name = filename.lower()
        if name.endswith(_STRONG_NAME_EXTENSIONS) or any(k in name for k in _STRONG_NAME_KEYWORDS):
            return Classification(
                doc_type=doc_type,
                confidence=0.9,
                rationale=f"Filename '{filename}' strongly signals {doc_type.value}",
            )
        if doc_type != DocumentType.unknown:
            return Classification(
                doc_type=doc_type,
                confidence=0.6,
                rationale=f"Inferred {doc_type.value} from document text content, not filename",
            )
        return Classification(
            doc_type=doc_type,
            confidence=0.4,
            rationale="No strong filename or content signal found",
        )


class LlmClassifier:
    """Zero-shot structured-output classification over the fixed DocumentType taxonomy."""

    def __init__(self, completer: ChatCompleter) -> None:
        self._completer = completer
        self._fallback = KeywordClassifier()

    def classify(self, filename: str, text_sample: str) -> Classification:
        if not self._completer.enabled:
            return self._fallback.classify(filename, text_sample)
        system = (
            "Classify a cloud migration assessment document into exactly one of: "
            f"{', '.join(t.value for t in _CLASSIFIABLE_TYPES)}. "
            "This document is not a ZIP archive, so never classify it as code_snapshot. "
            "Base the decision on the filename and text sample only. Give a one-sentence rationale."
        )
        user = f"Filename: {filename}\nText sample:\n{text_sample[:1500]}"
        content = call_if_supported(
            self._completer.complete,
            system,
            user,
            temperature=0.0,
            json_mode=True,
            response_schema=Classification,
        )
        if not content:
            return self._fallback.classify(filename, text_sample)
        try:
            result = Classification.model_validate(json.loads(content))
        except Exception:
            return self._fallback.classify(filename, text_sample)
        if result.doc_type == DocumentType.code_snapshot:
            # Would make ingest.py try to unzip a non-zip file — never valid here.
            return self._fallback.classify(filename, text_sample)
        return result
