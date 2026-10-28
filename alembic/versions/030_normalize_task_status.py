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

Миграция переводит статусы в словарь приложения по таблице алиасов — тем же
словам, что и `normalize_status()` в коде. Таблица здесь ЛИТЕРАЛОМ, а не
импортом из `app.models.task`: историческая миграция не должна менять смысл
старых значений, если модель потом поправят. Тест сверяет эти две таблицы,
чтобы они не разъехались.

Для закрывающих алиасов сразу заполняется `completed_at` (если пусто): статус
«выполнена» без даты закрытия — это та же невидимая задача в знаменателе дня.
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

# Алиасы интеграций — ровно те же, что STATUS_ALIASES в app/models/task.py.
STATUS_ALIASES = {
    "new": "новая",
    "open": "новая",
    "todo": "новая",
    "pending": "новая",
    "in_progress": "в_работе",
    "in-progress": "в_работе",
    "doing": "в_работе",
    "done": "выполнена",
    "completed": "выполнена",
    "closed": "выполнена",
    "postponed": "отложена",
    "deferred": "отложена",
}

UNKNOWN_TO = "новая"


def upgrade() -> None:
    if not table_exists("tasks"):
        return
    bind = op.get_bind()
    fixed = 0

    # 1. Пустое значение — статус по умолчанию.
    result = bind.execute(
        sa.text(
            "UPDATE tasks SET status = :dst "
            "WHERE status IS NULL OR TRIM(status) = ''"
        ).bindparams(dst=UNKNOWN_TO)
    )
    fixed += result.rowcount

    # 2. Известные алиасы — каждый в свой статус (а не всё в «новая»).
    for source, target in STATUS_ALIASES.items():
        sql = "UPDATE tasks SET status = :dst"
        if target == "выполнена":
            sql += ", completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)"
        sql += " WHERE LOWER(TRIM(status)) = :src"
        result = bind.execute(sa.text(sql).bindparams(dst=target, src=source))
        fixed += result.rowcount

    # 3. Всё остальное — открытая задача, а не исчезнувшая из списков.
    result = bind.execute(
        sa.text("UPDATE tasks SET status = :dst WHERE status NOT IN :known").bindparams(
            sa.bindparam("dst", UNKNOWN_TO),
            sa.bindparam("known", expanding=True, value=list(KNOWN_STATUSES)),
        )
    )
    fixed += result.rowcount

    print(f"[030_normalize_task_status] исправлено строк: {fixed}")


def downgrade() -> None:
    """Откат не нужен: исходное чужое значение не восстанавливаем.

    До фикса задача с ним была невидимой в списках — возвращать это состояние
    бессмысленно.
    """
