from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from sqlmodel import SQLModel, Field


class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str

    # Base rules (defaults for new members/projects)
    work_start: str = "08:00"   # "HH:MM"
    work_end: str = "17:00"
    work_days: str = "0,1,2,3,4"  # Mon=0..Sun=6


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str
    name: str
    role: str = "planner"  # admin|planner|employee|external


class CompanyMember(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    user_id: int = Field(index=True)
    role_in_company: str = "employee"  # admin|planner|employee|external


class Person(SQLModel, table=True):
    """Planning resource. In MVP this maps 1:1 to a User in a Company."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    user_id: int = Field(index=True)

    # Fixed times (can be overridden per person even though base rules exist)
    work_start: str = "08:00"
    work_end: str = "17:00"
    work_days: str = "0,1,2,3,4"


class UnitType(SQLModel, table=True):
    """Company-wide catalog of 'units' (product types) with time per unit."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    name: str
    default_minutes: int
    icon: str = "■"


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    name: str
    color: str = "#4C78A8"
    owner_user_id: int
    status: str = "active"  # active|closed

    # If > 0, project is in "budget" mode (no unit scope). Stored in minutes.
    budget_minutes: int = 0



class WorkItem(SQLModel, table=True):
    """Project scope line: quantity × minutes_per_unit => total."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    unit_type_id: int
    title: str
    quantity: int
    minutes_per_unit: int
    total_minutes: int
    deadline: Optional[datetime] = None


class Allocation(SQLModel, table=True):
    """High-level resourcing: person works X% on a project for a date range."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    project_id: int = Field(index=True)
    person_id: int = Field(index=True)
    start_date: date
    end_date: date
    percent: int  # 0..100


class AdhocAllocation(SQLModel, table=True):
    """Ad-hoc small task planned as % without creating a project."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    person_id: int = Field(index=True)
    start_date: date
    end_date: date
    percent: int  # 0..100
    title: str
    color: str = "#ff4fa3"  # pink



class UnitAllocation(SQLModel, table=True):
    """Project-level unit planning (time-based): assign minutes of a WorkItem to a person over a date range."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    work_item_id: int = Field(index=True)
    person_id: int = Field(index=True)
    start_date: date
    end_date: date
    minutes: int  # planned time (minutes) for this work item in this allocation



class ScheduleBlock(SQLModel, table=True):
    """(Optional) Detailed planning blocks. Kept for future expansion."""
    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(index=True)
    work_item_id: int = Field(index=True)
    start: datetime
    end: datetime
    locked: bool = False


class TimeOff(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(index=True)
    start: datetime
    end: datetime
    kind: str = "leave"  # leave|sick|other
    note: str = ""


class ProjectComment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    author_user_id: Optional[int] = Field(default=None, index=True)
    author_external_email: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    body: str


class ProjectShare(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    token: str = Field(index=True)
    email: str
    permission: str = "read"  # read|comment

    from_dt: datetime
    to_dt: datetime

    # Optional scope for MVP (empty => entire project)
    work_item_ids_csv: str = ""
