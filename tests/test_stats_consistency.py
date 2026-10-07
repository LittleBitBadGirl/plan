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


async def _add_task(db, title, *, completed_on=None, completed_at=None, created_on=None,
                    planned_for=None, source=None, parent_id=None, item_kind="task",
                    status="новая"):
    task = Task(
        title=title,
        status=status,
        item_kind=item_kind,
        source=source,
        parent_task_id=parent_id,
        completed_at=completed_at,
        planned_for=planned_for,
        created_at=datetime.now(timezone.utc),
    )
    if created_on is not None:
        # Полдень локального дня: тест не зависит от часового пояса машины.
        task.created_at = datetime.combine(created_on, time(12, 0)).astimezone(timezone.utc)
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
async def test_flow_year_buckets_are_twelve_full_months():
    """Год в потоке — 12 ПОЛНЫХ месяцев: текущий неполный не берётся.

    Раньше график брал отрезок в 365 дней от первого числа месяца: 13 месяцев,
    включая неполный текущий, поэтому и столбики, и сумма были неполными.
    """
    from app.web.deps import get_flow_data

    async with async_session() as db:
        flow = await get_flow_data(db, "year")

    assert len(flow["buckets"]) == 12
    today = date.today()
    prev_month = today.replace(day=1) - timedelta(days=1)
    assert flow["buckets"][-1]["label"] == prev_month.strftime("%m.%y")
    first_month = prev_month
    for _ in range(11):
        first_month = (first_month.replace(day=1) - timedelta(days=1)).replace(day=1)
    assert flow["buckets"][0]["label"] == first_month.strftime("%m.%y")
    assert flow["created_total"] == sum(b["created"] for b in flow["buckets"])
    assert flow["closed_total"] == sum(b["closed"] for b in flow["buckets"])
    assert flow["series"][0]["day"] == first_month, "первый столбик года — первое число первого месяца"


@pytest.mark.asyncio
async def test_flow_year_bucket_gets_the_arrival_of_its_month():
    """Данные попадают в свой месячный бакет, а не в соседний.

    Сдвиг на месяц здесь не видно на пустой базе: проверка с реальным прилётом
    ловит ошибку «бакет считается по следующему месяцу».
    """
    from app.web.deps import get_flow_data

    today = date.today()
    prev_month_day = today.replace(day=1) - timedelta(days=3)  # всегда в прошлом месяце
    assert prev_month_day.month != today.month

    async with async_session() as db:
        await _add_task(db, "прилёт прошлого месяца", created_on=prev_month_day)
        await db.commit()

    async with async_session() as db:
        flow = await get_flow_data(db, "year")

    last = flow["buckets"][-1]
    assert last["label"] == (today.replace(day=1) - timedelta(days=1)).strftime("%m.%y")
    assert last["created"] == 1, "прилёт прошлого месяца обязан попасть в этот бакет"
    # Ведущие месяцы без данных не показываем: до апреля 2026 у Веры задач нет,
    # поэтому год начинается с первого непустого месяца, а не с пустой полосы.
    assert flow["buckets"][0] == last and len(flow["buckets"]) == 1
    assert flow["created_total"] == 1


@pytest.mark.asyncio
async def test_flow_counts_arrivals_by_local_day_without_recurring_or_subtasks():
    """«Прилетело» — новые корневые задачи без регулярных, по местным суткам.

    Вера: «5 я утром выбираю, а ещё 6 прилетает, и не из бэклога». Поэтому взятая из
    бэклога задача, регулярное вхождение и подзадача в прилёт не попадают, а задача,
    созданная в день, для которого она же и поставлена, считается «сразу на сегодня».
    """
    from app.web.deps import get_flow_data

    today = date.today()
    yesterday = today - timedelta(days=1)

    async with async_session() as db:
        await _add_task(db, "прилетела вчера", created_on=yesterday)
        await _add_task(db, "сразу на сегодня", created_on=yesterday, planned_for=yesterday)
        await _add_task(db, "регулярная", created_on=yesterday, source="recurring")
        parent = await _add_task(db, "родитель с подзадачами", created_on=yesterday)
        await _add_task(db, "подзадача", created_on=yesterday, parent_id=parent.id)
        # Создана вчера, взята сегодня — для сегодняшнего дня это не прилёт.
        await _add_task(db, "из бэклога", created_on=yesterday, planned_for=today)
        await db.commit()

    async with async_session() as db:
        flow = await get_flow_data(db, "week")

    week_created = sum(s["created"] for s in flow["series"])
    # Прилёт: обычная корневая, «сразу на сегодня», родитель и «из бэклога» —
    # последняя СОЗДАНА вчера, поэтому она прилёт вчерашнего дня; что её взяли
    # сегодня, к прилёту не относится (это и есть разница «прилетело / взято»).
    assert week_created == 4, "подзадача и регулярная в прилёт не входят"
    assert flow["created_total"] == 4
    assert flow["by_dow"][6]["created"] + sum(d["created"] for d in flow["by_dow"][:5]) == week_created


@pytest.mark.asyncio
async def test_flow_excludes_non_tasks_and_keeps_null_archive_flag():
    """В прилёт входят только задачи: покупка и пункт чтения — нет, NULL-архив — да.

    `is_archived` у части строк NULL (их писала интеграция мимо ORM), и «не в архиве»
    через `== False` такие строки теряет. Прилёт — факт появления, поэтому архивность
    на него не влияет вовсе; тест держит это явно, чтобы фильтр не появился тихо.
    """
    from app.web.deps import get_flow_data

    yesterday = date.today() - timedelta(days=1)
    async with async_session() as db:
        await _add_task(db, "обычная задача", created_on=yesterday)
        await _add_task(db, "покупка", created_on=yesterday, item_kind="purchase")
        await _add_task(db, "пункт чтения", created_on=yesterday, item_kind="reading")
        archived = await _add_task(db, "без флага архива", created_on=yesterday)
        archived.is_archived = None
        await db.commit()

    async with async_session() as db:
        flow = await get_flow_data(db, "week")

    assert flow["created_total"] == 2, "покупка и пункт чтения — не задачи"


@pytest.mark.asyncio
async def test_flow_splits_weekend_arrivals_from_workday_ones():
    """Прилёт выходного дня не попадает в «в рабочие дни» и виден в субботней клетке.

    Регресс, который тут ловится: перепутать выходные и будни в разбивке местами —
    тогда «в рабочие дни ~X/день» считается по всем дням и врёт в сторону Веры.
    """
    from app.web.deps import get_flow_data

    today = date.today()
    back = (today.weekday() - 5) % 7 or 7
    saturday = today - timedelta(days=back)
    assert saturday.weekday() == 5 and saturday < today

    async with async_session() as db:
        await _add_task(db, "субботний прилёт", created_on=saturday)
        await _add_task(db, "закрыто в субботу", created_on=saturday, completed_on=saturday)
        await db.commit()

    async with async_session() as db:
        flow = await get_flow_data(db, "month")

    sat_entry = next(s for s in flow["series"] if s["day"] == saturday)
    # Обе задачи созданы в субботу (одна тут же закрыта) — значит в этот день их две.
    assert sat_entry["created"] == 2 and sat_entry["weekend"] is True
    assert flow["by_dow"][5]["created"] >= 1, "суббота — индекс 5"
    assert flow["weekend_created"] >= 1
    # «в рабочие дни» считается по невыходным дням — субботний прилёт туда не входит
    assert flow["created_workdays"] == sum(s["created"] for s in flow["series"] if not s["weekend"])


@pytest.mark.asyncio
async def test_stats_page_shows_flow_block(client):
    """На странице есть блок потока, а период переключается отдельным запросом."""
    html = (await client.get("/stats")).text
    assert "Поток задач" in html
    assert "Прилетело" in html and "прилетело" in html
    assert "По дням недели" in html

    week = await client.get("/api/stats/flow?period=week")
    assert week.status_code == 200
    assert "Поток задач" in week.text and "полные дни" in week.text
    # Мусор в параметре не должен ронять блок.
    junk = await client.get("/api/stats/flow?period=вечность")
    assert junk.status_code == 200 and "Поток задач" in junk.text


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
