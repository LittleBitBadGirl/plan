"""Тесты логики трекеров привычек."""
from datetime import date, timedelta

import pytest

from app.api.habits import compute_next_cycle_start
from app.models.habit import Habit


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
