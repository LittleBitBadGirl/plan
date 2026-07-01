"""Тесты фильтрации календарных событий."""
from datetime import datetime

import pytest

from app.services.calendar_filter_service import (
    calendar_included,
    event_planner_visible,
    reload_calendar_sync_config,
    title_excluded,
)


@pytest.fixture(autouse=True)
def _fresh_config():
    reload_calendar_sync_config()
    yield
    reload_calendar_sync_config()


def test_calendar_include_exclude(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "google_calendar_sync_enabled", False)
    monkeypatch.setattr(settings, "google_calendar_ical_url", "")
    assert calendar_included("встречи внутри группы")
    assert calendar_included("личные дела")
    assert not calendar_included("не обязательные")


def test_title_exact_exclude_frontend_lead():
    assert title_excluded("Лидирование команды Frontend") is not None


def test_event_visible_blocks_frontend():
    visible, reason = event_planner_visible(
        "Лидирование команды Frontend",
        datetime(2026, 6, 5, 14, 30),
        "встречи внутри группы",
    )
    assert visible is False
    assert reason.startswith("exact:")


def test_weekend_work_calendar_hidden():
    visible, reason = event_planner_visible(
        "ДАЛЕЕ // Планирование",
        datetime(2026, 6, 7, 11, 0),  # Sunday
        "календарь Далее",
    )
    assert visible is False
    assert reason == "weekend_work_calendar"


def test_weekend_personal_calendar_ok():
    visible, _ = event_planner_visible(
        "Стоматолог",
        datetime(2026, 6, 7, 10, 0),
        "личные дела",
    )
    assert visible is True
