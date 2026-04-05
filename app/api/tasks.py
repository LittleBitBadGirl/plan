from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel

from app.api.dependencies import get_db_session, verify_token
from app.models.task import Task
from app.models.category import Category

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(verify_token)])


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    category_id: Optional[int] = None
    priority: str = "средний"
    due_date: Optional[date] = None
    source: str = "web"


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[date] = None


@router.get("")
async def list_tasks(
    db: AsyncSession = Depends(get_db_session),
    status: Optional[str] = None,
    category_id: Optional[int] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    """Получить список задач с фильтрацией"""
    query = select(Task).where(Task.is_archived == False)

    if status:
        query = query.where(Task.status == status)
    if category_id:
        query = query.where(Task.category_id == category_id)
    if from_date:
        query = query.where(Task.due_date >= from_date)
    if to_date:
        query = query.where(Task.due_date <= to_date)

    query = query.order_by(Task.due_date.asc(), Task.sort_order.asc())
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("")
async def create_task(
    task_data: TaskCreate,
    db: AsyncSession = Depends(get_db_session),
):
    """Создать задачу"""
    task = Task(
        title=task_data.title,
        description=task_data.description,
        category_id=task_data.category_id,
        priority=task_data.priority,
        due_date=task_data.due_date,
        source=task_data.source,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


@router.get("/{task_id}")
async def get_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Получить задачу по ID"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}")
async def update_task(
    task_id: int,
    task_data: TaskUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    """Обновить задачу"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    update_data = task_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(task, key, value)

    await db.flush()
    await db.refresh(task)
    return task


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Soft delete задачи (архивирование)"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.is_archived = True
    await db.flush()
    return {"message": "Task archived"}


@router.post("/{task_id}/complete")
async def complete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Отметить задачу выполненной"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = "выполнена"
    task.completed_at = datetime.utcnow()
    await db.flush()
    return task


@router.post("/{task_id}/subtasks")
async def add_subtask(
    task_id: int,
    task_data: TaskCreate,
    db: AsyncSession = Depends(get_db_session),
):
    """Добавить подзадачу"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="Task not found")

    subtask = Task(
        title=task_data.title,
        description=task_data.description,
        category_id=task_data.category_id or parent.category_id,
        parent_task_id=task_id,
        source=task_data.source,
    )
    db.add(subtask)
    await db.flush()
    await db.refresh(subtask)
    return subtask


@router.post("/{task_id}/archive")
async def archive_task(
    task_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Архивировать задачу"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.is_archived = True
    await db.flush()
    return {"message": "Task archived"}


@router.get("/archive")
async def get_archive(
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    """Получить архив задач"""
    query = (
        select(Task)
        .where(Task.is_archived == True)
        .order_by(Task.completed_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/date/{task_date}")
async def get_tasks_by_date(
    task_date: date,
    db: AsyncSession = Depends(get_db_session),
):
    """Получить задачи на конкретную дату"""
    query = (
        select(Task)
        .where(Task.due_date == task_date, Task.is_archived == False)
        .order_by(Task.due_date.asc(), Task.sort_order.asc())
    )
    result = await db.execute(query)
    return result.scalars().all()
