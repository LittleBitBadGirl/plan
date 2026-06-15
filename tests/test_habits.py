"""Тесты логики трекеров привычек."""
from datetime import date, timedelta

import pytest

from app.api.habits import build_habit_history_cycles, compute_cycle_start_dates, compute_next_cycle_start
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

    def test_starts_today_if_restarted_early(self):
        today = date(2025, 5, 25)
        habit = Habit(
            title="Уход",
            start_date=date(2025, 5, 1),
            target_days=30,
            current_cycle=1,
        )
        assert compute_next_cycle_start(habit, today) == today

    def test_on_time_restart(self):
        today = date(2025, 5, 31)
        habit = Habit(
            title="Уход",
            start_date=date(2025, 5, 1),
            target_days=30,
            current_cycle=1,
        )
        assert compute_next_cycle_start(habit, today) == date(2025, 5, 31)


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
