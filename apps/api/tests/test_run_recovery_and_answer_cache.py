"""Production-hardening: interrupted runs recover, and Questions-tab answers are cached.

- A run lives inside an API process. If the process dies, the assessment used to stay "in
  flight" forever and every re-run/review/delete was refused with 409.
- The Questions tab re-requested answers on every poll and page load, re-running
  retrieval and the LLM prose rewrite for identical output.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.entities import (
    Assessment,
    PipelineLockRow,
    PipelineStatus,
    ReviewDecision,
)
from app.services import assessment_questions as aq
from app.services import pipeline_lock
from app.services.assessment_service import get_assessment
from app.services.pipeline_lock import (
    DbLock,
    Heartbeat,
    beat,
    is_in_flight,
    recover_if_interrupted,
    recover_interrupted_runs,
)


def _running(db, assessment, *, beat_age_s: float, status=PipelineStatus.extracting) -> None:
    now = datetime.utcnow()
    assessment.status = status
    assessment.pipeline_started_at = now - timedelta(seconds=beat_age_s + 60)
    assessment.pipeline_heartbeat_at = now - timedelta(seconds=beat_age_s)
    assessment.pipeline_finished_at = None
    db.commit()


# --------------------------------------------------------------------------- #
# Interrupted-run recovery
# --------------------------------------------------------------------------- #
def test_stale_run_is_marked_failed_on_next_read(db_session, assessment):
    _running(db_session, assessment, beat_age_s=600)
    last_beat = assessment.pipeline_heartbeat_at

    row = get_assessment(db_session, assessment.id)  # what every page poll does

    assert row.status == PipelineStatus.failed and not is_in_flight(row)
    assert "interrupted during extracting" in row.error_message
    assert row.pipeline_finished_at == last_beat  # timer frozen when it was last alive
    assert row.metrics["follow_up_log"][-1]["event"] == "run_interrupted"


def test_live_run_is_left_alone(db_session, assessment):
    _running(db_session, assessment, beat_age_s=10)
    assert get_assessment(db_session, assessment.id).status == PipelineStatus.extracting


def test_run_without_heartbeat_falls_back_to_start_time(db_session, assessment):
    """Runs started before the heartbeat column existed."""
    assessment.status = PipelineStatus.reconciling
    assessment.pipeline_started_at = datetime.utcnow() - timedelta(hours=2)
    assessment.pipeline_heartbeat_at = None
    db_session.commit()
    assert recover_if_interrupted(db_session, assessment) is True


def test_finished_runs_are_never_touched(db_session, assessment):
    for status in (PipelineStatus.completed, PipelineStatus.failed, PipelineStatus.pending):
        assessment.status = status
        assessment.pipeline_heartbeat_at = datetime.utcnow() - timedelta(days=1)
        db_session.commit()
        assert recover_if_interrupted(db_session, assessment, assume_dead=True) is False


def test_startup_sweep_with_single_process_lock_assumes_dead(db_session, assessment):
    _running(db_session, assessment, beat_age_s=1)  # fresh, but its process restarted
    assert recover_interrupted_runs(db_session) == 0
    assert recover_interrupted_runs(db_session, assume_dead=True) == 1
    assert assessment.status == PipelineStatus.failed


def test_recovery_releases_the_db_lock_so_a_rerun_can_start(db_session, assessment, monkeypatch):
    lock = DbLock(sessionmaker(bind=db_session.get_bind()))
    monkeypatch.setattr(pipeline_lock, "_get_lock", lambda: lock)
    assert lock.try_acquire(assessment.id)
    _running(db_session, assessment, beat_age_s=600)

    recover_if_interrupted(db_session, assessment)

    assert db_session.query(PipelineLockRow).count() == 0
    assert lock.try_acquire(assessment.id)  # the re-run is no longer blocked


def test_heartbeat_only_touches_in_flight_runs(db_session, assessment):
    factory = sessionmaker(bind=db_session.get_bind())
    _running(db_session, assessment, beat_age_s=600)
    beat(assessment.id, factory)
    db_session.refresh(assessment)
    assert (datetime.utcnow() - assessment.pipeline_heartbeat_at).total_seconds() < 5

    assessment.status = PipelineStatus.failed
    stamp = assessment.pipeline_heartbeat_at = datetime(2020, 1, 1)
    db_session.commit()
    beat(assessment.id, factory)  # a late beat must not revive a finished run
    db_session.refresh(assessment)
    assert assessment.pipeline_heartbeat_at == stamp and assessment.status == PipelineStatus.failed


def test_heartbeat_thread_beats_while_the_run_is_alive(tmp_path, monkeypatch):
    # The thread uses its own connection, so this needs a file DB, not :memory:.
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'hb.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    row = Assessment(name="hb", status=PipelineStatus.ingesting, pipeline_heartbeat_at=datetime(2020, 1, 1))
    db.add(row)
    db.commit()
    monkeypatch.setenv("PIPELINE_HEARTBEAT_SECONDS", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    with Heartbeat(row.id, factory):
        time.sleep(1.6)
    db.refresh(row)
    assert row.pipeline_heartbeat_at.year == datetime.utcnow().year
    db.close()
    engine.dispose()


# --------------------------------------------------------------------------- #
# Questions-tab answer cache
# --------------------------------------------------------------------------- #
@pytest.fixture
def build_calls(monkeypatch):
    calls: list[dict] = []
    real = aq.build_assessment_answers

    def counting(db, assessment_id, **kwargs):
        calls.append(kwargs)
        return real(db, assessment_id, **kwargs)

    monkeypatch.setattr(aq, "build_assessment_answers", counting)
    return calls


def _complete(db, assessment) -> None:
    assessment.status = PipelineStatus.completed
    assessment.pipeline_finished_at = datetime(2026, 9, 1, 12, 0, 0)
    db.commit()


def test_repeat_reads_compute_answers_once(db_session, assessment, build_calls):
    _complete(db_session, assessment)
    first = aq.cached_assessment_answers(db_session, assessment)
    for _ in range(5):  # page polls / reloads
        assert aq.cached_assessment_answers(db_session, assessment) == first
    assert len(build_calls) == 1


@pytest.mark.parametrize("change", ["review_decision", "ad_hoc_question", "new_run"])
def test_any_state_change_invalidates(db_session, assessment, build_calls, change):
    _complete(db_session, assessment)
    aq.cached_assessment_answers(db_session, assessment)
    if change == "review_decision":
        db_session.add(
            ReviewDecision(assessment_id=assessment.id, target_type="claim", target_key="k", action="accept")
        )
        db_session.commit()
    elif change == "ad_hoc_question":
        aq.persist_ad_hoc_question(db_session, assessment.id, "Which databases hold PII data?")
    else:
        assessment.pipeline_finished_at = datetime(2026, 9, 2, 9, 0, 0)
        db_session.commit()
    after = aq.cached_assessment_answers(db_session, assessment)
    assert len(build_calls) == 2
    if change == "ad_hoc_question":
        assert any(a["origin"] == "ad_hoc" for a in after["answers"])


def test_no_llm_retrieval_or_caching_while_a_run_is_in_flight(db_session, assessment, build_calls):
    _running(db_session, assessment, beat_age_s=5)
    aq.cached_assessment_answers(db_session, assessment, retriever=object())
    aq.cached_assessment_answers(db_session, assessment, retriever=object())
    assert len(build_calls) == 2  # cheap, and never cached: the claims are being rebuilt
    assert all(c["retriever"] is None for c in build_calls)
    assert all(not c["prose"]._completer.enabled for c in build_calls)
    assert assessment.answers_cache is None
