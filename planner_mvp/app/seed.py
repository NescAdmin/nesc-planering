from __future__ import annotations

from datetime import datetime, timedelta, date

from sqlmodel import select

from .db import init_db, get_session
from .models import (
    Company, User, CompanyMember, Person, UnitType, Project, WorkItem,
    Allocation, TimeOff, ProjectComment, ProjectShare
)

def run_seed():
    init_db()
    with get_session() as session:
        # If we already have users + company, skip to avoid clobbering local data.
        if session.exec(select(Company)).first():
            print("Seed skipped: company already exists.")
            return

        # Users (demo)
        christian = User(email="christian@example.com", name="Christian", role="admin")
        hakan = User(email="hakan@example.com", name="Håkan", role="planner")
        tai = User(email="tai@example.com", name="Tai", role="employee")
        mattias = User(email="mattias@example.com", name="Mattias", role="employee")
        hampus = User(email="hampus@example.com", name="Hampus", role="employee")

        session.add_all([christian, hakan, tai, mattias, hampus])
        session.commit()
        for u in [christian, hakan, tai, mattias, hampus]:
            session.refresh(u)

        # Company
        c = Company(name="NESC AB", work_start="08:00", work_end="17:00", work_days="0,1,2,3,4")
        session.add(c); session.commit(); session.refresh(c)

        # Members + People resources
        def add_member(u: User):
            role_in_company = "admin" if u.role == "admin" else ("planner" if u.role == "planner" else "employee")
            session.add(CompanyMember(company_id=c.id, user_id=u.id, role_in_company=role_in_company))
            if u.role != "external":
                session.add(Person(company_id=c.id, user_id=u.id, work_start=c.work_start, work_end=c.work_end, work_days=c.work_days))

        add_member(christian)
        add_member(hakan)
        add_member(tai)
        add_member(mattias)
        add_member(hampus)
        session.commit()

        # People lookups
        people = session.exec(select(Person).where(Person.company_id == c.id)).all()
        person_by_user = {p.user_id: p for p in people}
        p_christian = person_by_user[christian.id]
        p_tai = person_by_user[tai.id]
        p_mattias = person_by_user[mattias.id]
        p_hampus = person_by_user[hampus.id]

        # Unit catalog (company-wide)
        ut_hdf = UnitType(company_id=c.id, name="HD/F", default_minutes=30, icon="🔧")
        ut_wall = UnitType(company_id=c.id, name="Vägg", default_minutes=60, icon="🧱")
        ut_stt = UnitType(company_id=c.id, name="STT", default_minutes=60, icon="🧩")
        ut_random = UnitType(company_id=c.id, name="Random", default_minutes=60, icon="🎯")
        session.add_all([ut_hdf, ut_wall, ut_stt, ut_random])
        session.commit()
        for ut in [ut_hdf, ut_wall, ut_stt, ut_random]:
            session.refresh(ut)

        # Projects
        porsche = Project(company_id=c.id, name="Porsche", color="#4C78A8", owner_user_id=hakan.id, status="active")
        killingen = Project(company_id=c.id, name="Killingen", color="#F58518", owner_user_id=hakan.id, status="active")
        kltk = Project(company_id=c.id, name="KLTK", color="#54A24B", owner_user_id=hakan.id, status="active")
        session.add_all([porsche, killingen, kltk]); session.commit()
        for pr in [porsche, killingen, kltk]:
            session.refresh(pr)

        # Scope lines (using company unit defaults)
        def add_scope(project: Project, ut: UnitType, qty: int):
            session.add(WorkItem(
                project_id=project.id,
                unit_type_id=ut.id,
                title=ut.name,
                quantity=qty,
                minutes_per_unit=ut.default_minutes,
                total_minutes=qty * ut.default_minutes,
            ))

        add_scope(porsche, ut_wall, 40)     # 40 väggar
        add_scope(porsche, ut_hdf, 120)     # 120 HD/F
        add_scope(killingen, ut_stt, 80)    # 80 STT
        add_scope(killingen, ut_wall, 15)
        add_scope(kltk, ut_random, 100)
        session.commit()

        # Some time off example (Hampus off one day next week)
        next_mon = (datetime.now().date() + timedelta(days=(7 - datetime.now().date().weekday())) )  # next Monday
        session.add(TimeOff(
            person_id=p_hampus.id,
            start=datetime.combine(next_mon + timedelta(days=2), datetime.min.time()) + timedelta(hours=8),
            end=datetime.combine(next_mon + timedelta(days=2), datetime.min.time()) + timedelta(hours=17),
            kind="leave",
            note="Ledig (exempel)"
        ))
        session.commit()

        # High-level allocations (portfolio)
        ws = datetime.now().date() - timedelta(days=datetime.now().date().weekday())
        # Christian 50/50 on Killingen and KLTK for 4 weeks
        session.add_all([
            Allocation(company_id=c.id, project_id=killingen.id, person_id=p_christian.id, start_date=ws, end_date=ws + timedelta(days=27), percent=50),
            Allocation(company_id=c.id, project_id=kltk.id, person_id=p_christian.id, start_date=ws, end_date=ws + timedelta(days=27), percent=50),
        ])
        # Tai + Mattias + Hampus on Porsche
        session.add_all([
            Allocation(company_id=c.id, project_id=porsche.id, person_id=p_tai.id, start_date=ws, end_date=ws + timedelta(days=13), percent=80),
            Allocation(company_id=c.id, project_id=porsche.id, person_id=p_mattias.id, start_date=ws, end_date=ws + timedelta(days=13), percent=60),
            Allocation(company_id=c.id, project_id=porsche.id, person_id=p_hampus.id, start_date=ws, end_date=ws + timedelta(days=6), percent=40),
        ])
        session.commit()

        # Comments
        session.add(ProjectComment(project_id=porsche.id, author_user_id=hakan.id, body="Kickoff planeras i vecka 6."))
        session.commit()

        print("Seed completed.")
        print(f"Company: {c.name} (id={c.id})")
        print(f"Users: Christian={christian.id}, Håkan={hakan.id}, Tai={tai.id}, Mattias={mattias.id}, Hampus={hampus.id}")
        print("Tip: Add ?as_user=<id> to URLs to simulate different users.")
        
if __name__ == "__main__":
    run_seed()
