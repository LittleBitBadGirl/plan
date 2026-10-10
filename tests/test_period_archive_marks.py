"""Архив отметок цикла: пометка старого приложения (миграция 028) и рендер блока.

Проверяем две вещи, которые критика справедливо назвала незакрытыми:
1. правило пометки — 20 отметок мая–августа 2026 уходят в архив, сентябрьские 4
   остаются текущими, повторный прогон ничего не меняет, откат возвращает как было;
2. блок «Архив — старое приложение» действительно рисуется и не показывает
   литерал None, когда в архиве всего один цикл (длину цикла считать не от чего).
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "m028_period_archive_marks",
        ROOT / "alembic" / "versions" / "028_period_archive_marks.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Отметки боевой базы на 09.10.2026: 20 старых (старое приложение) + 4 текущих (сентябрь).
LIVE_ROWS = [
    (1, "2026-05-23"), (4, "2026-05-24"), (3, "2026-05-25"),
    (5, "2026-06-22"), (6, "2026-06-23"), (7, "2026-06-24"),
    (8, "2026-06-25"), (9, "2026-06-26"),
    (10, "2026-07-20"), (11, "2026-07-21"), (12, "2026-07-22"),
    (13, "2026-07-23"), (14, "2026-07-24"), (15, "2026-07-25"),
    (16, "2026-07-26"),
    (17, "2026-08-19"), (18, "2026-08-20"), (19, "2026-08-21"),
    (20, "2026-08-22"), (21, "2026-08-23"),
    (22, "2026-09-27"), (23, "2026-09-28"), (24, "2026-09-29"),
    (25, "2026-09-30"),
]


def test_mark_sql_marks_old_app_and_keeps_current():
    migration = _migration_module()
    conn = sqlite3.connect(":memory:")
    conn.execute(
        'CREATE TABLE period_entries (id INTEGER PRIMARY KEY, "date" TEXT, is_archival INTEGER)'
    )
    conn.executemany(
        'INSERT INTO period_entries (id, "date", is_archival) VALUES (?, ?, NULL)', LIVE_ROWS
    )

    conn.executescript(migration.NORMALIZE_NULLS_SQL + ";")
    conn.executescript(migration.MARK_ARCHIVAL_SQL + ";")

    archived = conn.execute(
        "SELECT count(*) FROM period_entries WHERE is_archival = 1"
    ).fetchone()[0]
    current = conn.execute(
        "SELECT count(*) FROM period_entries WHERE is_archival = 0"
    ).fetchone()[0]
    assert archived == 20, "в архив должны уйти все отметки до 01.09.2026"
    assert current == 4, "сентябрьские отметки остаются текущими"
    assert (
        conn.execute(
            "SELECT count(*) FROM period_entries WHERE is_archival IS NULL"
        ).fetchone()[0]
        == 0
    ), "пустых значений в колонке остаться не должно"

    # Идемпотентность: перезапуск контейнера повторяет миграцию — ничего не меняется.
    conn.executescript(migration.MARK_ARCHIVAL_SQL + ";")
    assert (
        conn.execute(
            "SELECT count(*) FROM period_entries WHERE is_archival = 1"
        ).fetchone()[0]
        == 20
    )

    # Откат возвращает всё как было.
    conn.executescript(migration.UNMARK_ARCHIVAL_SQL + ";")
    assert (
        conn.execute(
            "SELECT count(*) FROM period_entries WHERE is_archival = 1"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.asyncio
async def test_cycle_page_renders_archive_block_without_none(client):
    """Один цикл в архиве: блок рисуется и не показывает None вместо цифр."""
    from datetime import date

    from sqlalchemy import delete

    from app.db.database import async_session
    from app.models.period_entry import PeriodEntry

    async with async_session() as db:
        await db.execute(delete(PeriodEntry))
        for day in (22, 23):
            db.add(
                PeriodEntry(
                    date=date(2026, 6, day),
                    has_pain=False,
                    is_spotting=False,
                    is_archival=True,
                )
            )
        await db.commit()

    try:
        response = await client.get("/cycle")
        html = response.text
        assert response.status_code == 200
        assert "Архив — старое приложение" in html, "блок архива не отрисовался"
        block = html.split("Архив — старое приложение", 1)[1][:2000]
        assert "None" not in block, "в блоке архива всплыл литерал None"
    finally:
        async with async_session() as db:
            await db.execute(delete(PeriodEntry))
            await db.commit()
