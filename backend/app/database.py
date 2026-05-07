"""SQLAlchemy database setup."""
import os
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.config import settings

# For SQLite paths like sqlite:////app/data/workbench.db, ensure the parent dir exists.
if settings.DATABASE_URL.startswith("sqlite"):
    parsed = urlparse(settings.DATABASE_URL)
    db_path = parsed.path  # e.g. "/app/data/workbench.db" or "./workbench.db"
    if db_path and db_path != "/:memory:":
        # SQLAlchemy treats sqlite:///./x.db as relative; sqlite:////x.db as absolute.
        parent = os.path.dirname(db_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, echo=False, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def get_db() -> Session:
    """FastAPI dependency yielding a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # Import all models so they register on Base.metadata
    from app.models import (  # noqa: F401
        user, dataset, fbdi, project, conversion, environment, mapping,
        transformation, validation as validation_model, output, load,
        workflow, dependency, learned,
    )
    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()


def _apply_lightweight_migrations() -> None:
    """Best-effort additive ALTER TABLEs for columns added after a release.

    SQLAlchemy ``create_all`` only adds new tables — it doesn't ALTER existing
    ones. For the few cases where we add a nullable column to a table that
    already exists in a dev database, we apply the change here. SQLite happily
    accepts adding nullable columns and ignores duplicates with try/except.
    """
    from sqlalchemy import text
    additions: list[tuple[str, str, str]] = [
        # (table, column, type) — keep declaration in sync with the ORM model
        ("load_errors", "reference_value", "VARCHAR(255)"),
    ]
    with engine.begin() as conn:
        for table, column, coltype in additions:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
            except Exception:
                # Column already exists, or table doesn't yet — both are safe to skip.
                pass
