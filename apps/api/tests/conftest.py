from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Force test settings before app imports touch get_settings cache
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("STORAGE_DIR", str(Path("/tmp/cmaa-test-uploads")))
os.environ.setdefault("MOCK_LLM", "true")
os.environ.setdefault("ENFORCE_REVIEW", "true")

from app.config import get_settings
from app.database import Base
from app.models.entities import Assessment, PipelineStatus
from app.services.llm_clients import clear_llm_client_cache


@pytest.fixture(autouse=True)
def _clear_settings_and_llm_caches():
    get_settings.cache_clear()
    clear_llm_client_cache()
    yield
    get_settings.cache_clear()
    clear_llm_client_cache()


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("EMBEDDINGS_DIR", str(tmp_path / "embeddings"))
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()

    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        get_settings.cache_clear()


@pytest.fixture()
def assessment(db_session):
    row = Assessment(
        name="Test Assessment",
        status=PipelineStatus.pending,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row
