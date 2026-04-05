from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta

from app.api.dependencies import get_db_session
from app.services.ai_service import ai_service
from app.models.task import Task

router = APIRouter(prefix="/api/ai", tags=["ai"])


class CategorizeRequest(BaseModel):
    text: str


class FeedbackRequest(BaseModel):
    task_id: int
    old_category_id: int
    new_category_id: int
    reason: str


@router.post("/categorize")
async def categorize_task(data: CategorizeRequest):
    """AI категоризация текста"""
    result = await ai_service.categorize(data.text)
    return result


@router.post("/feedback")
async def submit_feedback(
    data: FeedbackRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Обратная связь на ошибку категоризации"""
    # Получить задачу
    result = await db.execute(select(Task).where(Task.id == data.task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Сохранить feedback
    ai_service._save_feedback(
        task_text=task.title,
        old_cat=str(data.old_category_id),
        new_cat=str(data.new_category_id),
        reason=data.reason,
    )
    
    # Обновить категорию задачи
    task.category_id = data.new_category_id
    await db.flush()
    
    return {"message": "Спасибо! Система запомнит это."}


@router.get("/load-analysis")
async def load_analysis(db: AsyncSession = Depends(get_db_session)):
    """AI анализ нагрузки на неделю"""
    # Посчитать задачи за последние 7 дней
    week_ago = datetime.utcnow() - timedelta(days=7)
    
    result = await db.execute(
        select(
            func.count(Task.id).filter(Task.status == "выполнена").label("completed"),
            func.count(Task.id).label("total"),
        ).where(Task.created_at >= week_ago)
    )
    row = result.first()
    
    completed = row.completed or 0
    total = row.total or 0
    avg_per_day = completed / 7 if completed else 0
    
    warning = None
    if avg_per_day > 0:
        warning = f"⚠️ Обычно вы выполняете {avg_per_day:.1f} задач/день"
    
    return {
        "warning": warning,
        "stats": {
            "week_completed": completed,
            "week_total": total,
            "avg_per_day": round(avg_per_day, 1),
        }
    }


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db_session)):
    """Статистика выполненных задач"""
    result = await db.execute(
        select(
            func.count(Task.id).filter(Task.status == "выполнена").label("completed"),
            func.count(Task.id).filter(Task.is_archived == False).label("active"),
            func.count(Task.id).filter(Task.chronic_task == True).label("chronic"),
        )
    )
    row = result.first()
    
    return {
        "completed": row.completed or 0,
        "active": row.active or 0,
        "chronic": row.chronic or 0,
    }
