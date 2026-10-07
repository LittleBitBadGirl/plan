"""Единая логика счётчика переносов (postpones)."""
from datetime import date, timedelta
from typing import Optional

from app.models.task import Task

# Категории, для которых переносы считаются только по рабочим дням
WORK_CATEGORY_NAMES = {"Работа", "Документы", "СБТ", "СМ Б24", "Майоли", "АТОЛ", "Тендеры", "ЗМ", "АИЖ", "Команда", "Карьера", "Конференции", "PR (пиар)"}


def _workdays_between(start: date, end: date) -> int:
    """Количество рабочих дней (пн–пт) между датами, не включая end."""
    count = 0
    d = start
    while d < end:
        if d.weekday() < 5:  # пн=0, ..., пт=4
            count += 1
        d += timedelta(days=1)
    return count


def rollover_days(due_date: date, today: date, is_work_category: bool = False) -> int:
    """Сколько дней задача просрочена.
    
    Для рабочих категорий — только рабочие дни.
    Для остальных — календарные дни.
    """
    if due_date >= today:
        return 0
    if is_work_category:
        return _workdays_between(due_date, today)
    return (today - due_date).days


def apply_backlog_return(
    task: Task,
    planned_for: Optional[date],
    today: date,
    is_work_category: bool = False,
) -> None:
    """Вечерний возврат задачи из дня в бэклог (замена прежнему ролловеру).

    В новой механике перенос — это «взял на день и не доделал». Считаем по
    календарным дням с момента, когда задачу брали (для рабочих категорий —
    только по рабочим), поднимаем счётчик и снимаем с дня. Якорь `overdue_since`
    ставится один раз, поэтому «тянется с 10-го» не сбрасывается при каждом
    возврате.
    """
    anchor = planned_for or today
    if task.overdue_since is None:
        task.overdue_since = anchor

    days = rollover_days(anchor, today, is_work_category)
    if days < 1:
        days = 1
    task.postpones = (task.postpones or 0) + days
    task.planned_for = None

    if (task.postpones or 0) > 7:
        task.chronic_task = True


def apply_rollover(task: Task, today: date, is_work_category: bool = False) -> None:
    """Прежний перенос по due_date (легаси, в механике дня больше не участвует).

    Оставлен ради арифметики счётчика: на нём держатся тесты
    `test_overdue_counter.py`, которые проверяют расчёт дней просрочки.

    Заодно ставится якорь просрочки (`overdue_since`) — от него считается бейдж
    «сколько задача тянется». Якорь ставится один раз и не сдвигается, поэтому
    число честно растёт, даже если задача давно «уехала» на сегодня.
    """
    if task.due_date is None or task.due_date >= today:
        return

    if task.overdue_since is None:
        task.overdue_since = task.due_date

    days = rollover_days(task.due_date, today, is_work_category)
    task.postpones = (task.postpones or 0) + days
    task.due_date = today

    if (task.postpones or 0) > 7:
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
