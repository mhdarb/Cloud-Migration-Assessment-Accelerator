import json

from app.models.entities import DocumentType
from app.services.classify import KeywordClassifier, LlmClassifier


def test_keyword_classifier_high_confidence_on_strong_filename_signal():
    result = KeywordClassifier().classify("cmdb-inventory.xlsx", "")
    assert result.doc_type == DocumentType.inventory
    assert result.confidence >= 0.85


def test_keyword_classifier_medium_confidence_on_content_only_signal():
    result = KeywordClassifier().classify(
        "notes.txt", "This document covers non-functional requirements and SLAs."
    )
    assert result.doc_type == DocumentType.requirements
    assert 0.5 <= result.confidence < 0.85


def test_keyword_classifier_low_confidence_when_unresolved():
    result = KeywordClassifier().classify("notes.txt", "")
    assert result.doc_type == DocumentType.unknown
    assert result.confidence < 0.5


def test_keyword_classifier_zip_is_code_snapshot():
    result = KeywordClassifier().classify("sample-app.zip", "")
    assert result.doc_type == DocumentType.code_snapshot
    assert result.confidence >= 0.85


class _StubCompleter:
    enabled = True
    source = "test"

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, system, user, **kwargs):
        return self._text


def test_llm_classifier_never_returns_code_snapshot_for_non_zip():
    """ingest.py only calls a DocClassifier for non-.zip files (see classify.py's
    _CLASSIFIABLE_TYPES comment) — code_snapshot is not a valid content-based label
    and would make ingest.py try to unzip a file that isn't a zip."""
    misbehaving = _StubCompleter(
        json.dumps({"doc_type": "code_snapshot", "confidence": 0.9, "rationale": "looks like code"})
    )
    result = LlmClassifier(misbehaving).classify("notes.txt", "some content")
    assert result.doc_type != DocumentType.code_snapshot


def test_llm_classifier_uses_completer_result_when_valid():
    completer = _StubCompleter(
        json.dumps({"doc_type": "requirements", "confidence": 0.95, "rationale": "mentions SLAs"})
    )
    result = LlmClassifier(completer).classify("notes.txt", "SLA requirements")
    assert result.doc_type == DocumentType.requirements
    assert result.confidence == 0.95
