from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _rename_legacy_stage_column()
    _add_missing_columns()


def _rename_legacy_stage_column() -> None:
    inspector = inspect(engine)
    if "assessments" not in inspector.get_table_names():
        return
    names = {c["name"] for c in inspector.get_columns("assessments")}
    if "hve_stage" in names and "workflow_stage" not in names:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE assessments RENAME COLUMN hve_stage TO workflow_stage")
            )


def _add_missing_columns() -> None:
    """`create_all()` only creates missing *tables* — it never alters a table that
    already exists, so a DB file created before a model gained a new nullable column
    (e.g. `Document.doc_type_confidence`) is permanently missing it and every query
    against that table fails with "no such column". Add any such columns in place,
    preserving existing rows, so upgrading the code doesn't require deleting local data.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # brand-new table — create_all() already handled it
        existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns or column.primary_key:
                continue
            ddl_type = column.type.compile(dialect=engine.dialect)
            with engine.begin() as conn:
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}')
                )
