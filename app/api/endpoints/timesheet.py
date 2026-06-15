from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Dict
from datetime import datetime, date, time
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import get_async_session
from app.models.timesheet import Timesheet
from app.schemas.timesheet import TimesheetRead, TimesheetCreate, TimesheetEntryRead
from app.core.auth import current_active_user
from app.models.user import User

router = APIRouter()


def _parse_time_str(s: str):
    if not s:
        return None
    try:
        # Try ISO parse
        return datetime.fromisoformat(s)
    except Exception:
        try:
            # HH:MM
            h, m = s.split(":")
            today = datetime.utcnow().date()
            return datetime.combine(today, time(int(h), int(m)))
        except Exception:
            return None


def _compute_hours(start_s: str, end_s: str):
    start = _parse_time_str(start_s)
    end = _parse_time_str(end_s)
    if not start or not end:
        return 0.0
    delta = end - start
    return max(0.0, delta.total_seconds() / 3600.0)


@router.post("/create", response_model=TimesheetRead, status_code=status.HTTP_201_CREATED)
async def create_timesheet(
    payload: TimesheetCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    # compute hours per entry and total
    processed_entries = []
    total = 0.0
    for e in payload.entries:
        hours = _compute_hours(e.start or "", e.end or "")
        total += hours
        processed_entries.append({
            "date": e.date.isoformat(),
            "start": e.start,
            "end": e.end,
            "activity": e.activity,
            "viaticos": float(e.viaticos or 0.0),
            "hours": hours,
        })

    ts = Timesheet(
        user_id=user.id,
        user_name=getattr(user, "username", None),
        user_department=getattr(user, "department", None),
        period_start=payload.period_start,
        period_end=payload.period_end,
        entries=processed_entries,
        total_hours=total,
        viaticos=float(payload.viaticos or 0.0),
        status="submitted",
    )
    session.add(ts)
    await session.commit()
    await session.refresh(ts)
    return ts


@router.get("/", response_model=List[TimesheetRead])
async def list_timesheets(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    # Admins (level 1-2) can see all, others only their own
    q = select(Timesheet)
    if not hasattr(user, "level") or user.level > 2:
        q = select(Timesheet).where(Timesheet.user_id == user.id)
    res = await session.execute(q)
    return res.scalars().all()


@router.get("/{timesheet_id}", response_model=TimesheetRead)
async def get_timesheet(timesheet_id: str, session: AsyncSession = Depends(get_async_session), user: User = Depends(current_active_user)):
    ts = await session.get(Timesheet, timesheet_id)
    if not ts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Timesheet not found")
    if user.level > 2 and ts.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado")
    return ts


@router.get("/summary/month/{year}/{month}")
async def summary_month(year: int, month: int, session: AsyncSession = Depends(get_async_session), user: User = Depends(current_active_user)):
    # return total hours for the user (or all if admin) for the month
    start = date(year, month, 1)
    # compute end month
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    q = select(Timesheet).where(Timesheet.period_start >= start, Timesheet.period_start < end)
    if user.level > 2:
        q = q.where(Timesheet.user_id == user.id)
    res = await session.execute(q)
    items = res.scalars().all()
    total = sum((i.total_hours or 0.0) for i in items)
    return {"year": year, "month": month, "total_hours": total}


@router.get("/summary/year/{year}")
async def summary_year(year: int, session: AsyncSession = Depends(get_async_session), user: User = Depends(current_active_user)):
    # return total hours per month for the year
    data = {}
    for m in range(1, 13):
        start = date(year, m, 1)
        if m == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, m + 1, 1)
        q = select(Timesheet).where(Timesheet.period_start >= start, Timesheet.period_start < end)
        if user.level > 2:
            q = q.where(Timesheet.user_id == user.id)
        res = await session.execute(q)
        items = res.scalars().all()
        data[m] = sum((i.total_hours or 0.0) for i in items)
    return {"year": year, "monthly_hours": data}
