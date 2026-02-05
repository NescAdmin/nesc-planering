from __future__ import annotations

import os

from sqlmodel import SQLModel, create_engine, Session


def _make_engine():
    """Create a database engine.

    - In production (Render), use DATABASE_URL (Postgres).
    - Locally, fall back to SQLite file (PLANNER_DB or planner.db).
    """
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        # Render often provides postgres://, SQLAlchemy prefers postgresql://
        if db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]
        return create_engine(db_url, echo=False, pool_pre_ping=True)

    # NOTE: Local prototype DB.
    # For upgrades between ZIP versions, it's easiest to delete planner.db and re-run seed.
    db_path = os.environ.get("PLANNER_DB", "planner.db")
    return create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )


engine = _make_engine()

def init_db() -> None:
    SQLModel.metadata.create_all(engine)

def get_session() -> Session:
    return Session(engine)
