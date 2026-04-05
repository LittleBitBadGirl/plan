from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from app.config import settings


async def save_feedback(db: AsyncSession, feedback_data):
    """Сохранить обратную связь в лог"""
    feedback_file = settings.config_dir / "feedback_log.md"
    
    entry = f"""
## {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Задача ID:** {feedback_data.task_id}
**Было:** Категория ID {feedback_data.old_category_id}
**Стало:** Категория ID {feedback_data.new_category_id}
**Причина:** {feedback_data.reason}

"""
    
    with open(feedback_file, "a", encoding="utf-8") as f:
        f.write(entry)
    
    # Обновить задачу в БД
    from sqlalchemy import select
    from app.models.task import Task
    
    result = await db.execute(select(Task).where(Task.id == feedback_data.task_id))
    task = result.scalar_one_or_none()
    if task:
        task.category_id = feedback_data.new_category_id
        await db.flush()
