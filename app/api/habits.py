from fastapi import APIRouter, Depends, HTTPException, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_, update
from app.db.database import async_session
from app.models.habit import Habit
from app.models.habit_log import HabitLog
from datetime import date
from pydantic import BaseModel
from typing import List, Optional
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/api/habits", tags=["habits"])

class HabitToggle(BaseModel):
    habit_id: int
    date: date

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

@router.post("/{habit_id}/next-cycle")
async def restart_habit_cycle(habit_id: int):
    """Завершить текущий цикл и начать новый (30 дней)"""
    async with async_session() as db:
        habit_res = await db.execute(select(Habit).where(Habit.id == habit_id))
        habit = habit_res.scalar_one_or_none()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")
        
        # Переключаем цикл и сбрасываем дату старта на сегодня
        habit.current_cycle += 1
        habit.start_date = date.today()
        
        await db.commit()
    return RedirectResponse(url="/", status_code=303)
