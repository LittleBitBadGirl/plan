"""Задача, взятая на день: planned_for.

Revision ID: 022_task_planned_for
Revises: 021_event_visit
Create Date: 2026-10-07

День больше не собирается автоматически: утром Вера набирает минимум 5 задач
из бэклога, вечером незакрытое возвращается в бэклог. Признак «взято на день» —
новая колонка tasks.planned_for.

Поле due_date не трогаем: на нём висит генератор регулярных задач (вхождение
появляется в свой день) и счётчик переносов. Из интерфейса дату убираем
отдельно, схему не ломаем.

Бэкфилл намеренно НЕ делается: задачи, висевшие на сегодня по старому
ролловеру, остаются в бэклоге, а не попадают в день автоматически — иначе
первый день новой механики будет той же свалкой.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import column_exists, table_exists

revision: str = "022_task_planned_for"
down_revision: Union[str, None] = "021_event_visit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_tasks_planned_for"


def _index_exists(index_name: str) -> bool:
    bind = op.get_bind()
    return index_name in {i["name"] for i in sa.inspect(bind).get_indexes("tasks")}


def upgrade() -> None:
    if not table_exists("tasks"):
        return
    if not column_exists("tasks", "planned_for"):
        op.add_column("tasks", sa.Column("planned_for", sa.Date(), nullable=True))
    if not _index_exists(INDEX_NAME):
        op.create_index(INDEX_NAME, "tasks", ["planned_for"])


def downgrade() -> None:
    if not table_exists("tasks"):
        return
    if _index_exists(INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name="tasks")
    if column_exists("tasks", "planned_for"):
        op.drop_column("tasks", "planned_for")
