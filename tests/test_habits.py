"""Тесты логики трекеров привычек."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.api.habits import (
    build_habit_cycle_grid,
    build_habit_history_cycles,
    compute_cycle_start_dates,
    compute_next_cycle_start,
    cycle_window,
)
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
