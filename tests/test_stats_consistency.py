"""Аналитика: одно «закрыто» на всю страницу и равные окна полных дней.

Разбор 07.10.2026: карточки и график считали РАЗНЫЕ множества задач (48 против 80
за 14 дней), «7 дней» было 8 календарных дней с неполной сегодняшней датой внутри,
график резал сутки по UTC, а в блоке «Зависли» искали статус, которого нет в базе.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.database import async_session
from app.models.task import Task
from app.web.deps import (
    _utc_day_bounds,
    completed_counts_by_local_day,
    count_completed_tasks,
    rolling_week_windows,
)


def test_windows_are_seven_full_days_and_today_excluded():
    """Окна сравнения — равные, только полные дни.

    Было `today-7 … today`: 8 календарных дней, в среду окно содержало две среды,
    а прошлое — одну, поэтому «Δ неделя» сравнивала неравные отрезки.
    """
    today = date(2026, 10, 7)  # среда
    (cs, ce), (ps, pe) = rolling_week_windows(today)
    assert (cs, ce) == (date(2026, 9, 30), date(2026, 10, 6))
    assert (ps, pe) == (date(2026, 9, 23), date(2026, 9, 29))
    assert (ce - cs).days + 1 == 7
    assert (pe - ps).days + 1 == 7
    assert ce < today and pe < cs


async def _add_task(db, title, *, completed_on=None, completed_at=None, source=None,
                    parent_id=None, item_kind="task", status="новая"):
    task = Task(
        title=title,
        status=status,
        item_kind=item_kind,
        source=source,
        parent_task_id=parent_id,
        completed_at=completed_at,
        created_at=datetime.now(timezone.utc),
    )
    if completed_on is not None:
        # Полдень локального дня — чтобы тест не зависел от часового пояса машины.
        task.completed_at = datetime.combine(completed_on, time(12, 0)).astimezone(timezone.utc)
    if task.completed_at is not None and status == "новая":
        # Закрытая задача без статуса «выполнена» в подсчёт не попадает —
        # так выглядел бы тест, доказывающий не то, что проверяет.
        task.status = "выполнена"
    db.add(task)
    await db.flush()
    return task


@pytest.mark.asyncio
async def test_cards_and_days_count_the_same_tasks():
    """Карточка и столбики графика берут одно множество: корневые, без регулярных.

    Раньше график считал ВСЕ закрытия, поэтому его сумма не сходилась с карточкой.
    Расхождение только по выходным: карточка темпа считает пн–пт, график рисует все дни.
    """
    monday = date(2026, 9, 28)      # пн
    tuesday = date(2026, 9, 29)     # вт
    saturday = date(2026, 10, 3)    # сб
    sunday = date(2026, 10, 4)      # вс

    async with async_session() as db:
        await _add_task(db, "корневая в пн", completed_on=monday)
        await _add_task(db, "корневая в сб", completed_on=saturday)
        await _add_task(db, "корневая в вс", completed_on=sunday)
        parent = await _add_task(db, "родитель", completed_on=None)
        await _add_task(db, "подзадача во вт", completed_on=tuesday, parent_id=parent.id)
        await _add_task(db, "тираж регулярной", completed_on=tuesday, source="recurring")
        await _add_task(db, "покупка", completed_on=tuesday, item_kind="purchase")
        await db.commit()

    async with async_session() as db:
        by_day = await completed_counts_by_local_day(db, monday, sunday)
        workdays = await count_completed_tasks(db, monday, sunday, workdays_only=True)

    assert by_day.get(monday) == 1
    assert by_day.get(saturday) == 1 and by_day.get(sunday) == 1
    assert by_day.get(tuesday, 0) == 0, "подзадача, тираж регулярной и покупка не считаются"
    assert workdays == 1
    assert workdays == sum(n for d, n in by_day.items() if d.weekday() < 5), \
        "карточка обязана совпадать с суммой будних столбиков графика"


def _set_tz(monkeypatch, name="Europe/Moscow"):
    """Поставить пояс процессу: без этого тест на машине в UTC доказывает не то."""
    import time as _time

    monkeypatch.setenv("TZ", name)
    _time.tzset()
    return _time


@pytest.mark.asyncio
async def test_completion_buckets_by_local_day_not_utc(monkeypatch):
    """Закрытие в 00:30 МСК — это СЛЕДУЮЩИЙ день, а не предыдущий.

    График резал сутки по UTC (`func.date`), поэтому такие закрытия (в базе Веры
    их 36) уезжали в предыдущий столбик и не сходились с карточками. Пояс задаём
    явно: на машине в UTC старый разрез прошёл бы этот тест, ничего не доказывая.
    """
    _time = _set_tz(monkeypatch)
    try:
        moment_utc = datetime(2026, 10, 5, 21, 30, tzinfo=timezone.utc)
        local_day = moment_utc.astimezone().date()
        assert local_day == date(2026, 10, 6), "сам тест обязан знать, что тут МСК"

        async with async_session() as db:
            await _add_task(db, "ночное закрытие", completed_at=moment_utc)
            await db.commit()

        async with async_session() as db:
            counts = await completed_counts_by_local_day(db, local_day - timedelta(days=1), local_day)

        assert counts.get(local_day) == 1
        assert counts.get(local_day - timedelta(days=1), 0) == 0
    finally:
        monkeypatch.delenv("TZ", raising=False)
        _time.tzset()


@pytest.mark.asyncio
async def test_weekend_filter_counts_local_weekday(monkeypatch):
    """Закрытие в ночь с воскресенья на понедельник (по МСК) — это будний день.

    Фильтр выходных считал `strftime('%w', completed_at)` по UTC-дате, поэтому
    понедельничное ночное закрытие выбрасывалось из будних чисел карточки.
    """
    _time = _set_tz(monkeypatch)
    try:
        moment_utc = datetime(2026, 10, 4, 21, 30, tzinfo=timezone.utc)  # пн 00:30 МСК
        local_day = moment_utc.astimezone().date()
        assert local_day == date(2026, 10, 5) and local_day.weekday() == 0

        async with async_session() as db:
            await _add_task(db, "ночь на понедельник", completed_at=moment_utc)
            await db.commit()

        async with async_session() as db:
            workdays = await count_completed_tasks(db, local_day, local_day, workdays_only=True)

        assert workdays == 1, "по местному дню это понедельник, а не воскресенье"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        _time.tzset()


@pytest.mark.asyncio
async def test_year_chart_is_twelve_full_months():
    """Год — 12 ПОЛНЫХ месяцев: текущий неполный в график не входит.

    Раньше брался отрезок в 365 дней от первого числа месяца (13 месяцев, включая
    неполный текущий) — столбики и сумма были неполными.
    """
    from app.web.deps import get_history_data

    async with async_session() as db:
        data = await get_history_data(db, "year")

    assert len(data["history"]) == 12
    today = date.today()
    prev_month = today.replace(day=1) - timedelta(days=1)
    assert data["history"][-1][0] == prev_month.strftime("%Y-%m")
    first_month = prev_month
    for _ in range(11):
        first_month = (first_month.replace(day=1) - timedelta(days=1)).replace(day=1)
    assert data["history"][0][0] == first_month.strftime("%Y-%m")
    assert data["total"] == sum(count for _, count in data["history"])


@pytest.mark.asyncio
async def test_avg_uses_full_days_and_reports_workdays():
    """Средний темп: только полные дни и честное число рабочих дней.

    Подпись «за 14 раб. дней» при 14 КАЛЕНДАРНЫХ днях (в них 10 рабочих) — именно
    та мелочь, из-за которой «~5» невозможно было пересчитать по странице.
    """
    from app.web.deps import count_workdays_between, get_avg_completed_per_day

    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=13)
    workdays = [start + timedelta(days=i) for i in range(14) if (start + timedelta(days=i)).weekday() < 5]

    async with async_session() as db:
        for day in workdays[:3]:
            await _add_task(db, f"закрыто {day}", completed_on=day)
        await _add_task(db, "закрыто сегодня", completed_on=today)  # неполный день не входит
        await db.commit()

    async with async_session() as db:
        avg, reported_workdays, got_start, got_end = await get_avg_completed_per_day(db, 14)

    assert (got_start, got_end) == (start, end), "сегодня в окно не входит"
    assert reported_workdays == count_workdays_between(start, end) == len(workdays)
    assert avg == pytest.approx(3 / len(workdays))


@pytest.mark.asyncio
async def test_stats_page_has_no_dead_cards_and_shows_periods(client):
    """На странице аналитики нет мёртвых карточек, а у чисел видны периоды."""
    html = (await client.get("/stats")).text

    assert "Зависли" not in html, "статуса «в работе» в базе нет — карточка была всегда нулевой"
    assert "Темп нед." not in html, "метрика сравнивала неделю с базой, куда входила та же неделя"
    assert "Рекорд нед." not in html
    assert "Подзадачи" not in html
    assert "полные дни" in html and "закрыто" in html, "у графика обязан быть период и сумма"
    assert "Закрытие:" not in html, "прогноз даты без притока новых задач вводит в заблуждение"
