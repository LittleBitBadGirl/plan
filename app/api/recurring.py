from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, time
from typing import Optional, List
from pydantic import BaseModel, field_validator

from app.api.dependencies import get_db_session, verify_token
from app.models.recurring import RecurringTask

router = APIRouter(prefix="/api/recurring", tags=["recurring"], dependencies=[Depends(verify_token)])


class RecurringTaskResponse(BaseModel):
    """Pydantic схема для сериализации периодической задачи"""
    model_config = {"from_attributes": True}

    id: int
    title: str
    description: str
    category_id: Optional[int]
    priority: str
    recurrence_type: str
    recurrence_days: Optional[List[str]] = None
    recurrence_interval: int
    start_date: date
    end_date: Optional[date]
    time_of_day: Optional[str]
    is_active: bool
    completed_count: int
    created_at: str

    @field_validator("time_of_day", mode="before")
    @classmethod
    def parse_time(cls, v):
        if v is None:
            return None
        if isinstance(v, time):
            return v.isoformat()
        return str(v)

    @field_validator("recurrence_days", mode="before")
    @classmethod
    def parse_days(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        if v is None:
            return None
        return str(v)


class RecurringTaskCreate(BaseModel):
    title: str
    description: str = ""
    category_id: Optional[int] = None
    priority: str = "средний"
    recurrence_type: str  # daily, weekly, monthly
    recurrence_days: Optional[List[str]] = None
    start_date: date
    end_date: Optional[date] = None
    time_of_day: Optional[str] = None  # HH:MM format


class RecurringTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    priority: Optional[str] = None
    recurrence_type: Optional[str] = None
    recurrence_days: Optional[List[str]] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    time_of_day: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("", response_model=List[RecurringTaskResponse])
async def list_recurring(
    db: AsyncSession = Depends(get_db_session),
):
    """Получить все периодические задачи"""
    result = await db.execute(
        select(RecurringTask).order_by(RecurringTask.is_active.desc(), RecurringTask.title)
    )
    return result.scalars().all()


@router.post("", response_model=RecurringTaskResponse)
async def create_recurring(
    task_data: RecurringTaskCreate,
    db: AsyncSession = Depends(get_db_session),
):
    """Создать периодическую задачу"""
    # Проверка на дубль
    existing = await db.execute(
        select(RecurringTask).where(
            RecurringTask.title == task_data.title,
            RecurringTask.recurrence_type == task_data.recurrence_type,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Такой шаблон уже существует")

    task = RecurringTask(
        title=task_data.title,
        description=task_data.description,
        category_id=task_data.category_id,
        priority=task_data.priority,
        recurrence_type=task_data.recurrence_type,
        recurrence_days=task_data.recurrence_days,
        start_date=task_data.start_date,
        end_date=task_data.end_date,
        time_of_day=time.fromisoformat(task_data.time_of_day) if task_data.time_of_day else None,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


@router.put("/{recurring_id}")
async def update_recurring(
    recurring_id: int,
    task_data: RecurringTaskUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    """Обновить периодическую задачу"""
    result = await db.execute(select(RecurringTask).where(RecurringTask.id == recurring_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Recurring task not found")

    update_data = task_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key == "time_of_day" and value is not None:
            value = time.fromisoformat(value)
        setattr(task, key, value)

    await db.flush()
    await db.refresh(task)
    return task


@router.delete("/{recurring_id}")
async def delete_recurring(
    recurring_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Удалить периодическую задачу"""
    result = await db.execute(select(RecurringTask).where(RecurringTask.id == recurring_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Recurring task not found")

    await db.delete(task)
    await db.flush()
    return {"message": "Recurring task deleted"}


@router.post("/{recurring_id}/toggle")
async def toggle_recurring(
    recurring_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Переключить активность периодической задачи"""
    result = await db.execute(select(RecurringTask).where(RecurringTask.id == recurring_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Recurring task not found")

    task.is_active = not task.is_active
    await db.flush()
    return {"message": f"Toggled to {task.is_active}", "is_active": task.is_active}


@router.post("/{recurring_id}/complete")
async def complete_recurring(
    recurring_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Отметить периодическую задачу выполненной — создаёт выполненную задачу на сегодня"""
    from app.models.task import Task
    from datetime import datetime
    
    result = await db.execute(select(RecurringTask).where(RecurringTask.id == recurring_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Recurring task not found")

    # Увеличиваем счётчик выполнений
    task.completed_count += 1
    
    # Создаём обычную задачу на сегодня со статусом "выполнена"
    today = date.today()
    completed_task = Task(
        title=task.title,
        description=task.description,
        category_id=task.category_id,
        priority=task.priority,
        due_date=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        source="recurring",
    )
    db.add(completed_task)
    await db.flush()
    
    # Возвращаем пустой блок — задача исчезнет из списка
    from fastapi.responses import HTMLResponse
    return HTMLResponse(f'<div id="recurring-{task.id}" class="hidden"></div>')


@router.get("/for-date/{task_date}")
async def get_recurring_for_date(
    task_date: date,
    db: AsyncSession = Depends(get_db_session),
):
    """Получить периодические задачи активные на конкретную дату"""
    result = await db.execute(
        select(RecurringTask).where(RecurringTask.is_active == True)
    )
    all_recurring = result.scalars().all()

    matching = []
    day_of_week = task_date.weekday()  # 0=mon, 6=sun
    day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    for rt in all_recurring:
        if rt.end_date and task_date > rt.end_date:
            continue
        if task_date < rt.start_date:
            continue

        if rt.recurrence_type == "daily":
            matching.append(rt)
        elif rt.recurrence_type == "weekly":
            if rt.recurrence_days:
                days = rt.recurrence_days
                if isinstance(days, str):
                    import json
                    try:
                        days = json.loads(days)
                    except Exception:
                        days = []
                if day_names[day_of_week] in days:
                    matching.append(rt)
        elif rt.recurrence_type == "monthly":
            if task_date.day == rt.start_date.day:
                matching.append(rt)

    return matching
