"""Статистика: рабочие дни и выходные."""
from datetime import date

from app.web.deps import count_workdays_between, is_weekend, rolling_week_windows


def test_is_weekend():
    assert is_weekend(date(2026, 6, 6)) is True   # суббота
    assert is_weekend(date(2026, 6, 7)) is True   # воскресенье
    assert is_weekend(date(2026, 6, 4)) is False  # четверг


def test_count_workdays_one_week():
    # пн 2 — вс 8 июня 2026
    start = date(2026, 6, 2)
    end = date(2026, 6, 8)
    assert count_workdays_between(start, end) == 5


def test_rolling_week_windows_non_overlapping():
    today = date(2026, 6, 4)
    (cur_start, cur_end), (prev_start, prev_end) = rolling_week_windows(today)
    assert cur_start == date(2026, 5, 28)
    assert cur_end == today
    assert prev_end == date(2026, 5, 27)
    assert prev_start == date(2026, 5, 20)
    assert prev_end < cur_start
