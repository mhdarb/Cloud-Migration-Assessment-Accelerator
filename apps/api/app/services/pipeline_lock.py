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


def try_acquire(assessment_id: str) -> bool:
    return _get_lock().try_acquire(assessment_id)


def release(assessment_id: str) -> None:
    _get_lock().release(assessment_id)
