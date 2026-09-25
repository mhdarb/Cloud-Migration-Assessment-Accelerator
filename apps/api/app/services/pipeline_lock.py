from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import Assessment, PipelineLockRow, PipelineStatus

logger = logging.getLogger(__name__)

IN_FLIGHT = frozenset(
    {
        PipelineStatus.ingesting,
        PipelineStatus.extracting,
        PipelineStatus.reconciling,
        PipelineStatus.building_graph,
        PipelineStatus.generating_report,
    }
)


@runtime_checkable
class Lock(Protocol):
    def try_acquire(self, assessment_id: str) -> bool: ...

    def release(self, assessment_id: str) -> None: ...


class InProcessLock:
    """Single-process lock. Not safe across multiple uvicorn workers — use `DbLock` for that."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._active: set[str] = set()

    def try_acquire(self, assessment_id: str) -> bool:
        with self._guard:
            if assessment_id in self._active:
                return False
            self._active.add(assessment_id)
            return True

    def release(self, assessment_id: str) -> None:
        with self._guard:
            self._active.discard(assessment_id)


class DbLock:
    """Multi-worker-safe lock backed by a `pipeline_locks` row per assessment.

    Acquire is a plain insert (fails on primary-key conflict if already locked);
    release deletes the row. Works identically on SQLite and Postgres.

    A hard-killed worker (OOM, forced redeploy) never reaches `release()`'s `finally`,
    which would otherwise leave the row — and the assessment stuck "in flight" — forever.
    `try_acquire` steals a lock whose `locked_at` is older than
    `settings.pipeline_lock_stale_seconds` before attempting its own insert, so a crash
    self-heals on the next run attempt instead of requiring manual DB cleanup.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self._worker_id = str(uuid.uuid4())[:8]

    def _steal_if_stale(self, db: Session, assessment_id: str) -> None:
        stale_seconds = get_settings().pipeline_lock_stale_seconds
        if stale_seconds <= 0:
            return
        row = (
            db.query(PipelineLockRow)
            .filter(PipelineLockRow.assessment_id == assessment_id)
            .one_or_none()
        )
        if row is None:
            return
        age = (datetime.utcnow() - row.locked_at).total_seconds()
        if age >= stale_seconds:
            logger.warning(
                "Stealing stale pipeline lock for %s (age=%.0fs, held by worker %s)",
                assessment_id,
                age,
                row.worker_id,
            )
            db.delete(row)
            db.commit()

    def try_acquire(self, assessment_id: str) -> bool:
        db = self._session_factory()
        try:
            self._steal_if_stale(db, assessment_id)
            db.add(PipelineLockRow(assessment_id=assessment_id, worker_id=self._worker_id))
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False
        finally:
            db.close()

    def release(self, assessment_id: str) -> None:
        db = self._session_factory()
        try:
            db.query(PipelineLockRow).filter(
                PipelineLockRow.assessment_id == assessment_id
            ).delete()
            db.commit()
        finally:
            db.close()


_memory_lock = InProcessLock()


def _get_lock() -> Lock:
    if get_settings().pipeline_lock_backend == "db":
        from app.database import SessionLocal

        return DbLock(SessionLocal)
    return _memory_lock


def is_in_flight(assessment: Assessment) -> bool:
    return assessment.status in IN_FLIGHT


# --------------------------------------------------------------------------- #
# Liveness: a run lives inside one API process (FastAPI BackgroundTasks). If that
# process dies — restart, redeploy, scale-in, OOM — nothing is left to set the status to
# `failed`, and every action that refuses to race a run (re-run, delete, review) would
# refuse forever. A live run writes a heartbeat; a stale one means the run is gone.
# --------------------------------------------------------------------------- #
class Heartbeat:
    """Background thread stamping `pipeline_heartbeat_at` while a run is alive."""

    def __init__(self, assessment_id: str, session_factory: Callable[[], Session]) -> None:
        self._assessment_id = assessment_id
        self._session_factory = session_factory
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"heartbeat-{assessment_id[:8]}", daemon=True
        )

    def __enter__(self) -> Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        interval = max(1.0, get_settings().pipeline_heartbeat_seconds)
        while not self._stop.wait(interval):
            beat(self._assessment_id, self._session_factory)


def beat(assessment_id: str, session_factory: Callable[[], Session]) -> None:
    db = session_factory()
    try:
        # Only while the run is still in flight: a heartbeat must never revive a run
        # that has finished, failed, or been declared interrupted.
        db.query(Assessment).filter(
            Assessment.id == assessment_id, Assessment.status.in_(IN_FLIGHT)
        ).update({Assessment.pipeline_heartbeat_at: datetime.utcnow()}, synchronize_session=False)
        db.commit()
    except Exception:  # a missed beat is harmless; the next one retries
        db.rollback()
        logger.debug("heartbeat failed for %s", assessment_id, exc_info=True)
    finally:
        db.close()


def _last_sign_of_life(assessment: Assessment) -> datetime | None:
    return assessment.pipeline_heartbeat_at or assessment.pipeline_started_at or assessment.updated_at


def recover_if_interrupted(db: Session, assessment: Assessment, *, assume_dead: bool = False) -> bool:
    """Mark an in-flight run whose process is gone as failed, and free its lock, so the
    assessment can be re-run, reviewed or deleted again. Returns True if it recovered one.

    `assume_dead` skips the staleness check: used at startup with the in-process lock,
    where a restarted process can't possibly still be running anything."""
    if not is_in_flight(assessment):
        return False
    last = _last_sign_of_life(assessment)
    if not assume_dead:
        stale_after = get_settings().pipeline_run_stale_seconds
        if stale_after <= 0 or last is None:
            return False
        if (datetime.utcnow() - last).total_seconds() < stale_after:
            return False

    from app.services.workflow import append_follow_up

    stage = assessment.status.value
    logger.warning("Pipeline run for %s was interrupted during %s; marking failed", assessment.id, stage)
    assessment.status = PipelineStatus.failed
    assessment.error_message = (
        f"The run was interrupted during {stage.replace('_', ' ')} — the server restarted or the "
        "worker stopped. Re-run the pipeline to continue; review decisions are kept."
    )
    # Freeze the timer at the last moment the run was known to be alive.
    assessment.pipeline_finished_at = last or datetime.utcnow()
    append_follow_up(assessment, event="run_interrupted", detail={"note": f"Interrupted during {stage}"})
    db.commit()
    release(assessment.id)
    return True


def recover_interrupted_runs(db: Session, *, assume_dead: bool = False) -> int:
    """Sweep every in-flight assessment (startup)."""
    rows = db.query(Assessment).filter(Assessment.status.in_(IN_FLIGHT)).all()
    return sum(recover_if_interrupted(db, a, assume_dead=assume_dead) for a in rows)


def try_acquire(assessment_id: str) -> bool:
    return _get_lock().try_acquire(assessment_id)


def release(assessment_id: str) -> None:
    _get_lock().release(assessment_id)
