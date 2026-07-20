from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Form, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_, update, tuple_
from app.db.database import async_session
from app.models.habit import Habit
from app.models.habit_log import HabitLog
from datetime import date, timedelta
from pydantic import BaseModel
from typing import List, Optional
from fastapi.responses import RedirectResponse, HTMLResponse

from app.web.deps import templates

router = APIRouter(prefix="/api/habits", tags=["habits"])

class HabitToggle(BaseModel):
    habit_id: int
    date: date


def compute_next_cycle_start(habit: Habit, today: date) -> date:
    """Дата старта следующего цикла: сразу после окна предыдущего (без разрыва).

    Если «След. 30 дней» нажали с опозданием — пропущенные дни попадают в начало
    новой сетки. Если нажали раньше конца цикла — старт с сегодня.
    """
    target_days = habit.target_days or 30
    old_start = habit.start_date or today
    scheduled = old_start + timedelta(days=target_days)
    if scheduled <= today:
        return scheduled
    return today


async def load_habit_logs_map(
    db: AsyncSession, habits: list[Habit]
) -> dict[int, set[str]]:
    """Batch-load current-cycle log dates for dashboard habits (one query)."""
    if not habits:
        return {}
    pairs = [(h.id, h.current_cycle) for h in habits]
    result = await db.execute(
        select(HabitLog.habit_id, HabitLog.date).where(
            tuple_(HabitLog.habit_id, HabitLog.cycle_number).in_(pairs)
        )
    )
    out: dict[int, set[str]] = defaultdict(set)
    for habit_id, log_date in result.all():
        out[habit_id].add(log_date.isoformat())
    return dict(out)


def build_habit_cycle_grid(habit: Habit, today: date) -> dict:
    """Сетка текущего цикла для дашборда и истории."""
    target_days = habit.target_days or 30
    start = habit.start_date or today
    dates = [start + timedelta(days=i) for i in range(target_days)]
    return {
        "start": start,
        "dates": dates,
        "start_weekday": start.weekday(),
        "target_days": target_days,
    }


def compute_cycle_start_dates(
    habit: Habit,
    logs_by_cycle: dict[int, list[date]],
) -> dict[int, date]:
    """Вычисляет дату старта каждого цикла, идя назад от текущего."""
    target_days = habit.target_days or 30
    starts: dict[int, date] = {}
    next_start = habit.start_date or date.today()
    starts[habit.current_cycle] = next_start

    for cycle_num in range(habit.current_cycle - 1, 0, -1):
        candidate = next_start - timedelta(days=target_days)
        marked = logs_by_cycle.get(cycle_num, [])

        if marked:
            mark_min = min(marked)
            window_end = candidate + timedelta(days=target_days - 1)
            if mark_min < candidate or max(marked) > window_end:
                starts[cycle_num] = mark_min
            else:
                starts[cycle_num] = candidate
        else:
            starts[cycle_num] = candidate

        next_start = starts[cycle_num]

    return starts


def build_habit_history_cycles(habit: Habit, logs: List[HabitLog], today: date) -> List[dict]:
    """Собирает историю отметок по циклам (новые сверху)."""
    by_cycle: dict[int, list[date]] = defaultdict(list)
    for log in logs:
        by_cycle[log.cycle_number].append(log.date)

    target_days = habit.target_days or 30
    cycle_starts = compute_cycle_start_dates(habit, by_cycle)
    cycles: List[dict] = []

    for cycle_num in range(habit.current_cycle, 0, -1):
        marked_dates = sorted(set(by_cycle.get(cycle_num, [])))
        is_current = cycle_num == habit.current_cycle
        marked_iso = {d.isoformat() for d in marked_dates}

        if is_current:
            grid = build_habit_cycle_grid(habit, today)
            dates = grid["dates"]
            start_weekday = grid["start_weekday"]
        else:
            start = cycle_starts[cycle_num]
            dates = [start + timedelta(days=i) for i in range(target_days)]
            start_weekday = start.weekday()

        cycles.append({
            "cycle_number": cycle_num,
            "is_current": is_current,
            "empty": not marked_dates,
            "dates": dates,
            "logs": marked_iso,
            "progress": len(marked_iso),
            "start_weekday": start_weekday,
            "target_days": target_days,
        })

    return cycles


@router.get("/")
async def get_habits():
    async with async_session() as db:
        result = await db.execute(select(Habit).where(Habit.is_active == True, Habit.is_archived == False))
        return result.scalars().all()

@router.post("/create")
async def create_habit(
    title: str = Form(...), 
    start_date: date = Form(...),
    category_id: int = Form(22)
):
    async with async_session() as db:
        new_habit = Habit(
            title=title, 
            start_date=start_date, 
            category_id=category_id,
            target_days=30
        )
        db.add(new_habit)
        await db.commit()
    return RedirectResponse(url="/", status_code=303)

@router.post("/toggle")
async def toggle_habit(data: HabitToggle):
    async with async_session() as db:
        # Получаем привычку, чтобы знать текущий цикл
        habit_res = await db.execute(select(Habit).where(Habit.id == data.habit_id))
        habit = habit_res.scalar_one_or_none()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")

        result = await db.execute(
            select(HabitLog).where(
                and_(
                    HabitLog.habit_id == data.habit_id,
                    HabitLog.date == data.date,
                    HabitLog.cycle_number == habit.current_cycle
                )
            )
        )
        existing_log = result.scalar_one_or_none()

        if existing_log:
            await db.delete(existing_log)
            action = "removed"
        else:
            new_log = HabitLog(
                habit_id=data.habit_id, 
                date=data.date,
                cycle_number=habit.current_cycle
            )
            db.add(new_log)
            action = "added"
        
        await db.commit()
        return {"status": "success", "action": action}

@router.post("/{habit_id}/archive")
async def archive_habit(habit_id: int):
    async with async_session() as db:
        await db.execute(
            update(Habit).where(Habit.id == habit_id).values(is_archived=True)
        )
        await db.commit()
    return RedirectResponse(url="/", status_code=303)

@router.get("/{habit_id}/history", response_class=HTMLResponse)
async def habit_history(habit_id: int, request: Request):
    """HTML-фрагмент: полная история отметок трекера в попапе."""
    today = date.today()
    async with async_session() as db:
        habit_res = await db.execute(select(Habit).where(Habit.id == habit_id))
        habit = habit_res.scalar_one_or_none()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")

        logs_result = await db.execute(
            select(HabitLog)
            .where(HabitLog.habit_id == habit_id)
            .order_by(HabitLog.cycle_number, HabitLog.date)
        )
        logs = list(logs_result.scalars().all())
        cycles = build_habit_history_cycles(habit, logs, today)

    return templates.TemplateResponse(request, "partials/habit_history_modal.html", {
        "request": request,
        "habit": habit,
        "cycles": cycles,
        "total_marks": len(logs),
        "today": today,
    })


@router.post("/{habit_id}/next-cycle")
async def restart_habit_cycle(habit_id: int):
    """Завершить текущий цикл и начать новый (30 дней без разрыва календаря)."""
    today = date.today()
    async with async_session() as db:
        habit_res = await db.execute(select(Habit).where(Habit.id == habit_id))
        habit = habit_res.scalar_one_or_none()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")

        habit.current_cycle += 1
        habit.start_date = compute_next_cycle_start(habit, today)

        await db.commit()
    return RedirectResponse(url="/", status_code=303)
