from app.models.entities import (
    Chunk,
    Claim,
    DependencyEdge,
    Document,
    DocumentType,
    QuestionOrigin,
)
from app.services.assessment_questions import (
    build_assessment_answers,
    persist_ad_hoc_question,
)
from app.services.questionnaire_extract import (
    parse_questionnaire_questions,
    sync_uploaded_questions,
)
from app.services.search import KeywordRetriever


def test_answers_are_grounded_and_versioned(db_session, assessment):
    db_session.add(
        Claim(
            assessment_id=assessment.id,
            entity_type="server",
            entity_key="app-01",
            attribute="os",
            value="Ubuntu 22",
            confidence=0.9,
            evidence_refs=["chunk-1"],
            evidence_quote="app-01 runs Ubuntu 22",
            unsupported=False,
            is_selected=True,
            needs_human_review=False,
        )
    )
    db_session.add(
        DependencyEdge(
            assessment_id=assessment.id,
            source_type="application",
            source_key="portal",
            target_type="server",
            target_key="app-01",
            rel_type="hosted_on",
            confidence=0.9,
            evidence_refs=["chunk-2"],
        )
    )
    db_session.commit()
    result = build_assessment_answers(db_session, assessment.id)
    platform = next(a for a in result["answers"] if a["id"] == "platform_compatibility")
    dependency = next(a for a in result["answers"] if a["id"] == "dependencies")
    assert result["question_set"] == "migration-readiness-v1"
    assert platform["supported"] is True
    assert platform["answer_source"] == "template"
    assert platform["origin"] == "standard"
    assert platform["evidence_refs"] == ["chunk-1"]
    assert dependency["supported"] is True
    assert [a["id"] for a in result["answers"] if a["origin"] == "standard"] == [
        "estate_inventory",
        "dependencies",
        "platform_compatibility",
        "service_levels",
        "security_compliance",
        "migration_blockers",
    ]


def test_standard_question_set_still_has_six(db_session, assessment):
    result = build_assessment_answers(db_session, assessment.id)
    assert result["question_set"] == "migration-readiness-v1"
    assert len([a for a in result["answers"] if a["origin"] == "standard"]) == 6


def test_parse_questionnaire_q_lines():
    text = (
        "Q: Which applications are business critical?\n"
        "A: Billing Service is high.\n"
        "Question: Are there known compliance constraints?\n"
        "Not a question line\n"
        "Q: x\n"
    )
    questions = parse_questionnaire_questions(text)
    assert questions == [
        "Which applications are business critical?",
        "Are there known compliance constraints?",
    ]


def test_sync_uploaded_questions_from_chunks(db_session, assessment):
    doc = Document(
        assessment_id=assessment.id,
        filename="assessment-questionnaire.docx",
        storage_path="/tmp/q.docx",
        doc_type=DocumentType.questionnaire,
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(
        Chunk(
            assessment_id=assessment.id,
            document_id=doc.id,
            chunk_index=0,
            text="Q: Which applications are business critical?\nA: Billing Service.",
        )
    )
    db_session.commit()
    rows = sync_uploaded_questions(db_session, assessment.id)
    assert len(rows) == 1
    assert rows[0].origin == QuestionOrigin.uploaded
    again = sync_uploaded_questions(db_session, assessment.id)
    assert len(again) == 1


def test_ad_hoc_question_matches_selected_claim(db_session, assessment):
    db_session.add(
        Claim(
            assessment_id=assessment.id,
            entity_type="application",
            entity_key="billing-service",
            attribute="business_criticality",
            value="high",
            confidence=0.9,
            evidence_refs=["c-crit"],
            evidence_quote="Billing Service is business critical",
            unsupported=False,
            is_selected=True,
            needs_human_review=False,
        )
    )
    db_session.commit()
    persist_ad_hoc_question(
        db_session, assessment.id, "Which applications are business critical?"
    )
    result = build_assessment_answers(db_session, assessment.id)
    custom = [a for a in result["answers"] if a["origin"] == "ad_hoc"]
    assert len(custom) == 1
    assert custom[0]["supported"] is True
    assert "high" in custom[0]["answer"]
    assert custom[0]["evidence_refs"] == ["c-crit"]


def test_unknown_ad_hoc_question_is_evidence_gap(db_session, assessment):
    persist_ad_hoc_question(
        db_session, assessment.id, "What color is the logo xyzzyfoo?"
    )
    result = build_assessment_answers(db_session, assessment.id)
    custom = next(a for a in result["answers"] if a["origin"] == "ad_hoc")
    assert custom["supported"] is False
    assert custom["facts"] == []
    assert "does not answer" in custom["answer"]


def test_custom_question_can_cite_retrieved_chunk(db_session, assessment):
    doc = Document(
        assessment_id=assessment.id,
        filename="runbook.docx",
        storage_path="/tmp/q.docx",
        doc_type=DocumentType.runbook,
    )
    db_session.add(doc)
    db_session.flush()
    chunk = Chunk(
        assessment_id=assessment.id,
        document_id=doc.id,
        chunk_index=0,
        text="Known gaps: network topology and firewall rules are not documented.",
    )
    db_session.add(chunk)
    db_session.commit()
    persist_ad_hoc_question(db_session, assessment.id, "What known gaps remain?")
    result = build_assessment_answers(
        db_session, assessment.id, retriever=KeywordRetriever(top_k=4)
    )
    custom = next(a for a in result["answers"] if a["origin"] == "ad_hoc")
    assert custom["supported"] is True
    assert custom["facts"] == []
    assert "firewall" in custom["answer"].lower()


def test_custom_question_does_not_cite_questionnaire_chunk(db_session, assessment):
    """The exact scenario this exclusion targets: an `uploaded`-origin question's text
    comes straight out of a questionnaire chunk, so retrieving that same chunk back as
    "evidence" would just cite the question at itself. No other document answers it, so
    the question should come back as an honest evidence gap, not a fabricated citation."""
    doc = Document(
        assessment_id=assessment.id,
        filename="assessment-questionnaire.docx",
        storage_path="/tmp/q.docx",
        doc_type=DocumentType.questionnaire,
    )
    db_session.add(doc)
    db_session.flush()
    chunk = Chunk(
        assessment_id=assessment.id,
        document_id=doc.id,
        chunk_index=0,
        text="Q: Known gaps?\nA: Network topology and firewall rules are not documented.",
    )
    db_session.add(chunk)
    db_session.commit()
    persist_ad_hoc_question(db_session, assessment.id, "What known gaps remain?")
    result = build_assessment_answers(
        db_session, assessment.id, retriever=KeywordRetriever(top_k=4)
    )
    custom = next(a for a in result["answers"] if a["origin"] == "ad_hoc")
    assert custom["supported"] is False
    assert custom["evidence_refs"] == []
    assert "does not answer" in custom["answer"]


def test_custom_question_prefers_non_questionnaire_chunk_when_both_match(db_session, assessment):
    """A questionnaire chunk and a real source chunk both match -- the questionnaire
    chunk must be filtered out, and the real one still gets cited."""
    q_doc = Document(
        assessment_id=assessment.id,
        filename="assessment-questionnaire.docx",
        storage_path="/tmp/q.docx",
        doc_type=DocumentType.questionnaire,
    )
    arch_doc = Document(
        assessment_id=assessment.id,
        filename="architecture.docx",
        storage_path="/tmp/a.docx",
        doc_type=DocumentType.architecture,
    )
    db_session.add_all([q_doc, arch_doc])
    db_session.flush()
    db_session.add_all(
        [
            Chunk(
                assessment_id=assessment.id,
                document_id=q_doc.id,
                chunk_index=0,
                text="Q: Known gaps?\nA: Network topology and firewall rules are not documented.",
            ),
            Chunk(
                assessment_id=assessment.id,
                document_id=arch_doc.id,
                chunk_index=0,
                text="Known gaps: network topology and firewall rules are not documented.",
            ),
        ]
    )
    db_session.commit()
    persist_ad_hoc_question(db_session, assessment.id, "What known gaps remain?")
    result = build_assessment_answers(
        db_session, assessment.id, retriever=KeywordRetriever(top_k=4)
    )
    custom = next(a for a in result["answers"] if a["origin"] == "ad_hoc")
    assert custom["supported"] is True
    assert "firewall" in custom["answer"].lower()
    for ref in custom["evidence_refs"]:
        chunk = db_session.get(Chunk, ref)
        assert chunk.document_id != q_doc.id
