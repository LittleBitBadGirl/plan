import pytest
from datetime import date, timedelta
from app.models.task import Task
from app.services.rollover_service import rollover_overdue_tasks


@pytest.mark.asyncio
async def test_rollover_overdue_tasks(db):
    """Тест переноса просроченных задач"""
    yesterday = date.today() - timedelta(days=1)

    task = Task(
        title="Просроченная задача",
        status="новая",
        due_date=yesterday,
    )
    db.add(task)
    await db.flush()

    result = await rollover_overdue_tasks(db)

    assert result["moved"] == 1
    assert task.due_date == date.today()
    assert task.postpones == 1
