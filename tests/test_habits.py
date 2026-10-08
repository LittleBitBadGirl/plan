"""Тесты логики трекеров привычек."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.api.habits import (
    build_habit_cycle_grid,
    build_habit_history_cycles,
    compute_cycle_start_dates,
    compute_next_cycle_start,
    current_cycle_number,
    cycle_window,
    habit_cycle_window,
    month_bounds,
    monthly_cycle_number,
    monthly_cycle_window,
    shift_month,
)
from app.models.habit import CYCLE_MODE_DAYS, CYCLE_MODE_MONTHLY
from app.db.database import async_session
from app.models.habit import Habit
from app.models.habit_log import HabitLog


class TestComputeNextCycleStart:
    def test_continues_after_late_restart(self):
        today = date(2025, 6, 3)
        habit = Habit(
            title="Уход",
            start_date=date(2025, 5, 1),
            target_days=30,
            current_cycle=1,
        )
        # Цикл 1: 1–30 мая; нажали «След. 30 дней» 3 июня → старт 31 мая
        assert compute_next_cycle_start(habit, today) == date(2025, 5, 31)

    def test_restart_before_window_end_moves_start_to_window_end(self):
        """Раньше здесь возвращалось `today`, и циклы делили один день."""
        today = date(2025, 5, 25)
        habit = Habit(
            title="Уход",
            start_date=date(2025, 5, 1),
            target_days=30,
            current_cycle=1,
        )
        new_start = compute_next_cycle_start(habit, today)
        assert new_start == date(2025, 5, 31)
        # Нахлёста быть не может: ни один день старого окна не входит в новое
        old_window = {date(2025, 5, 1) + timedelta(days=i) for i in range(30)}
        new_window = {new_start + timedelta(days=i) for i in range(30)}
        assert old_window & new_window == set()

    def test_restart_on_last_day_does_not_repeat_that_day(self):
        """Случай Веры: нажатие в последний день цикла повторяло этот день в новом."""
        last_day = date(2026, 9, 28)
        habit = Habit(
            title="Не курю",
            start_date=last_day - timedelta(days=29),  # цикл: 30.08–28.09
            target_days=30,
            current_cycle=1,
        )
        new_start = compute_next_cycle_start(habit, last_day)
        assert new_start == date(2026, 9, 29)
        assert new_start != last_day, "день нажатия не должен попасть в новый цикл"

    def test_on_time_restart(self):
        today = date(2025, 5, 31)
        habit = Habit(
            title="Уход",
            start_date=date(2025, 5, 1),
            target_days=30,
            current_cycle=1,
        )
        assert compute_next_cycle_start(habit, today) == date(2025, 5, 31)

    def test_habit_without_start_date_starts_today(self):
        today = date(2025, 5, 25)
        habit = Habit(title="Уход", start_date=None, target_days=30, current_cycle=1)
        assert compute_next_cycle_start(habit, today) == today


class TestComputeCycleStartDates:
    def test_on_time_cycles_chain_backwards(self):
        habit = Habit(
            title="Уход",
            start_date=date(2025, 5, 31),
            target_days=30,
            current_cycle=2,
        )
        starts = compute_cycle_start_dates(habit, {1: [date(2025, 5, 1)]})
        assert starts[2] == date(2025, 5, 31)
        assert starts[1] == date(2025, 5, 1)


class TestBuildHabitHistoryCycles:
    def test_past_cycle_gets_color_grid(self):
        habit = Habit(
            title="Уход",
            start_date=date(2025, 5, 31),
            target_days=30,
            current_cycle=2,
        )
        logs = [
            HabitLog(habit_id=1, cycle_number=1, date=date(2025, 5, 1)),
            HabitLog(habit_id=1, cycle_number=1, date=date(2025, 5, 2)),
            HabitLog(habit_id=1, cycle_number=2, date=date(2025, 5, 31)),
        ]
        cycles = build_habit_history_cycles(habit, logs, date(2025, 6, 15))

        assert len(cycles) == 2
        past = cycles[1]
        assert past["cycle_number"] == 1
        assert past["is_current"] is False
        assert len(past["dates"]) == 30
        assert past["logs"] == {"2025-05-01", "2025-05-02"}
        assert past["progress"] == 2


class TestHabitNextCycleAPI:
    @pytest.mark.asyncio
    async def test_next_cycle_preserves_gap_days(self, client, db):
        today = date.today()
        old_start = today - timedelta(days=33)
        habit = Habit(title="Трекер", start_date=old_start, current_cycle=1, target_days=30)
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        response = await client.post(f"/api/habits/{habit.id}/next-cycle")
        assert response.status_code == 303

        await db.refresh(habit)
        assert habit.current_cycle == 2
        assert habit.start_date == old_start + timedelta(days=30)

    @pytest.mark.asyncio
    async def test_next_cycle_on_last_day_does_not_overlap(self, client, db):
        """Нажатие в последний день цикла не повторяет этот день в новом цикле."""
        today = date.today()
        habit = Habit(
            title="Трекер",
            start_date=today - timedelta(days=29),  # последний, 30-й день — сегодня
            current_cycle=1,
            target_days=30,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        response = await client.post(f"/api/habits/{habit.id}/next-cycle")
        assert response.status_code == 303

        await db.refresh(habit)
        assert habit.current_cycle == 2
        assert habit.start_date == today + timedelta(days=1)
        assert today not in build_habit_cycle_grid(habit, today)["dates"], "день нажатия попал в оба цикла"

    @pytest.mark.asyncio
    async def test_repeated_press_does_not_move_cycle_ahead(self, client, db):
        """Повторное нажатие, пока новый цикл не начался, не сдвигает его на месяц."""
        today = date.today()
        habit = Habit(
            title="Трекер",
            start_date=today - timedelta(days=29),
            current_cycle=1,
            target_days=30,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        await client.post(f"/api/habits/{habit.id}/next-cycle")
        second = await client.post(f"/api/habits/{habit.id}/next-cycle")
        assert second.status_code == 303

        await db.refresh(habit)
        assert habit.current_cycle == 2
        assert habit.start_date == today + timedelta(days=1)

    @pytest.mark.asyncio
    async def test_same_day_can_be_marked_in_two_cycles(self, client, db):
        """День, уже отмеченный в прошлом цикле, отмечается и в новом.

        На этом падала боевая база: там осталось старое ограничение
        UNIQUE (habit_id, date), и отметка отдавала 500.
        """
        today = date.today()
        habit = Habit(title="Трекер", start_date=today, current_cycle=2, target_days=30)
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        db.add(HabitLog(habit_id=habit.id, cycle_number=1, date=today))
        await db.commit()

        response = await client.post(
            "/api/habits/toggle",
            json={"habit_id": habit.id, "date": today.isoformat()},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "success", "action": "added"}

        async with async_session() as fresh:
            cycles = (
                await fresh.execute(
                    select(HabitLog.cycle_number).where(HabitLog.habit_id == habit.id)
                )
            ).scalars().all()
        assert sorted(cycles) == [1, 2], "отметка должна жить в каждом цикле отдельно"


class TestCycleWindow:
    """Окно цикла: конец — за день до старта следующего, пересечений нет."""

    def test_window_ends_the_day_before_next_start(self):
        habit = Habit(
            title="Не курю",
            start_date=date(2026, 9, 29),
            target_days=30,
            current_cycle=2,
        )
        start, end = cycle_window(habit)
        assert (start, end) == (date(2026, 9, 29), date(2026, 10, 28))
        assert compute_next_cycle_start(habit, date(2026, 10, 20)) == end + timedelta(days=1)

    def test_neighbouring_windows_do_not_share_a_day(self):
        habit = Habit(
            title="Не курю",
            start_date=date(2026, 9, 29),
            target_days=30,
            current_cycle=2,
        )
        previous_start = habit.start_date - timedelta(days=30)
        previous = {previous_start + timedelta(days=i) for i in range(30)}
        start, end = cycle_window(habit)
        current = {start + timedelta(days=i) for i in range(30)}
        assert previous & current == set()
        assert min(current) == max(previous) + timedelta(days=1)

    def test_window_without_start_date_is_today(self):
        habit = Habit(title="Новый", start_date=None, target_days=30, current_cycle=1)
        start, end = cycle_window(habit)
        assert start == date.today()
        assert end == date.today() + timedelta(days=29)


class TestHabitToggleWindow:
    @pytest.mark.asyncio
    async def test_day_outside_current_cycle_is_refused(self, client, db):
        """Страница, открытая до смены цикла, не пишет отметку в чужой цикл."""
        today = date.today()
        habit = Habit(title="Трекер", start_date=today, current_cycle=2, target_days=30)
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        yesterday = today - timedelta(days=1)
        response = await client.post(
            "/api/habits/toggle",
            json={"habit_id": habit.id, "date": yesterday.isoformat()},
        )
        assert response.status_code == 409
        assert "не входит в текущий цикл" in response.json()["detail"]

        async with async_session() as fresh:
            logs = (
                await fresh.execute(
                    select(HabitLog).where(HabitLog.habit_id == habit.id)
                )
            ).scalars().all()
        assert logs == [], "отметка вне окна не должна сохраняться"

    @pytest.mark.asyncio
    async def test_last_day_of_previous_cycle_is_markable_in_new_cycle(self, client, db):
        """Ровно случай Веры: день конца прошлого цикла не блокируется в новом."""
        today = date.today()
        habit = Habit(
            title="Не курю",
            start_date=today,               # новый цикл начинается сегодня
            current_cycle=2,
            target_days=30,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        # Последний день прошлого цикла — вчера, и он там уже отмечен.
        db.add(HabitLog(habit_id=habit.id, cycle_number=1, date=today - timedelta(days=1)))
        await db.commit()

        day_one = await client.post(
            "/api/habits/toggle",
            json={"habit_id": habit.id, "date": today.isoformat()},
        )
        assert day_one.status_code == 200
        assert day_one.json()["action"] == "added"

        # И повторное нажатие снимает отметку своего цикла, не трогая прошлый.
        again = await client.post(
            "/api/habits/toggle",
            json={"habit_id": habit.id, "date": today.isoformat()},
        )
        assert again.status_code == 200
        assert again.json()["action"] == "removed"

        async with async_session() as fresh:
            rows = (
                await fresh.execute(
                    select(HabitLog.cycle_number, HabitLog.date)
                    .where(HabitLog.habit_id == habit.id)
                    .order_by(HabitLog.cycle_number)
                )
            ).all()
        assert [(c, d) for c, d in rows] == [(1, today - timedelta(days=1))]


class TestHabitCycleGuard:
    @pytest.mark.asyncio
    async def test_press_on_start_day_does_not_skip_cycle(self, client, db):
        """Нажатие в первый день начавшегося цикла не перескакивает его."""
        today = date.today()
        habit = Habit(title="Трекер", start_date=today, current_cycle=2, target_days=30)
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        response = await client.post(f"/api/habits/{habit.id}/next-cycle")
        assert response.status_code == 303

        async with async_session() as fresh:
            row = (await fresh.execute(select(Habit).where(Habit.id == habit.id))).scalar_one()
        assert row.current_cycle == 2, "цикл перескочил через только что начавшийся"
        assert row.start_date == today

    @pytest.mark.asyncio
    async def test_two_presses_at_once_move_cycle_once(self, client, db):
        """Двойной сабмит формы: UPDATE с условием не даёт увести цикл на два.

        Старт нового цикла при этом уходит на день после конца окна, поэтому
        второй запрос отсекается и условием на номер цикла, и guard-ом.
        """
        import asyncio

        today = date.today()
        habit = Habit(
            title="Трекер",
            start_date=today - timedelta(days=29),  # последний день окна — сегодня
            current_cycle=1,
            target_days=30,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        first, second = await asyncio.gather(
            client.post(f"/api/habits/{habit.id}/next-cycle"),
            client.post(f"/api/habits/{habit.id}/next-cycle"),
        )
        assert first.status_code == 303
        assert second.status_code == 303

        async with async_session() as fresh:
            row = (await fresh.execute(select(Habit).where(Habit.id == habit.id))).scalar_one()
        assert row.current_cycle == 2, "два нажатия увели цикл на два"
        assert row.start_date == today + timedelta(days=1)


class TestMonthlyCycles:
    """Непрерывный трекер: цикл — календарный месяц, номер растёт сам."""

    def test_cycle_number_grows_with_calendar(self):
        habit = Habit(
            title="Витамины",
            start_date=date(2026, 10, 8),
            cycle_mode=CYCLE_MODE_MONTHLY,
            current_cycle=1,
        )
        assert monthly_cycle_number(habit, date(2026, 10, 8)) == 1
        assert monthly_cycle_number(habit, date(2026, 10, 31)) == 1
        assert monthly_cycle_number(habit, date(2026, 11, 1)) == 2
        assert monthly_cycle_number(habit, date(2027, 1, 15)) == 4

        # Месяц без захода в планер не сдвигает нумерацию: цикл считается по
        # календарю, а не по числу нажатий.
        assert monthly_cycle_number(habit, date(2026, 12, 31)) == 3
        assert current_cycle_number(habit, date(2026, 12, 31)) == 3

    def test_first_cycle_is_clipped_to_start_day(self):
        """Первый цикл — от дня заведения до конца месяца, дальше месяцы целиком."""
        habit = Habit(
            title="Витамины",
            start_date=date(2026, 10, 8),
            cycle_mode=CYCLE_MODE_MONTHLY,
            current_cycle=1,
        )
        assert monthly_cycle_window(habit, 1, date(2026, 10, 8)) == (
            date(2026, 10, 8),
            date(2026, 10, 31),
        )
        assert monthly_cycle_window(habit, 2, date(2026, 11, 1)) == (
            date(2026, 11, 1),
            date(2026, 11, 30),
        )

    def test_short_month_does_not_shift_the_next_cycle(self):
        """Февраль не съедает март: циклы привязаны к числам календаря."""
        habit = Habit(
            title="Витамины",
            start_date=date(2027, 1, 15),
            cycle_mode=CYCLE_MODE_MONTHLY,
            current_cycle=1,
        )
        assert monthly_cycle_window(habit, 2, date(2027, 2, 1)) == (
            date(2027, 2, 1),
            date(2027, 2, 28),
        )
        assert monthly_cycle_window(habit, 3, date(2027, 3, 1)) == (
            date(2027, 3, 1),
            date(2027, 3, 31),
        )

    def test_grid_shows_the_current_month_not_the_start_interval(self):
        """Через два месяца трекер рисует свой месяц, а не 30 дней от старта."""
        habit = Habit(
            title="Витамины",
            start_date=date(2027, 1, 15),
            cycle_mode=CYCLE_MODE_MONTHLY,
            current_cycle=1,
        )
        grid = build_habit_cycle_grid(habit, date(2027, 3, 5))
        assert grid["dates"][0] == date(2027, 3, 1)
        assert grid["dates"][-1] == date(2027, 3, 31)
        assert grid["target_days"] == 31
        assert grid["start_weekday"] == date(2027, 3, 1).weekday()

    def test_history_shows_months_and_keeps_marks_in_their_cycle(self):
        habit = Habit(
            title="Витамины",
            start_date=date(2026, 10, 8),
            cycle_mode=CYCLE_MODE_MONTHLY,
            current_cycle=1,
        )
        logs = [
            HabitLog(habit_id=1, cycle_number=1, date=date(2026, 10, 9)),
            HabitLog(habit_id=1, cycle_number=2, date=date(2026, 11, 3)),
        ]
        cycles = build_habit_history_cycles(habit, logs, date(2026, 11, 10))

        assert len(cycles) == 2
        current, past = cycles[0], cycles[1]
        assert current["cycle_number"] == 2
        assert current["is_current"] is True
        assert (current["dates"][0], current["dates"][-1]) == (
            date(2026, 11, 1),
            date(2026, 11, 30),
        )
        assert current["window_label"] == "01.11 – 30.11"
        assert past["cycle_number"] == 1
        assert (past["dates"][0], past["dates"][-1]) == (
            date(2026, 10, 8),
            date(2026, 10, 31),
        )
        assert past["logs"] == {"2026-10-09"}
        assert past["progress"] == 1


class TestHabitCreateModes:
    """Создание трекера: старт всегда сегодня, режим цикла выбирается."""

    @pytest.mark.asyncio
    async def test_days_tracker_starts_today_and_runs_n_days(self, client, db):
        today = date.today()
        response = await client.post(
            "/api/habits/create",
            data={"title": "21 день без сахара", "cycle_mode": "days", "target_days": "21"},
        )
        assert response.status_code == 303

        habit = (
            await db.execute(select(Habit).where(Habit.title == "21 день без сахара"))
        ).scalar_one()
        assert habit.cycle_mode == CYCLE_MODE_DAYS
        assert habit.start_date == today, "старт — всегда день создания"
        assert habit.target_days == 21

        grid = build_habit_cycle_grid(habit, today)
        assert len(grid["dates"]) == 21
        assert grid["dates"][0] == today
        assert grid["end"] == today + timedelta(days=20)

    @pytest.mark.asyncio
    async def test_target_days_is_clamped_to_sane_bounds(self, client, db):
        """0 и 10 000 дней в форме не должны доехать до базы."""
        await client.post(
            "/api/habits/create",
            data={"title": "Ноль дней", "cycle_mode": "days", "target_days": "0"},
        )
        await client.post(
            "/api/habits/create",
            data={"title": "Тысяча лет", "cycle_mode": "days", "target_days": "10000"},
        )
        zero = (await db.execute(select(Habit).where(Habit.title == "Ноль дней"))).scalar_one()
        huge = (await db.execute(select(Habit).where(Habit.title == "Тысяча лет"))).scalar_one()
        assert zero.target_days == 1
        assert huge.target_days == 366

    @pytest.mark.asyncio
    async def test_monthly_tracker_needs_no_number_and_covers_its_month(self, client, db):
        """Пустое число у «непрерывного» — не 422, а обычный месячный трекер."""
        response = await client.post(
            "/api/habits/create",
            data={"title": "Месячный", "cycle_mode": "monthly", "target_days": ""},
        )
        assert response.status_code == 303

        today = date.today()
        habit = (
            await db.execute(select(Habit).where(Habit.title == "Месячный"))
        ).scalar_one()
        assert habit.cycle_mode == CYCLE_MODE_MONTHLY
        assert habit.start_date == today

        grid = build_habit_cycle_grid(habit, today)
        _, last = month_bounds(today)
        assert grid["dates"][0] == today
        assert grid["dates"][-1] == last
        # Первый цикл считается от дня заведения до конца месяца, а не месяц
        # целиком: дни до создания трекера в сетку не попадают.
        assert grid["target_days"] == (last - today).days + 1

    @pytest.mark.asyncio
    async def test_unknown_mode_falls_back_to_days(self, client, db):
        """Мусор в cycle_mode не должен создавать трекер без цикла."""
        response = await client.post(
            "/api/habits/create",
            data={"title": "Что-то странное", "cycle_mode": "weekly", "target_days": "14"},
        )
        assert response.status_code == 303
        habit = (
            await db.execute(select(Habit).where(Habit.title == "Что-то странное"))
        ).scalar_one()
        assert habit.cycle_mode == CYCLE_MODE_DAYS
        assert habit.target_days == 14


class TestMonthlyTrackerAPI:
    """Отметки и перевод цикла у непрерывного трекера."""

    @pytest.mark.asyncio
    async def test_mark_lands_in_current_month_cycle(self, client, db):
        """Трекер живёт третий месяц — отметка идёт в цикл 3, не в первый."""
        today = date.today()
        start = shift_month(today, -2)
        habit = Habit(
            title="Витамины",
            start_date=start,
            cycle_mode=CYCLE_MODE_MONTHLY,
            current_cycle=1,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        response = await client.post(
            "/api/habits/toggle",
            json={"habit_id": habit.id, "date": today.isoformat()},
        )
        assert response.status_code == 200
        assert response.json()["action"] == "added"

        async with async_session() as fresh:
            cycles = (
                await fresh.execute(
                    select(HabitLog.cycle_number).where(HabitLog.habit_id == habit.id)
                )
            ).scalars().all()
        assert list(cycles) == [3], "отметка ушла не в цикл текущего месяца"

        await db.refresh(habit)
        assert habit.current_cycle == 3, "сохранённый номер цикла отстал от календаря"

    @pytest.mark.asyncio
    async def test_day_outside_current_month_is_refused(self, client, db):
        """День прошлого месяца в новый цикл не записывается."""
        today = date.today()
        habit = Habit(
            title="Витамины",
            start_date=today,
            cycle_mode=CYCLE_MODE_MONTHLY,
            current_cycle=1,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        outside = month_bounds(today)[0] - timedelta(days=1)
        response = await client.post(
            "/api/habits/toggle",
            json={"habit_id": habit.id, "date": outside.isoformat()},
        )
        assert response.status_code == 409
        assert "не входит в текущий цикл" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_next_cycle_button_does_not_move_monthly_tracker(self, client, db):
        """У непрерывного трекера переводить нечего: цикл меняется сам."""
        today = date.today()
        start = shift_month(today, -1)
        habit = Habit(
            title="Витамины",
            start_date=start,
            cycle_mode=CYCLE_MODE_MONTHLY,
            current_cycle=1,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        response = await client.post(f"/api/habits/{habit.id}/next-cycle")
        assert response.status_code == 303

        await db.refresh(habit)
        assert habit.start_date == start, "день заведения трекера сдвинулся"
        assert habit.current_cycle == 1, "цикл ушёл вперёд от нажатия кнопки"

    @pytest.mark.asyncio
    async def test_days_tracker_keeps_manual_window(self, client, db):
        """Режим «на N дней»: окно ровно N дней и кнопка переводит цикл."""
        today = date.today()
        habit = Habit(
            title="21 день",
            start_date=today - timedelta(days=21),
            target_days=21,
            cycle_mode=CYCLE_MODE_DAYS,
            current_cycle=1,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        start, end = habit_cycle_window(habit, current_cycle_number(habit, today), today)
        assert (end - start).days == 20, "это не 21-дневный цикл"
        assert end < today, "цикл должен быть уже закончен"

        response = await client.post(f"/api/habits/{habit.id}/next-cycle")
        assert response.status_code == 303
        await db.refresh(habit)
        assert habit.current_cycle == 2
        assert habit.start_date == today, "новый цикл на 21 день начинается сразу после окна"


class TestTrackerCardRendering:
    """Дашборд: форма создания и карточка трекера говорят про режим правду."""

    @pytest.mark.asyncio
    async def test_dashboard_shows_named_length_and_no_next_button_for_monthly(self, client, db):
        today = date.today()
        days_habit = Habit(
            title="Спринт 21",
            start_date=today - timedelta(days=21),
            target_days=21,
            cycle_mode=CYCLE_MODE_DAYS,
            current_cycle=1,
        )
        monthly_habit = Habit(
            title="Витамины",
            start_date=today,
            cycle_mode=CYCLE_MODE_MONTHLY,
            current_cycle=1,
        )
        db.add_all([days_habit, monthly_habit])
        await db.commit()

        html = (await client.get("/")).text

        # Форма создания: режим и число дней вместо поля с датой (старт — сегодня).
        assert 'name="cycle_mode"' in html
        assert 'name="target_days"' in html
        assert 'name="start_date"' not in html
        assert "Непрерывный — циклы по календарным месяцам" in html

        # Карточка «на 21 день»: закончившийся цикл предлагает продлить.
        assert "21 день" in html
        assert "Продлить" in html
        assert "Цикл закончен" in html
        assert "Начать новый цикл на 21 день?" in html

        # Непрерывный: своя подпись и никакой кнопки перевода цикла.
        assert "по месяцам" in html
        assert f"/api/habits/{monthly_habit.id}/next-cycle" not in html
        assert f"/api/habits/{days_habit.id}/next-cycle" in html


class TestHabitExtendWindow:
    """«Продлить» работает с последнего дня окна, а не когда угодно."""

    @pytest.mark.asyncio
    async def test_press_in_the_middle_of_the_cycle_does_nothing(self, client, db):
        """Раньше нажатие в середине цикла уводило старт в будущее."""
        today = date.today()
        habit = Habit(
            title="Середина",
            start_date=today - timedelta(days=5),
            target_days=21,
            cycle_mode=CYCLE_MODE_DAYS,
            current_cycle=1,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        response = await client.post(f"/api/habits/{habit.id}/next-cycle")
        assert response.status_code == 303

        await db.refresh(habit)
        assert habit.current_cycle == 1
        assert habit.start_date == today - timedelta(days=5)
        _, window_end = habit_cycle_window(habit, 1, today)
        assert today < window_end, "цикл ещё идёт — продлевать нечего"

    @pytest.mark.asyncio
    async def test_second_press_does_not_skip_the_fresh_cycle(self, client, db):
        """Второе нажатие подряд не уводит цикл в будущее (случай из ревью).

        Трекер из 21 дня, окно кончилось 17 дней назад: первое нажатие даёт
        новый цикл со дня после конца окна, второе — «почему сетка старая?» —
        раньше сдвигало старт ещё на 21 день вперёд, и трекер до этого дня
        нельзя было отметить.
        """
        today = date.today()
        habit = Habit(
            title="Позднее продление",
            start_date=today - timedelta(days=37),
            target_days=21,
            cycle_mode=CYCLE_MODE_DAYS,
            current_cycle=1,
        )
        db.add(habit)
        await db.commit()
        await db.refresh(habit)

        first = await client.post(f"/api/habits/{habit.id}/next-cycle")
        assert first.status_code == 303
        await db.refresh(habit)
        assert habit.current_cycle == 2
        assert habit.start_date == today - timedelta(days=16)

        second = await client.post(f"/api/habits/{habit.id}/next-cycle")
        assert second.status_code == 303
        await db.refresh(habit)
        assert habit.current_cycle == 2, "второе нажатие пропустило начатый цикл"
        assert habit.start_date == today - timedelta(days=16)

    @pytest.mark.asyncio
    async def test_create_ignores_start_date_from_request(self, client, db):
        """«Дата старта всегда сегодня» держит сервер, а не только форма."""
        response = await client.post(
            "/api/habits/create",
            data={
                "title": "Со стартом в будущем",
                "cycle_mode": "days",
                "target_days": "21",
                "start_date": "2027-01-01",
            },
        )
        assert response.status_code == 303
        habit = (
            await db.execute(select(Habit).where(Habit.title == "Со стартом в будущем"))
        ).scalar_one()
        assert habit.start_date == date.today(), "внешний вызов задал старт в будущем"

    @pytest.mark.asyncio
    async def test_card_shows_archive_word_and_hides_extend_inside_the_cycle(self, client, db):
        today = date.today()
        running = Habit(
            title="Идёт цикл",
            start_date=today - timedelta(days=3),
            target_days=21,
            cycle_mode=CYCLE_MODE_DAYS,
            current_cycle=1,
        )
        last_day = Habit(
            title="Последний день",
            start_date=today - timedelta(days=20),
            target_days=21,
            cycle_mode=CYCLE_MODE_DAYS,
            current_cycle=1,
        )
        db.add_all([running, last_day])
        await db.commit()

        html = (await client.get("/")).text
        assert "Архив" in html, "на карточке нет слова «Архив» — только иконка"
        # Середина цикла: продлевать нечего, кнопки на карточке нет.
        assert f"/api/habits/{running.id}/next-cycle" not in html
        # Последний день окна: кнопка есть, но цикл ещё не «закончен».
        assert f"/api/habits/{last_day.id}/next-cycle" in html
        assert "Начать следующие 21 день?" in html
