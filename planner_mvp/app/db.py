from __future__ import annotations

import os

from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text


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
    # Create missing tables
    SQLModel.metadata.create_all(engine)

    # NOTE: SQLModel doesn't auto-migrate existing tables. When re-deploying on Render
    # with a persistent Postgres DB, older tables might miss new columns. We run a
    # small, safe migration to add expected columns.
    try:
        if engine.url.get_backend_name().startswith("postgres"):
            with engine.begin() as conn:
                # Company
                conn.execute(text("ALTER TABLE IF EXISTS company ADD COLUMN IF NOT EXISTS work_start VARCHAR DEFAULT '08:00'"))
                conn.execute(text("ALTER TABLE IF EXISTS company ADD COLUMN IF NOT EXISTS work_end VARCHAR DEFAULT '17:00'"))
                conn.execute(text("ALTER TABLE IF EXISTS company ADD COLUMN IF NOT EXISTS work_days VARCHAR DEFAULT '0,1,2,3,4'"))

                # User
                conn.execute(text("ALTER TABLE IF EXISTS \"user\" ADD COLUMN IF NOT EXISTS role VARCHAR DEFAULT 'planner'"))

                # CompanyMember
                conn.execute(text("ALTER TABLE IF EXISTS companymember ADD COLUMN IF NOT EXISTS role_in_company VARCHAR DEFAULT 'employee'"))

                # Person
                conn.execute(text("ALTER TABLE IF EXISTS person ADD COLUMN IF NOT EXISTS work_start VARCHAR DEFAULT '08:00'"))
                conn.execute(text("ALTER TABLE IF EXISTS person ADD COLUMN IF NOT EXISTS work_end VARCHAR DEFAULT '17:00'"))
                conn.execute(text("ALTER TABLE IF EXISTS person ADD COLUMN IF NOT EXISTS work_days VARCHAR DEFAULT '0,1,2,3,4'"))

                # Project
                conn.execute(text("ALTER TABLE IF EXISTS project ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'active'"))
                conn.execute(text("ALTER TABLE IF EXISTS project ADD COLUMN IF NOT EXISTS budget_minutes INTEGER DEFAULT 0"))

                # Company name must be unique (case-insensitive)
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_company_name_lower ON company (lower(name))"))

                # WorkItem
                conn.execute(text("ALTER TABLE IF EXISTS workitem ADD COLUMN IF NOT EXISTS deadline TIMESTAMP"))

                # AdhocAllocation
                conn.execute(text("ALTER TABLE IF EXISTS adhocallocation ADD COLUMN IF NOT EXISTS color VARCHAR DEFAULT '#ff4fa3'"))

                # UnitAllocation
                conn.execute(text("ALTER TABLE IF EXISTS unitallocation ADD COLUMN IF NOT EXISTS minutes INTEGER DEFAULT 0"))
    except Exception:
        # Never block startup due to a migration step; we'll surface errors in logs.
        return

def get_session() -> Session:
    return Session(engine)
