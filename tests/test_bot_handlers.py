"""Тесты логики Telegram-бота (без aiogram runtime)."""
import re
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.bot.task_logic import (
    detect_intent,
    parse_time_from_title,
    create_task_from_text,
    backlog_tasks_filter,
    today_open_tasks_filter,
)
from app.models.task import Task

_DONE_SUFFIX = re.compile(r"\s*[-–—]\s*(сделала?|готово)\s*$", re.IGNORECASE)
_DONE_PREFIX = re.compile(r"^\s*(сделала?|готово)\s*[-–—:]\s*", re.IGNORECASE)
_BULLET = re.compile(r"^\s*[-•*]\s+", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)


def _intent(text: str) -> dict:
    return detect_intent(
        text,
        done_suffix=_DONE_SUFFIX,
        done_prefix=_DONE_PREFIX,
        bullet=_BULLET,
        numbered=_NUMBERED,
    )


class TestBotIntent:
    def test_backlog_prefix(self):
        assert _intent("бэклог: купить фильтр")["intent"] == "backlog_add"
        assert _intent("потом — написать Антону")["intent"] == "backlog_add"

    def test_bulk_goes_to_backlog(self):
        intent = _intent("- задача 1\n- задача 2")
        assert intent["intent"] == "bulk_add"
        assert intent["target"] == "backlog"

    def test_single_add_today(self):
        intent = _intent("позвонить маме")
        assert intent["intent"] == "add"
        assert intent["target"] == "today"

    def test_complete_suffix(self):
        intent = _intent("отчёт — сделала")
        assert intent["intent"] == "complete"
        assert intent["task_name"] == "отчёт"


class TestBotTaskLogic:
    def test_parse_time_from_title(self):
        title, due_time = parse_time_from_title("9:30 Созвон с клиентом")
        assert title == "Созвон с клиентом"
        assert due_time.strftime("%H:%M") == "09:30"

    @pytest.mark.asyncio
    async def test_create_backlog_task(self, db):
        with patch("app.bot.task_logic.ai_service.categorize", new_callable=AsyncMock) as mock_ai:
            mock_ai.return_value = {"category_id": None, "tags": [], "due_date": None}
            task, summary = await create_task_from_text(db, "бэклог: идея", target="backlog")

        assert task.due_date is None
        assert task.title == "идея"
        assert "бэклог" in summary.lower()

        result = await db.execute(select(Task).where(Task.id == task.id))
        saved = result.scalar_one()
        assert saved.due_date is None

    @pytest.mark.asyncio
    async def test_create_today_with_time(self, db):
        with patch("app.bot.task_logic.ai_service.categorize", new_callable=AsyncMock) as mock_ai:
            mock_ai.return_value = {"category_id": None, "tags": [], "due_date": None}
            task, _ = await create_task_from_text(db, "14:00 Встреча")

        assert task.due_date == date.today()
        assert task.due_time.strftime("%H:%M") == "14:00"
        assert task.title == "Встреча"

    def test_today_filter_has_core_constraints(self):
        assert len(today_open_tasks_filter()) >= 5

    def test_backlog_filter_has_no_date(self):
        assert len(backlog_tasks_filter()) >= 4
