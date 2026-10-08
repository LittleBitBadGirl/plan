"""День без хвоста: похвала за закрытый день и память о таких днях.

Вера: «если там уже были задачи и они все ушли — писать, что ты молодец, мы
запомним этот день»; «а если и регулярные все закрыла — то вообще всё».

Проверяем и то, что видно на экране (через HTTP), и то, что осталось в базе:
похвала без записи была бы обещанием, а запись без правил — враньём в аналитике.
"""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.database import async_session
from app.models.day_win import DayWin
from app.models.recurring import RecurringTask
from app.models.task import Task
from app.services.day_win_service import day_wins_summary, load_day_state
from app.services.rollover_service import rollover_overdue_tasks
from app.web.deps import get_flow_data


async def _wins(day=None, session=None):
    """Строки памяти о днях (свежая сессия, если сессию не передали)."""
    if session is not None:
        stmt = select(DayWin)
        if day is not None:
            stmt = stmt.where(DayWin.day == day)
        return list((await session.execute(stmt)).scalars().all())
    async with async_session() as s:
        return await _wins(day, session=s)


def _done(title, day, **kwargs):
    task = Task(
        title=title,
        planned_for=day,
        status="выполнена",
        completed_at=datetime.utcnow(),
        source="web",
        **kwargs,
    )
    return task


@pytest.mark.asyncio
async def test_empty_day_still_says_the_day_is_empty(client):
    """Дня ещё не было — это не победа: «возьми из бэклога», а не поздравление."""
    resp = await client.get("/")

    assert resp.status_code == 200
    assert "День пока пустой" in resp.text
    assert "Все задачи на сегодня закрыты" not in resp.text


@pytest.mark.asyncio
async def test_last_task_closed_shows_congrats_and_remembers_day(client, db):
    """Последняя задача дня закрыта — поздравление сразу и запись в базе."""
    today = date.today()
    first = Task(title="Первая", planned_for=today, status="новая", source="web")
    second = Task(title="Вторая", planned_for=today, status="новая", source="web")
    db.add_all([first, second])
    await db.commit()
    await db.refresh(first)
    await db.refresh(second)

    await client.post(f"/tasks/{first.id}/complete", headers={"HX-Target": f"task-{first.id}"})

    page = await client.get("/")
    assert "Все задачи на сегодня закрыты" not in page.text, "вторая задача ещё висит"
    assert await _wins(today) == [], "незакрытая задача — не повод запоминать день"

    resp = await client.post(
        f"/tasks/{second.id}/complete", headers={"HX-Target": f"task-{second.id}"}
    )
    assert resp.status_code == 200
    # Поздравление приезжает тем же ответом: карточка задачи исчезает, а блок дня
    # обязан появиться не после перезагрузки.
    assert 'id="day-win"' in resp.text
    assert "Ты закрыла все задачи на сегодня" in resp.text

    page = await client.get("/")
    assert "Все задачи на сегодня закрыты" in page.text

    wins = await _wins(today)
    assert len(wins) == 1
    assert wins[0].level == "tasks"
    assert wins[0].tasks_done == 2


@pytest.mark.asyncio
async def test_regular_done_makes_it_full_win(client, db):
    """Задачи закрыты, но осталось регулярное — уровень «tasks», не «full»."""
    today = date.today()
    task = Task(title="Единственная", planned_for=today, status="новая", source="web")
    template = RecurringTask(
        title="Зарядка", recurrence_type="daily", start_date=today, is_active=True
    )
    db.add_all([task, template])
    await db.commit()
    await db.refresh(task)
    await db.refresh(template)

    await client.post(f"/tasks/{task.id}/complete", headers={"HX-Target": f"task-{task.id}"})
    wins = await _wins(today)
    assert len(wins) == 1 and wins[0].level == "tasks", "регулярное ещё не отмечено"

    resp = await client.post(f"/api/recurring/{template.id}/complete")
    assert resp.status_code == 200
    assert "Ты закрыла вообще всё" in resp.text

    wins = await _wins(today)
    assert len(wins) == 1, "запись на день одна: второе закрытие обновляет её"
    assert wins[0].level == "full"
    assert wins[0].recurring_done == 1


@pytest.mark.asyncio
async def test_recurring_all_done_line_on_dashboard(client, db):
    """Когда отмечены все регулярные, блок не пропадает: видно, что всё закрыто."""
    today = date.today()
    template = RecurringTask(
        title="Зарядка", recurrence_type="daily", start_date=today, is_active=True
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    page = await client.get("/")
    assert "Зарядка" in page.text

    await client.post(f"/api/recurring/{template.id}/complete")

    page = await client.get("/")
    assert "регулярных на сегодня закрыты" in page.text


@pytest.mark.asyncio
async def test_new_task_removes_the_win(client, db):
    """Добавила задачу после победы — день снова с хвостом, записи быть не должно."""
    today = date.today()
    task = Task(title="Одна", planned_for=today, status="новая", source="web")
    db.add(task)
    await db.commit()
    await db.refresh(task)

    await client.post(f"/tasks/{task.id}/complete", headers={"HX-Target": f"task-{task.id}"})
    await client.get("/")
    assert len(await _wins(today)) == 1

    await client.post("/tasks/create", data={"title": "Ещё задача", "where": "today"})

    page = await client.get("/")
    assert "Все задачи на сегодня закрыты" not in page.text
    assert await _wins(today) == [], "день с хвостом не должен остаться в памяти"


@pytest.mark.asyncio
async def test_deleted_open_day_task_is_not_a_win(client, db):
    """Удалить задачу — не то же самое, что сделать её."""
    today = date.today()
    db.add_all([
        _done("Сделана", today, is_archived=True),
        Task(title="Удалённая", planned_for=today, status="новая", is_archived=True, source="web"),
    ])
    await db.commit()

    page = await client.get("/")
    assert "Все задачи на сегодня закрыты" not in page.text
    assert await _wins(today) == []


@pytest.mark.asyncio
async def test_taken_subtask_counts_as_day_task(db):
    """Взятый на день кусок большой задачи — тоже задача дня."""
    today = date.today()
    parent = Task(title="Родитель", status="новая", source="web")
    db.add(parent)
    await db.flush()
    sub = Task(title="Кусок", parent_task_id=parent.id, planned_for=today, status="новая", source="web")
    db.add(sub)
    await db.commit()

    state = await load_day_state(db, today)
    assert (state.tasks_total, state.tasks_done) == (1, 0)
    assert state.level == "none"

    sub.status = "выполнена"
    await db.commit()

    state = await load_day_state(db, today)
    assert state.is_win
    assert state.tasks_done == 1


@pytest.mark.asyncio
async def test_rollover_remembers_closed_day(db):
    """Ночная зачистка запоминает закрытый день, пока данные ещё честные."""
    yesterday = date.today() - timedelta(days=1)
    db.add(_done("Вчера закрыто", yesterday))
    await db.commit()

    await rollover_overdue_tasks(db)

    wins = await _wins(yesterday, session=db)
    assert len(wins) == 1
    assert wins[0].level == "tasks"


@pytest.mark.asyncio
async def test_rollover_does_not_remember_day_with_tail(db):
    """Хвост был — дня в памяти нет (иначе похвала досталась бы задним числом)."""
    yesterday = date.today() - timedelta(days=1)
    db.add_all([
        _done("Вчера закрыто", yesterday),
        Task(title="Вчера брошено", planned_for=yesterday, status="новая", source="web"),
    ])
    await db.commit()

    await rollover_overdue_tasks(db)

    assert await _wins(yesterday, session=db) == []


@pytest.mark.asyncio
async def test_day_wins_summary_and_stats_block(client, db):
    """Аналитика: блок «Дни без хвоста» со своими числами и списком дней."""
    today = date.today()
    db.add_all([
        DayWin(day=today, level="full", tasks_done=4, recurring_done=2),
        DayWin(day=today - timedelta(days=40), level="tasks", tasks_done=3, recurring_done=0),
    ])
    await db.commit()

    summary = await day_wins_summary(db, today, window_days=30)
    assert summary["count"] == 1, "в окно 30 дней попадает только сегодняшняя запись"
    assert summary["full"] == 1
    assert summary["total"] == 2
    assert summary["total_full"] == 1
    assert summary["days"][0]["label"] == today.strftime("%d.%m")

    resp = await client.get("/stats")
    assert resp.status_code == 200
    assert "Дни без хвоста" in resp.text
    assert "Всего записано" in resp.text


@pytest.mark.asyncio
async def test_flow_marks_days_without_tail(db):
    """На графике потока день без хвоста помечен — не только в отдельном блоке."""
    win_day = date.today() - timedelta(days=3)
    db.add(DayWin(day=win_day, level="tasks", tasks_done=2, recurring_done=0))
    await db.commit()

    flow = await get_flow_data(db, "week")
    marks = {s["iso"]: s["win"] for s in flow["series"]}

    assert marks.get(win_day.isoformat()) is True
    assert any(not value for value in marks.values()), "остальные дни помечены не должны"
