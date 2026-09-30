"""Отметка дня уникальна в пределах цикла, а не привычки целиком.

Revision ID: 019_habit_log_cycle_unique
Revises: 018_reading_format_document
Create Date: 2026-09-29

Что было. Уникальность отметок в `habit_logs` ограничена парой
(habit_id, date) — так таблица была заведена в первой версии трекеров.
Когда у отметки появился номер цикла, в моделях ограничение сменилось на
(habit_id, date, cycle_number), но миграции на это не было: SQLite не меняет
табличное ограничение без пересборки таблицы, и в боевой базе осталось старое
`CONSTRAINT _habit_date_uc UNIQUE (habit_id, date)`.

Чем это выходило боком. Пока циклы шли подряд, ни один день не мог попасть в
два цикла, и всё работало. Стоило циклам нахлестнуться (старт нового цикла
совпадал с последним днём предыдущего), как отметка такого дня в новом цикле
падала на `UNIQUE constraint failed: habit_logs.habit_id, habit_logs.date`:
сервер отдавал 500, а дашборд молча гасил ошибку — в трекере «день не
отмечается».

Что делает миграция.
1. Пересобирает `habit_logs` с ограничением (habit_id, date, cycle_number) —
   так, как объявлено в модели.
2. Убирает уже накопившийся нахлёст в данных: если начало текущего цикла уже
   отмечено в предыдущем цикле, начало сдвигается на день после последней
   такой отметки. Отметки не удаляются и не переписываются.

Идемпотентность: повторный прогон ничего не меняет — таблица уже пересобрана
(в DDL стоит имя нового ограничения `_habit_date_cycle_uc`), а сдвигать
больше нечего. На свежей базе `create_all` создаёт таблицу сразу в новом виде,
и вся миграция — пустой проход.
"""

import re
from datetime import date, timedelta
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import column_exists, table_exists

revision: str = "019_habit_log_cycle_unique"
down_revision: Union[str, None] = "018_reading_format_document"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_UNIQUE = "unique (habit_id, date)"
NEW_UNIQUE_NAME = "_habit_date_cycle_uc"

NEW_TABLE_SQL = """
CREATE TABLE habit_logs (
    id INTEGER NOT NULL,
    habit_id INTEGER NOT NULL,
    cycle_number INTEGER,
    date DATE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT _habit_date_cycle_uc UNIQUE (habit_id, date, cycle_number),
    FOREIGN KEY(habit_id) REFERENCES habits (id) ON DELETE CASCADE
)
"""

LEGACY_TABLE_SQL = """
CREATE TABLE habit_logs (
    id INTEGER NOT NULL,
    habit_id INTEGER NOT NULL,
    date DATE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    cycle_number INTEGER DEFAULT 1,
    PRIMARY KEY (id),
    CONSTRAINT _habit_date_uc UNIQUE (habit_id, date),
    FOREIGN KEY(habit_id) REFERENCES habits (id) ON DELETE CASCADE
)
"""

# Отметки предыдущего цикла, лежащие в окне текущего: именно они делают день
# «отмеченным в двух циклах». start_date текущего цикла сдвигается на день
# после самой поздней такой отметки.
OVERLAP_QUERY = """
SELECT h.id, h.start_date, MAX(l.date) AS last_overlap
FROM habits h
JOIN habit_logs l ON l.habit_id = h.id AND l.cycle_number = h.current_cycle - 1
WHERE h.current_cycle > 1
  AND h.start_date IS NOT NULL
  AND l.date >= h.start_date
GROUP BY h.id
ORDER BY h.id
"""


def _normalized_ddl() -> str:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'habit_logs'")
    ).fetchone()
    return re.sub(r"\s+", " ", (row[0] or "").lower()) if row else ""


def _has_legacy_unique(ddl: str) -> bool:
    return LEGACY_UNIQUE in ddl and "unique (habit_id, date, cycle_number)" not in ddl


def _rebuild_table(new_table_sql: str, into_cycle_number: bool) -> int:
    """Пересобрать habit_logs: новая таблица, перенос строк, старые индексы заново."""
    bind = op.get_bind()
    before = bind.execute(sa.text("SELECT COUNT(*) FROM habit_logs")).scalar_one()

    bind.execute(sa.text("ALTER TABLE habit_logs RENAME TO habit_logs_legacy"))
    bind.execute(sa.text(new_table_sql))

    if into_cycle_number:
        bind.execute(
            sa.text(
                "INSERT INTO habit_logs (id, habit_id, cycle_number, date, created_at) "
                "SELECT id, habit_id, cycle_number, date, created_at FROM habit_logs_legacy"
            )
        )
    else:
        bind.execute(
            sa.text(
                "INSERT INTO habit_logs (id, habit_id, cycle_number, date, created_at) "
                "SELECT id, habit_id, 1, date, created_at FROM habit_logs_legacy"
            )
        )

    # Индексы носили те же имена: они ушли вместе со старой таблицей, иначе
    # CREATE INDEX падал бы на «index already exists».
    bind.execute(sa.text("DROP TABLE habit_logs_legacy"))
    bind.execute(sa.text("CREATE INDEX ix_habit_logs_id ON habit_logs (id)"))
    bind.execute(
        sa.text("CREATE INDEX ix_habit_logs_habit_cycle ON habit_logs (habit_id, cycle_number)")
    )

    after = bind.execute(sa.text("SELECT COUNT(*) FROM habit_logs")).scalar_one()
    if after != before:
        raise RuntimeError(f"019: отметок было {before}, стало {after} — перенос потерял строки")
    return after


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _remove_overlaps() -> None:
    """Сдвинуть старт текущего цикла за последнюю отметку предыдущего."""
    bind = op.get_bind()
    rows = bind.execute(sa.text(OVERLAP_QUERY)).fetchall()
    for habit_id, old_start, last_overlap in rows:
        overlap_day = _as_date(last_overlap)
        new_start = overlap_day + timedelta(days=1)
        result = bind.execute(
            sa.text("UPDATE habits SET start_date = :start WHERE id = :habit_id"),
            {"start": new_start.isoformat(), "habit_id": habit_id},
        )
        if result.rowcount != 1:
            raise RuntimeError(f"019: привычка {habit_id} — обновлено {result.rowcount} строк")
        print(
            "019: привычка %s — день %s был отмечен в двух циклах, старт текущего "
            "цикла перенесён с %s на %s"
            % (habit_id, overlap_day.isoformat(), _as_date(old_start).isoformat(), new_start.isoformat())
        )


def _report_marks_outside_window() -> None:
    """Отметки, оставшиеся за окном своего цикла. Не удаляем — только сообщаем."""
    bind = op.get_bind()
    stranded = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM habit_logs l JOIN habits h ON h.id = l.habit_id "
            "WHERE l.cycle_number = h.current_cycle AND h.start_date IS NOT NULL "
            "AND l.date < h.start_date"
        )
    ).scalar_one()
    if stranded:
        print(f"019: отметок раньше начала своего цикла — {stranded} (оставлены как есть)")


def upgrade() -> None:
    if not table_exists("habit_logs"):
        return

    ddl = _normalized_ddl()
    if _has_legacy_unique(ddl):
        has_cycle = column_exists("habit_logs", "cycle_number")
        rows = _rebuild_table(NEW_TABLE_SQL, into_cycle_number=has_cycle)
        print(
            "019: habit_logs пересобрана — уникальность (habit_id, date, cycle_number), "
            "отметок %s" % rows
        )

    _remove_overlaps()
    _report_marks_outside_window()


def downgrade() -> None:
    """Возвращает старое ограничение (habit_id, date), если это возможно.

    Если один и тот же день уже отмечен в двух циклах, старое ограничение
    накрыло бы часть данных, поэтому пересборка не делается: сначала нужно
    развести такие отметки руками.
    """
    if not table_exists("habit_logs"):
        return

    bind = op.get_bind()
    collisions = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM (SELECT habit_id, date FROM habit_logs "
            "GROUP BY habit_id, date HAVING COUNT(*) > 1)"
        )
    ).scalar_one()
    if collisions:
        print(
            f"019: откат пропущен — дней, отмеченных в двух циклах: {collisions}. "
            "Старое ограничение (habit_id, date) их не примет"
        )
        return

    if _has_legacy_unique(_normalized_ddl()):
        return
    rows = _rebuild_table(LEGACY_TABLE_SQL, into_cycle_number=True)
    print(f"019: habit_logs возвращена к ограничению (habit_id, date), отметок {rows}")
