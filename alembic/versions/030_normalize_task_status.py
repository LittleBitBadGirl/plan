"""Задачи: чужие статусы → словарь приложения.

Revision ID: 030_normalize_task_status
Revises: 029_event_tickets
Create Date: 2026-10-28

Вера 28.10.2026: «убрала 1 задачу, а счётчик не уменьшился — осталось 2 задачи,
а на счётчике надо ещё 3 закрыть».

Причина — статус `open`. Его записала интеграция Hermes (source='hermes') в
обход ORM. Список дня фильтрует задачи по известным статусам
('новая'/'в_работе'), а счётчики «на день» и «прогресс» статус не смотрели:
задача считалась, но на экране её не было. В базе Веры таких строк было три,
одна из них (id 1617) стояла на 28.10.

Миграция приводит колонку к словарю: всё, что не входит в набор статусов
приложения, становится 'новая' (то есть открытая задача, а не исчезнувшая).
Пишем только явно перечисленные значения и печатаем rowcount, чтобы в логе
старта контейнера было видно, сколько строк поправилось.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import table_exists

revision: str = "030_normalize_task_status"
down_revision: Union[str, None] = "029_event_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Словарь статусов приложения: модель Task, веб-формы, бот, API.
KNOWN_STATUSES = ("новая", "в_работе", "выполнена", "отложена")


def upgrade() -> None:
    if not table_exists("tasks"):
        return
    result = op.get_bind().execute(
        sa.text(
            "UPDATE tasks SET status = 'новая' "
            "WHERE status IS NULL OR TRIM(status) = '' "
            "   OR status NOT IN :known"
        ).bindparams(sa.bindparam("known", expanding=True, value=list(KNOWN_STATUSES)))
    )
    print(f"[030_normalize_task_status] исправлено строк: {result.rowcount}")


def downgrade() -> None:
    """Откат не нужен: исходное чужое значение не восстанавливаем.

    До фикса задача с ним была невидимой в списках — возвращать это состояние
    бессмысленно.
    """
