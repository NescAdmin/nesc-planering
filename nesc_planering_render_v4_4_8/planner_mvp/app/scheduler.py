from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import List, Tuple

from sqlmodel import Session, select
from .models import Person, ScheduleBlock, TimeOff, WorkItem

WEEK_SNAP_MIN = 60

def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))

def _ceil_to_snap(dt: datetime, snap_minutes: int) -> datetime:
    # ceil to next snap boundary
    seconds = dt.minute * 60 + dt.second + dt.microsecond/1e6
    # easier using minutes
    total_minutes = dt.hour * 60 + dt.minute
    remainder = total_minutes % snap_minutes
    if remainder == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt.replace(second=0, microsecond=0)
    delta = snap_minutes - remainder
    new_dt = dt.replace(second=0, microsecond=0) + timedelta(minutes=delta)
    return new_dt

def _is_workday(person: Person, dt: datetime) -> bool:
    wd = dt.weekday()
    allowed = {int(x) for x in person.work_days.split(",") if x.strip() != ""}
    return wd in allowed

def _day_bounds(person: Person, day: datetime) -> Tuple[datetime, datetime]:
    start_t = _parse_hhmm(person.work_start)
    end_t = _parse_hhmm(person.work_end)
    day0 = day.replace(hour=0, minute=0, second=0, microsecond=0)
    start = day0.replace(hour=start_t.hour, minute=start_t.minute)
    end = day0.replace(hour=end_t.hour, minute=end_t.minute)
    return start, end

def _subtract_intervals(base: Tuple[datetime, datetime], blocks: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    # base interval minus blocks (blocks may overlap)
    start, end = base
    if start >= end:
        return []
    blocks = sorted([(max(start, a), min(end, b)) for a, b in blocks if b > start and a < end], key=lambda x: x[0])
    free = []
    cur = start
    for a, b in blocks:
        if a > cur:
            free.append((cur, a))
        cur = max(cur, b)
    if cur < end:
        free.append((cur, end))
    return free

@dataclass
class AutoScheduleResult:
    created_block_ids: List[int]
    remaining_minutes: int

def auto_schedule_next_available_same_person(
    session: Session,
    work_item_id: int,
    person_id: int,
    from_dt: datetime,
    horizon_weeks: int = 12,
) -> AutoScheduleResult:
    person = session.get(Person, person_id)
    wi = session.get(WorkItem, work_item_id)
    assert person and wi

    # remaining minutes
    existing = session.exec(select(ScheduleBlock).where(ScheduleBlock.work_item_id == work_item_id, ScheduleBlock.person_id == person_id)).all()
    scheduled = sum(int((b.end - b.start).total_seconds() // 60) for b in existing)
    remaining = max(0, wi.total_minutes - scheduled)

    cursor = _ceil_to_snap(from_dt, WEEK_SNAP_MIN)
    end_horizon = cursor + timedelta(weeks=horizon_weeks)

    created_ids: List[int] = []

    while remaining > 0 and cursor < end_horizon:
        # move to next workday if needed
        if not _is_workday(person, cursor):
            cursor = (cursor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).replace(hour=8, minute=0)
            continue

        day_start, day_end = _day_bounds(person, cursor)
        # if cursor before day_start, jump to day_start; if after day_end, next day
        if cursor < day_start:
            cursor = day_start
        if cursor >= day_end:
            cursor = (day_start + timedelta(days=1)).replace(hour=day_start.hour, minute=day_start.minute)
            continue

        # gather busy intervals for that day: other schedule blocks + timeoff
        day0 = day_start
        day1 = day_end

        busy = []
        blocks = session.exec(select(ScheduleBlock).where(ScheduleBlock.person_id == person_id, ScheduleBlock.end > day0, ScheduleBlock.start < day1)).all()
        for b in blocks:
            if b.locked is False or b.locked is True:
                busy.append((b.start, b.end))
        offs = session.exec(select(TimeOff).where(TimeOff.person_id == person_id, TimeOff.end > day0, TimeOff.start < day1)).all()
        for o in offs:
            busy.append((o.start, o.end))

        # free intervals
        free = _subtract_intervals((day0, day1), busy)

        # find first free interval starting at/after cursor
        placed = False
        for a, b in free:
            start = max(a, cursor)
            start = _ceil_to_snap(start, WEEK_SNAP_MIN)
            if start >= b:
                continue
            # length available in minutes snapped down to 60-min multiples
            avail_min = int((b - start).total_seconds() // 60)
            avail_min = (avail_min // WEEK_SNAP_MIN) * WEEK_SNAP_MIN
            if avail_min <= 0:
                continue

            block_min = min(remaining, avail_min)
            block_min = (block_min // WEEK_SNAP_MIN) * WEEK_SNAP_MIN
            if block_min <= 0:
                continue

            new_block = ScheduleBlock(
                person_id=person_id,
                work_item_id=work_item_id,
                start=start,
                end=start + timedelta(minutes=block_min),
                locked=False,
            )
            session.add(new_block)
            session.commit()
            session.refresh(new_block)
            created_ids.append(new_block.id)
            remaining -= block_min
            cursor = new_block.end
            placed = True
            break

        if not placed:
            # no free slot today; go next day
            cursor = (day_start + timedelta(days=1)).replace(hour=day_start.hour, minute=day_start.minute)

    return AutoScheduleResult(created_block_ids=created_ids, remaining_minutes=remaining)
