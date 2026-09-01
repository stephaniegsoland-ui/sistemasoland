from fastapi import APIRouter, Body, Depends, HTTPException, status
from typing import List, Dict, Any
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


def _coerce_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except Exception:
            return date.today()
    return date.today()


@router.post("/create", response_model=TimesheetRead, status_code=status.HTTP_201_CREATED)
async def create_timesheet(
    payload: Dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    if isinstance(payload, dict) and "entries" in payload:
        raw_entries = payload.get("entries") or []
        period_start = _coerce_date(payload.get("period_start") or payload.get("date"))
        period_end = _coerce_date(payload.get("period_end") or payload.get("date") or period_start)
        viaticos = float(payload.get("viaticos") or 0.0)
    else:
        raw_entries = payload.get("activities") or []
        period_start = _coerce_date(payload.get("period_start") or payload.get("date"))
        period_end = _coerce_date(payload.get("period_end") or payload.get("date") or period_start)
        viaticos = float(payload.get("viaticos") or 0.0)

    processed_entries = []
    total = 0.0
    for e in raw_entries:
        if not isinstance(e, dict):
            continue
        start = e.get("start")
        end = e.get("end")
        activity = e.get("activity") or e.get("description") or ""
        hours = e.get("hours")
        if hours is None:
            hours = _compute_hours(start or "", end or "")
        hours = float(hours or 0.0)
        total += hours
        processed_entries.append({
            "date": e.get("date") or period_start.isoformat(),
            "start": start,
            "end": end,
            "activity": activity,
            "viaticos": float(e.get("viaticos") or 0.0),
            "viatico_type": e.get("viatico_type") or None,
            "hours": hours,
        })

    ts = Timesheet(
        user_id=user.id,
        user_name=getattr(user, "username", None),
        user_department=getattr(user, "department", None),
        period_start=period_start,
        period_end=period_end,
        entries=processed_entries,
        total_hours=total,
        viaticos=viaticos,
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


@router.get("/all")
async def list_all_timesheets(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    if not hasattr(user, "level") or user.level > 2:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado")

    result = await session.execute(select(Timesheet).order_by(Timesheet.created_at.desc()))
    timesheets = result.scalars().all()

    def normalize_entry(entry: Any) -> Dict[str, Any]:
        if not isinstance(entry, dict):
            return {
                "description": "",
                "start": None,
                "end": None,
                "viaticos": 0.0,
                "viatico_type": None,
                "hours": 0.0,
            }
        return {
            "description": entry.get("activity") or "",
            "start": entry.get("start"),
            "end": entry.get("end"),
            "viaticos": float(entry.get("viaticos") or 0.0),
            "viatico_type": entry.get("viatico_type") or None,
            "hours": float(entry.get("hours") or 0.0),
        }

    def normalize_date_from_entries(entries_list: Any, period_start_value: date) -> str:
        if isinstance(entries_list, list) and entries_list:
            first = entries_list[0]
            if isinstance(first, dict):
                date_value = first.get("date")
                if isinstance(date_value, str) and date_value:
                    return date_value
        return period_start_value.isoformat()

    return [
        {
            "id": str(ts.id),
            "user_id": str(ts.user_id),
            "username": ts.user_name or "Usuario",
            "period_start": ts.period_start.isoformat(),
            "period_end": ts.period_end.isoformat(),
            "date": normalize_date_from_entries(ts.entries, ts.period_start),
            "viaticos": float(ts.viaticos or 0.0),
            "activities": [normalize_entry(entry) for entry in ts.entries or []],
            "total_hours": float(ts.total_hours or 0.0),
            "notes": None,
            "created_at": ts.created_at.isoformat() if ts.created_at else None,
        }
        for ts in timesheets
    ]


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
