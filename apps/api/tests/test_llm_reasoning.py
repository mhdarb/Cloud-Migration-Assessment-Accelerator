import json
from types import SimpleNamespace

from app.config import Settings
from app.models.entities import Claim, Conflict
from app.schemas.api import ExtractedClaim, ExtractionResult
from app.services.agent_extract import extra_gap_query
from app.services.llm_clients import DisabledChatCompleter, get_chat_completer
from app.services.llm_reasoning import (
    GroundedProse,
    explain_conflict,
    rewrite_question_answer,
    rewrite_readiness_summary,
)
from app.services.reconciliation import persist_extraction
from app.services.report import generate_report


class _StubCompleter:
    enabled = True
    source = "azure-openai"

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, system: str, user: str, *, temperature=0.1, json_mode=False):
        return self._text


def test_azure_enables_chat_llm_when_mock_off():
    s = Settings(
        mock_llm=False,
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="azure-key",
    )
    assert s.chat_llm_configured is True
    assert s.use_mock_llm is False


def test_mock_stays_on_without_azure():
    s = Settings(
        mock_llm=False,
        azure_openai_endpoint="",
        azure_openai_api_key="",
    )
    assert s.chat_llm_configured is False
    assert s.use_mock_llm is True


def test_question_rewrite_uses_llm_prose():
    text, source = rewrite_question_answer(
        "Which OS?",
        [{"entity": "server:app-01", "attribute": "os", "value": "Ubuntu 22"}],
        ["app-01 runs Ubuntu 22"],
        "server:app-01 os=Ubuntu 22",
        completer=_StubCompleter("Ubuntu 22 is the evidenced operating system."),
    )
    assert source == "llm"
    assert "Ubuntu 22" in text


def test_question_rewrite_falls_back_without_llm():
    text, source = rewrite_question_answer(
        "Which OS?",
        [{"value": "x"}],
        ["q"],
        "template",
        completer=DisabledChatCompleter(),
    )
    assert source == "template"
    assert text == "template"


def test_batch_question_rewrite():
    answers = [
        {
            "id": "q1",
            "question": "Which OS?",
            "facts": [{"value": "Ubuntu 22"}],
            "evidence": [{"quote": "runs Ubuntu 22"}],
            "answer": "template",
            "answer_source": "template",
        }
    ]
    GroundedProse(
        _StubCompleter(json.dumps({"answers": [{"id": "q1", "text": "Ubuntu 22 is evidenced."}]}))
    ).rewrite_question_answers(answers)
    assert answers[0]["answer"] == "Ubuntu 22 is evidenced."
    assert answers[0]["answer_source"] == "llm"


def test_conflict_notes_use_llm(db_session, assessment):
    from app.models.entities import Chunk, Document, DocumentType

    inv = Document(
        assessment_id=assessment.id,
        filename="cmdb.xlsx",
        content_type="application/vnd.ms-excel",
        storage_path="/tmp/x",
        doc_type=DocumentType.inventory,
        precedence=100,
    )
    arch = Document(
        assessment_id=assessment.id,
        filename="arch.docx",
        content_type="application/docx",
        storage_path="/tmp/y",
        doc_type=DocumentType.architecture,
        precedence=80,
    )
    db_session.add_all([inv, arch])
    db_session.flush()
    c_inv = Chunk(
        assessment_id=assessment.id,
        document_id=inv.id,
        chunk_index=0,
        page=1,
        text="Windows",
    )
    c_arch = Chunk(
        assessment_id=assessment.id,
        document_id=arch.id,
        chunk_index=0,
        page=1,
        text="RHEL",
    )
    db_session.add_all([c_inv, c_arch])
    db_session.commit()
    persist_extraction(
        db_session,
        assessment.id,
        ExtractionResult(
            claims=[
                ExtractedClaim(
                    entity_type="server",
                    entity_key="app-01",
                    attribute="os",
                    value="Windows Server 2019",
                    confidence=0.8,
                    chunk_ids=[c_inv.id],
                    evidence_quote="Windows",
                ),
                ExtractedClaim(
                    entity_type="server",
                    entity_key="app-01",
                    attribute="os",
                    value="RHEL 8",
                    confidence=0.9,
                    chunk_ids=[c_arch.id],
                    evidence_quote="RHEL",
                ),
            ]
        ),
        prose=GroundedProse(
            _StubCompleter("Inventory Windows beats architecture RHEL by precedence.")
        ),
    )
    row = db_session.query(Conflict).one()
    assert "precedence" in row.resolution_notes.lower()
    selected = (
        db_session.query(Claim)
        .filter(Claim.assessment_id == assessment.id, Claim.is_selected.is_(True))
        .one()
    )
    assert selected.value == "Windows Server 2019"


def test_readiness_summary_uses_llm(db_session, assessment):
    output = generate_report(
        db_session,
        assessment.id,
        prose=GroundedProse(_StubCompleter("LLM readiness: inventory is incomplete.")),
    )
    assert output.readiness_summary == "LLM readiness: inventory is incomplete."


def test_explain_conflict_template_when_disabled():
    assert explain_conflict({"entity": "x"}, "fallback", completer=DisabledChatCompleter()) == "fallback"


def test_chat_extractor_parses_json():
    from app.services.llm_extractors import ChatLlmExtractor

    result = ChatLlmExtractor(
        _StubCompleter(
            json.dumps(
                {
                    "claims": [
                        {
                            "entity_type": "server",
                            "entity_key": "app-01",
                            "attribute": "os",
                            "value": "Ubuntu 22",
                            "confidence": 0.9,
                            "chunk_ids": ["c1"],
                            "evidence_quote": "Ubuntu 22",
                        }
                    ],
                    "dependencies": [],
                    "gaps": [],
                    "assumptions": [],
                }
            )
        )
    ).extract("os", [{"chunk_id": "c1", "text": "Ubuntu 22"}])
    assert len(result.claims) == 1
    assert result.claims[0].value == "Ubuntu 22"


def test_get_llm_extractor_uses_chat_when_enabled(monkeypatch):
    from app.services.llm_extractors import ChatLlmExtractor
    from app.services.providers import get_llm_extractor

    monkeypatch.setattr(
        "app.services.providers.get_settings",
        lambda: SimpleNamespace(use_mock_llm=False, extraction_strategy="llm"),
    )
    monkeypatch.setattr(
        "app.services.providers.get_chat_completer",
        lambda: _StubCompleter("{}"),
    )
    assert isinstance(get_llm_extractor(), ChatLlmExtractor)


def test_rewrite_summary_template_when_disabled():
    assert rewrite_readiness_summary({}, "bits", completer=DisabledChatCompleter()) == "bits"


def test_extra_gap_query_caps_and_dedupes():
    q = extra_gap_query(["RTO missing", " RTO missing ", "latency"])
    assert q is not None
    assert q.startswith("Find evidence for remaining assessment gaps:")
    assert q.count("RTO missing") == 1


def test_get_chat_completer_disabled_when_mock(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    assert get_chat_completer().enabled is False
