"""Мероприятия: файлы билетов.

Revision ID: 029_event_tickets
Revises: 028_period_archive_marks
Create Date: 2026-10-12

Вера 11.10.2026: «в мероприятия добавить загрузку билетов и чтоб оттуда можно
было бы их забрать — у меня 25 числа хоккей и есть билет». Прежде чем идти в
мероприятие, хочется открыть сам билет, а не искать его в почте.

Колонка хранит список словарей [{"path": ..., "name": ...}]: путь в закрытой
папке tickets/ и исходное имя файла — по нему билет узнаётся в списке.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import column_exists, table_exists

revision: str = "029_event_tickets"
down_revision: Union[str, None] = "028_period_archive_marks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("events"):
        return
    if not column_exists("events", "ticket_files"):
        op.add_column("events", sa.Column("ticket_files", sa.JSON(), nullable=True))


def downgrade() -> None:
    if table_exists("events") and column_exists("events", "ticket_files"):
        op.drop_column("events", "ticket_files")
