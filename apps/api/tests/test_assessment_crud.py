from pathlib import Path

import pytest

from app.models.entities import Assessment, Document, PipelineStatus
from app.services.assessment_service import (
    PipelineBusy,
    delete_assessment,
    remove_document,
    rename_assessment,
)


def _attach_file(db_session, assessment: Assessment, tmp_path: Path, name: str) -> Document:
    folder = tmp_path / "uploads" / assessment.id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("sample", encoding="utf-8")
    doc = Document(
        assessment_id=assessment.id,
        filename=name,
        content_type="text/plain",
        storage_path=str(path),
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def test_rename_assessment(db_session, assessment):
    updated = rename_assessment(db_session, assessment, "  Wave-2  ")
    assert updated.name == "Wave-2"


def test_delete_assessment_removes_row_and_storage(db_session, assessment, tmp_path):
    doc = _attach_file(db_session, assessment, tmp_path, "inv.txt")
    storage_root = Path(doc.storage_path).parent
    assert storage_root.exists()
    aid = assessment.id
    delete_assessment(db_session, assessment)
    assert db_session.query(Assessment).filter(Assessment.id == aid).one_or_none() is None
    assert db_session.query(Document).filter(Document.assessment_id == aid).count() == 0
    assert not storage_root.exists()


def test_delete_assessment_blocked_when_in_flight(db_session, assessment):
    assessment.status = PipelineStatus.extracting
    db_session.commit()
    with pytest.raises(PipelineBusy):
        delete_assessment(db_session, assessment)
    assert db_session.query(Assessment).filter(Assessment.id == assessment.id).one()


def test_remove_document_unlinks_file(db_session, assessment, tmp_path):
    doc = _attach_file(db_session, assessment, tmp_path, "keep.txt")
    extra = _attach_file(db_session, assessment, tmp_path, "drop.txt")
    path = Path(extra.storage_path)
    updated, rerun = remove_document(db_session, assessment, extra.id)
    assert rerun is True
    assert path.exists() is False
    assert {d.filename for d in updated.documents} == {"keep.txt"}
    assert doc.id in {d.id for d in updated.documents}


def test_remove_last_document_clears_without_rerun(db_session, assessment, tmp_path):
    doc = _attach_file(db_session, assessment, tmp_path, "only.txt")
    path = Path(doc.storage_path)
    updated, rerun = remove_document(db_session, assessment, doc.id)
    assert rerun is False
    assert updated.status == PipelineStatus.pending
    assert updated.documents == []
    assert path.exists() is False


def test_remove_document_blocked_when_in_flight(db_session, assessment, tmp_path):
    doc = _attach_file(db_session, assessment, tmp_path, "busy.txt")
    assessment.status = PipelineStatus.ingesting
    db_session.commit()
    with pytest.raises(PipelineBusy):
        remove_document(db_session, assessment, doc.id)
    assert Path(doc.storage_path).exists()
