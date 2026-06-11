"""Единая логика счётчика переносов (postpones)."""
from datetime import date
from typing import Optional

from app.models.task import Task


def rollover_days(due_date: date, today: date) -> int:
    """Сколько календарных дней задача просрочена (due_date строго раньше today)."""
    return max(0, (today - due_date).days)


def apply_rollover(task: Task, today: date) -> None:
    """Увеличить postpones на число просроченных дней и перенести due_date на today."""
    days = rollover_days(task.due_date, today)
    if days <= 0:
        return

    task.postpones = (task.postpones or 0) + days
    task.due_date = today

    if task.postpones > 7:
        task.chronic_task = True


def apply_manual_plan(task: Task, old_due: Optional[date], new_due: date) -> None:
    """Обновить postpones при ручном планировании (календарь / бэклог)."""
    today = date.today()

    if new_due == today:
        if old_due is None or old_due > today:
            task.postpones = 0
        elif old_due < today:
            task.postpones = (task.postpones or 0) + rollover_days(old_due, today)
        return

    if old_due == today and new_due > today:
        task.postpones = 0
