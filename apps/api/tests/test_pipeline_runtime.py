"""Pipeline runtime tracking — backs the UI's runtime timer."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from app.models.entities import Assessment, PipelineStatus, WorkflowStage
from app.schemas.api import ExtractionResult
from app.services.assessment_service import assessment_out
from app.services.pipeline import AssessmentPipeline, PipelineServices


# --------------------------------------------------------------------------- #
# The runtime_seconds property
# --------------------------------------------------------------------------- #
def test_runtime_none_before_first_run():
    assert Assessment(name="a").runtime_seconds is None


def test_runtime_is_final_once_finished():
    start = datetime(2026, 1, 1, 12, 0, 0)
    a = Assessment(name="a", pipeline_started_at=start, pipeline_finished_at=start + timedelta(seconds=127))
    assert a.runtime_seconds == 127.0


def test_runtime_is_live_while_running():
    a = Assessment(name="a", pipeline_started_at=datetime.utcnow() - timedelta(seconds=30))
    assert 29.0 <= a.runtime_seconds <= 32.0


def test_timestamps_serialize_as_explicit_utc():
    """A bare ISO string is parsed by browsers as *local* time; the API must tag UTC."""
    start = datetime(2026, 1, 1, 12, 0, 0)
    a = Assessment(
        id="x", name="a", status=PipelineStatus.completed, workflow_stage=WorkflowStage.review,
        created_at=start, updated_at=start,
        pipeline_started_at=start, pipeline_finished_at=start + timedelta(seconds=5),
    )
    a.documents = []
    payload = assessment_out(a).model_dump(mode="json")
    assert payload["pipeline_started_at"].endswith("+00:00") or payload["pipeline_started_at"].endswith("Z")
    assert payload["runtime_seconds"] == 5.0


# --------------------------------------------------------------------------- #
# The pipeline stamps start/finish (success and failure)
# --------------------------------------------------------------------------- #
class _NoopEmbedder:
    def embed_texts(self, texts):
        return [[0.0] for _ in texts]

    def embed_query(self, text):
        return [0.0]


class _NoopRetriever:
    def retrieve(self, *args, **kwargs):
        return []


class _EmptyExtractor:
    def extract_assessment(self, db, assessment_id):
        return ExtractionResult(), {}


class _ExplodingExtractor:
    def extract_assessment(self, db, assessment_id):
        raise RuntimeError("boom")


def _pipeline(db_session, extractor, tmp_path) -> AssessmentPipeline:
    # The pipeline closes its own session when done, so hand it a separate one on the same
    # engine rather than the test's session.
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    return AssessmentPipeline(
        PipelineServices(
            embedder=_NoopEmbedder(),
            indexes=[],
            extractor=extractor,
            retriever=_NoopRetriever(),
            storage_dir=str(tmp_path),
            session_factory=factory,
        )
    )


def test_successful_run_records_start_finish_and_runtime(db_session, assessment, tmp_path):
    _pipeline(db_session, _EmptyExtractor(), tmp_path).run(assessment.id)

    db_session.expire_all()
    a = db_session.get(Assessment, assessment.id)
    assert a.status == PipelineStatus.completed
    assert a.pipeline_started_at is not None
    assert a.pipeline_finished_at is not None
    assert a.pipeline_finished_at >= a.pipeline_started_at
    assert a.runtime_seconds is not None and a.runtime_seconds >= 0
    assert "pipeline_runtime_seconds" in (a.metrics or {})


def test_failed_run_freezes_the_timer(db_session, assessment, tmp_path):
    _pipeline(db_session, _ExplodingExtractor(), tmp_path).run(assessment.id)

    db_session.expire_all()
    a = db_session.get(Assessment, assessment.id)
    assert a.status == PipelineStatus.failed
    assert a.pipeline_started_at is not None
    assert a.pipeline_finished_at is not None, "a failed run must stop the clock, not tick forever"


def test_rerun_resets_the_clock(db_session, assessment, tmp_path):
    old_start = datetime(2020, 1, 1)
    assessment.pipeline_started_at = old_start
    assessment.pipeline_finished_at = old_start + timedelta(hours=1)
    db_session.commit()

    _pipeline(db_session, _EmptyExtractor(), tmp_path).run(assessment.id)

    db_session.expire_all()
    a = db_session.get(Assessment, assessment.id)
    assert a.pipeline_started_at > old_start
    assert a.runtime_seconds < 3600  # not the stale one-hour value
