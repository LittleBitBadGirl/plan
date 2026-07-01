"""Тесты парсинга даты в форме задачи."""
from datetime import date

import pytest
from fastapi import HTTPException

from app.web.routes.tasks import _parse_due_date_form, _parse_ddmm


class TestParseDueDateForm:
    def test_empty(self):
        assert _parse_due_date_form("") is None
        assert _parse_due_date_form("   ") is None

    def test_iso_format(self):
        assert _parse_due_date_form("2025-06-12") == date(2025, 6, 12)

    def test_ddmm_dot(self):
        today = date.today()
        assert _parse_due_date_form("12.06") == date(today.year, 6, 12)

    def test_ddmm_digits(self):
        today = date.today()
        assert _parse_due_date_form("1206") == date(today.year, 6, 12)

    def test_invalid_raises(self):
        with pytest.raises(HTTPException) as exc:
            _parse_due_date_form("99.99")
        assert exc.value.status_code == 400


class TestParseDdmm:
    def test_dot_format(self):
        assert _parse_ddmm("06.06") == (6, 6)

    def test_digit_format(self):
        assert _parse_ddmm("0606") == (6, 6)
