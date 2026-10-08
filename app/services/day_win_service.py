"""День без хвоста: состояние дня и память о закрытых днях.

Два вопроса, на которые отвечает модуль:

1. «Всё ли на сегодня закрыто?» — ``DayState``: сколько взято на день, сколько
   из этого закрыто, сколько регулярных на день и сколько из них отмечено.
   День считается закрытым, когда выполнено ВСЁ, что стоит в дне: и задачи, и
   регулярные. Если задач на день не брали вовсе — дня нет, хвалить не за что.

2. «Какие дни уже закрывались?» — таблица ``day_wins``. Пересчитать это задним
   числом нельзя: ночной возврат стирает ``planned_for`` у незакрытых, и в любом
   прошлом дне остаются только закрытые задачи. Поэтому запись делается в момент
   закрытия дня и в момент ночной зачистки (пока ``planned_for`` ещё не стёрт), а
   если день снова получил хвост — запись убирается, чтобы аналитика не врала.

Определение дня берётся ровно то же, что у списка на дашборде: корневые задачи
без регулярных плюс подзадачи, взятые на день. Удалённая из дня незакрытая
задача хвостом остаётся — её не сделали, а убрали.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.day_win import DayWin
from app.models.task import Task

LEVEL_NONE = "none"
LEVEL_TASKS = "tasks"
LEVEL_FULL = "full"

WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


@dataclass(frozen=True)
class DayState:
    """Что сейчас происходит с днём."""

    day: date
    tasks_total: int = 0
    tasks_done: int = 0
    recurring_total: int = 0
    recurring_done: int = 0

    @property
    def tasks_all_done(self) -> bool:
        return self.tasks_total > 0 and self.tasks_done >= self.tasks_total

    @property
    def recurring_all_done(self) -> bool:
        return self.recurring_total > 0 and self.recurring_done >= self.recurring_total

    @property
    def level(self) -> str:
        """none — хвост есть или дня не было; tasks — задачи дня закрыты;
        full — закрыто вообще всё, включая регулярные."""
        if not self.tasks_all_done:
            return LEVEL_NONE
        return LEVEL_FULL if self.recurring_all_done else LEVEL_TASKS

    @property
    def is_win(self) -> bool:
        return self.level != LEVEL_NONE

    @property
    def left(self) -> int:
        """Сколько ещё висит на дне (задачи + регулярные)."""
        return max(self.tasks_total - self.tasks_done, 0) + max(
            self.recurring_total - self.recurring_done, 0
        )

    @property
    def recurring_left(self) -> int:
        return max(self.recurring_total - self.recurring_done, 0)


def build_day_state(
    day: date,
    *,
    tasks_total: int = 0,
    tasks_done: int = 0,
    recurring_total: int = 0,
    recurring_done: int = 0,
) -> DayState:
    return DayState(
        day=day,
        tasks_total=int(tasks_total or 0),
        tasks_done=int(tasks_done or 0),
        recurring_total=int(recurring_total or 0),
        recurring_done=int(recurring_done or 0),
    )


def day_task_filters(day: date, work_category_ids: Optional[Iterable[int]] = None) -> list:
    """Что считается задачами дня.

    День на дашборде = корневые задачи (не регулярные) + подзадачи, взятые на
    день. Фильтра «не в архиве» здесь нет намеренно:

    * закрытая корневая задача лежит в архиве (так работает закрытие), и если её
      отбросить, день будет выглядеть закрытым, не имея ни одной сделанной задачи;
    * удалённая из дня незакрытая задача хвостом остаётся: похвала «всё закрыто»
      не должна доставаться за удаление задачи вместо её выполнения.
    """
    filters = [
        Task.planned_for == day,
        Task.item_kind == "task",
        or_(
            and_(Task.parent_task_id.is_(None), Task.source.is_distinct_from("recurring")),
            Task.parent_task_id.is_not(None),
        ),
    ]
    if work_category_ids:
        ids = list(work_category_ids)
        filters.append(or_(Task.category_id.is_(None), Task.category_id.notin_(ids)))
    return filters


async def count_day_tasks(
    db: AsyncSession, day: date, work_category_ids: Optional[Iterable[int]] = None
) -> tuple[int, int]:
    """(взято на день, закрыто из них) — одним запросом."""
    result = await db.execute(
        select(
            func.count(Task.id),
            func.sum(case((Task.status == "выполнена", 1), else_=0)),
        ).where(*day_task_filters(day, work_category_ids))
    )
    total, done = result.one()
    return int(total or 0), int(done or 0)


def recurring_counts_for_day(templates, day: date, completed_keys, work_category_ids=None) -> tuple[int, int]:
    """(регулярных на день, отмечено из них) по уже загруженным шаблонам.

    Запросов не делает: и шаблоны, и отметки на день дашборд уже держит в руках,
    поэтому числа регулярных берутся тем же определением, что у блока
    «Регулярные», и не разъезжаются с ним.
    """
    from app.services.recurring_schedule import filter_recurring_templates

    day_templates = filter_recurring_templates(templates, day)
    if work_category_ids:
        ids = set(work_category_ids)
        day_templates = [t for t in day_templates if t.category_id not in ids]
    done = sum(1 for t in day_templates if (t.title, t.category_id) in (completed_keys or set()))
    return len(day_templates), done


async def load_day_state(
    db: AsyncSession, day: date, work_category_ids: Optional[Iterable[int]] = None
) -> DayState:
    """Состояние дня без готового bundle: свои запросы (задачи + регулярные)."""
    from app.services.recurring_completion_service import get_completed_today_keys
    from app.services.recurring_schedule import load_active_recurring_templates

    tasks_total, tasks_done = await count_day_tasks(db, day, work_category_ids)
    templates = await load_active_recurring_templates(db)
    completed_keys = await get_completed_today_keys(db, day)
    recurring_total, recurring_done = recurring_counts_for_day(
        templates, day, completed_keys, work_category_ids
    )
    return build_day_state(
        day,
        tasks_total=tasks_total,
        tasks_done=tasks_done,
        recurring_total=recurring_total,
        recurring_done=recurring_done,
    )


async def sync_day_win(
    db: AsyncSession,
    day: date,
    state: Optional[DayState] = None,
    work_category_ids: Optional[Iterable[int]] = None,
) -> DayState:
    """Записать (или снять) память о дне. Коммит — на вызывающем.

    День закрыт — строка появляется; у дня снова появился хвост (добавили
    задачу, сняли отметку с регулярной) — строка уходит: аналитика показывает
    только то, что правда.

    Пишем одним INSERT ... ON CONFLICT, а не «прочитал, потом записал»: дашборд
    и закрытие задачи могут прийти в одну секунду, и две вставки на один день
    уронили бы запрос на уникальном индексе.
    """
    if state is None:
        state = await load_day_state(db, day, work_category_ids)

    if not state.is_win:
        await db.execute(delete(DayWin).where(DayWin.day == day))
        return state

    await db.execute(
        sqlite_insert(DayWin)
        .values(
            day=day,
            level=state.level,
            tasks_done=state.tasks_done,
            recurring_done=state.recurring_done,
        )
        .on_conflict_do_update(
            index_elements=[DayWin.day],
            set_={
                "level": state.level,
                "tasks_done": state.tasks_done,
                "recurring_done": state.recurring_done,
            },
        )
    )
    return state


async def load_day_wins(db: AsyncSession, start: date, end: date) -> dict[date, DayWin]:
    """Память о днях за окно — для аналитики и подсветки графика."""
    result = await db.execute(
        select(DayWin).where(DayWin.day >= start, DayWin.day <= end)
    )
    return {row.day: row for row in result.scalars().all()}


async def count_day_wins(db: AsyncSession, full_only: bool = False) -> int:
    stmt = select(func.count(DayWin.id))
    if full_only:
        stmt = stmt.where(DayWin.level == LEVEL_FULL)
    return (await db.execute(stmt)).scalar() or 0


async def day_wins_summary(db: AsyncSession, today: Optional[date] = None, window_days: int = 30) -> dict:
    """Блок аналитики «Дни без хвоста»: сколько их и какие именно."""
    today = today or date.today()
    start = today - timedelta(days=window_days - 1)

    rows = (
        await db.execute(
            select(DayWin)
            .where(DayWin.day >= start, DayWin.day <= today)
            .order_by(DayWin.day.desc())
        )
    ).scalars().all()

    days = [
        {
            "day": row.day,
            "label": row.day.strftime("%d.%m"),
            "weekday": WEEKDAY_SHORT[row.day.weekday()],
            "full": row.level == LEVEL_FULL,
            "tasks_done": row.tasks_done or 0,
            "recurring_done": row.recurring_done or 0,
        }
        for row in rows
    ]

    return {
        "window_days": window_days,
        "window_label": f"{start.strftime('%d.%m')}–{today.strftime('%d.%m')}",
        "count": len(days),
        "full": sum(1 for d in days if d["full"]),
        "total": await count_day_wins(db),
        "total_full": await count_day_wins(db, full_only=True),
        "days": days,
        "has_any": bool(days),
    }


def day_win_view(state: DayState) -> dict:
    """Состояние дня в виде, удобном шаблонам."""
    return {
        "day": state.day,
        "level": state.level,
        "is_win": state.is_win,
        "tasks_done": state.tasks_done,
        "tasks_total": state.tasks_total,
        "recurring_done": state.recurring_done,
        "recurring_total": state.recurring_total,
        "recurring_left": state.recurring_left,
        "left": state.left,
    }
