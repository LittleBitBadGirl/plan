"""Логика задач для Telegram-бота — синхронизирована с веб-дашбордом."""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.task import Task
from app.services.ai_service import ai_service
from app.web.deps import dashboard_task_order_by, get_today_stats

# Маркеры бэклога: «бэклог: задача», «потом — задача»
_BACKLOG_PREFIX = re.compile(
    r"^\s*(?:бэклог|backlog|потом|later)\s*[:.\-–—]\s*",
    re.IGNORECASE,
)

_TIME_PREFIX = re.compile(r"^(\d{1,2}:\d{2})\s+(.*)$")


def detect_intent(text: str, *, done_suffix: re.Pattern, done_prefix: re.Pattern, bullet: re.Pattern, numbered: re.Pattern) -> dict:
    """Определяет намерение пользователя (complete / bulk_add / backlog_add / add)."""
    text = text.strip()

    m = done_suffix.search(text)
    if m:
        task_name = text[: m.start()].strip().rstrip("-–—").strip()
        return {"intent": "complete", "task_name": task_name}

    m = done_prefix.match(text)
    if m:
        task_name = text[m.end() :].strip()
        return {"intent": "complete", "task_name": task_name}

    backlog_m = _BACKLOG_PREFIX.match(text)
    if backlog_m:
        task_name = text[backlog_m.end() :].strip()
        if task_name:
            return {"intent": "backlog_add", "task_name": task_name}

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        clean = [bullet.sub("", numbered.sub("", line)).strip() for line in lines]
        clean = [item for item in clean if item]
        if len(clean) > 1:
            return {"intent": "bulk_add", "tasks": clean, "target": "backlog"}

    if text.count(",") >= 2:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) >= 2:
            return {"intent": "bulk_add", "tasks": parts, "target": "backlog"}

    return {"intent": "add", "task_name": text, "target": "today"}


def parse_time_from_title(title: str) -> tuple[str, time | None]:
    """«12:00 Задача» → (Задача, 12:00) — как на дашборде."""
    m = _TIME_PREFIX.match(title.strip())
    if not m:
        return title.strip(), None
    time_str, clean = m.group(1), m.group(2).strip()
    try:
        if len(time_str.split(":")[0]) == 1:
            time_str = "0" + time_str
        return clean or title.strip(), time.fromisoformat(time_str)
    except ValueError:
        return title.strip(), None


def today_open_tasks_filter(today: date | None = None) -> list:
    """Активные корневые задачи на сегодня (как дашборд)."""
    today = today or date.today()
    return [
        Task.due_date == today,
        Task.is_archived == False,
        Task.status.in_(["новая", "в_работе"]),
        Task.parent_task_id == None,
        Task.source.is_distinct_from("recurring"),
        Task.item_kind == "task",
    ]


def backlog_tasks_filter() -> list:
    """Корневые задачи бэклога."""
    return [
        Task.is_archived == False,
        Task.due_date == None,
        Task.parent_task_id == None,
        Task.status.in_(["новая", "в_работе", "отложена"]),
        Task.item_kind == "task",
    ]


async def load_task_categories(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Category)
        .where(Category.type == "task")
        .order_by(Category.is_global.desc(), Category.name)
    )
    categories = result.scalars().all()
    return [{"id": c.id, "name": c.name, "is_global": c.is_global} for c in categories]


def _strip_date_words(text: str) -> str:
    clean = text
    for word in ["завтра", "сегодня", "послезавтра"]:
        clean = clean.replace(word, "").replace(word.capitalize(), "").strip()
    return clean


async def create_task_from_text(
    db: AsyncSession,
    raw_text: str,
    *,
    source: str = "telegram",
    target: Literal["today", "backlog"] = "today",
) -> tuple[Task, str]:
    """
    Создать задачу. Возвращает (task, human_summary).
    target=backlog — без даты; target=today — на сегодня или дату от AI.
    """
    text = raw_text.strip()
    backlog_m = _BACKLOG_PREFIX.match(text)
    if backlog_m:
        text = text[backlog_m.end() :].strip()
        target = "backlog"

    clean_title, due_time = parse_time_from_title(text)
    clean_title = _strip_date_words(clean_title) or text

    cat_list = await load_task_categories(db)

    dup_filters = [
        Task.title == clean_title,
        Task.is_archived == False,
        Task.parent_task_id == None,
    ]
    if target == "backlog":
        dup_filters.append(Task.due_date == None)
        task_due_date = None
        category_id = None
        tags_str = None
        if cat_list:
            ai_result = await ai_service.categorize(raw_text, cat_list)
            category_id = ai_result.get("category_id")
            tags_list = ai_result.get("tags", [])
            tags_str = ", ".join(tags_list) if tags_list else None
    else:
        task_due_date = date.today()
        category_id = None
        tags_str = None
        if cat_list:
            ai_result = await ai_service.categorize(raw_text, cat_list)
            category_id = ai_result.get("category_id")
            tags_list = ai_result.get("tags", [])
            tags_str = ", ".join(tags_list) if tags_list else None
            due_date_str = ai_result.get("due_date")
            if due_date_str:
                try:
                    task_due_date = date.fromisoformat(due_date_str)
                except ValueError:
                    pass
        dup_filters.append(Task.due_date == task_due_date)

    dup = await db.execute(select(Task).where(*dup_filters))
    if dup.scalar_one_or_none():
        where = "бэклоге" if target == "backlog" else task_due_date.strftime("%d.%m.%Y")
        raise ValueError(f"«{clean_title}» уже есть ({where})")

    task = Task(
        title=clean_title,
        category_id=category_id,
        source=source,
        due_date=task_due_date,
        due_time=due_time if target == "today" else None,
        tags=tags_str,
        status="новая",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    cat_name = "Без категории"
    if category_id:
        cat_obj = next((c for c in cat_list if c["id"] == category_id), None)
        if cat_obj:
            cat_name = cat_obj["name"]

    if target == "backlog":
        summary = f"📥 В бэклог: {clean_title}\n📂 {cat_name}"
    else:
        time_part = f" ⏰ {due_time.strftime('%H:%M')}" if due_time else ""
        summary = (
            f"✅ На {task_due_date.strftime('%d.%m.%Y')}: {clean_title}{time_part}\n"
            f"📂 {cat_name}"
        )
    if tags_str:
        summary += f"\n🏷️ {tags_str}"

    return task, summary


async def mark_task_complete(db: AsyncSession, task: Task) -> None:
    """Закрыть задачу как на вебе — архив корня, подзадачи зачёркнуты."""
    task.status = "выполнена"
    task.completed_at = datetime.now()
    if task.parent_task_id is None:
        task.is_archived = True
        task.item_kind = "task"
        children_result = await db.execute(
            select(Task).where(
                Task.parent_task_id == task.id,
                Task.status != "выполнена",
            )
        )
        for child in children_result.scalars().all():
            child.status = "выполнена"
            child.completed_at = datetime.now()
            child.is_archived = False
    await db.commit()


async def plan_task_for_today(db: AsyncSession, task_id: int) -> Task | None:
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        return None
    task.due_date = date.today()
    task.postpones = 0
    task.status = "новая"
    await db.commit()
    return task


async def fetch_today_tasks(db: AsyncSession) -> list[Task]:
    result = await db.execute(
        select(Task)
        .where(*today_open_tasks_filter())
        .order_by(*dashboard_task_order_by())
    )
    return list(result.scalars().all())


async def fetch_backlog_tasks(db: AsyncSession, *, limit: int = 30) -> list[Task]:
    result = await db.execute(
        select(Task)
        .where(*backlog_tasks_filter())
        .order_by(Task.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def find_tasks_to_complete(db: AsyncSession, task_name: str) -> list[Task]:
    """Сегодня + бэклог — fuzzy по названию."""
    pattern = f"%{task_name}%"
    today = date.today()
    result = await db.execute(
        select(Task).where(
            Task.is_archived == False,
            Task.status.in_(["новая", "в_работе"]),
            Task.parent_task_id == None,
            Task.item_kind == "task",
            Task.title.ilike(pattern),
            or_(Task.due_date == today, Task.due_date == None),
        )
    )
    return list(result.scalars().all())


async def format_today_stats(db: AsyncSession) -> str:
    completed, total = await get_today_stats(db)
    return f"📊 Прогресс сегодня: {completed}/{total}"
