"""Мероприятия: день и время похода. Статус «не иду» больше не нужен.

Revision ID: 021_event_visit
Revises: 020_events
Create Date: 2026-09-30

«Иду» стало действием: нажатие сохраняет день и время сеанса (билет куплен),
из них собирается событие календаря с напоминанием за сутки. Поэтому у events
появляются visit_date и visit_time.

Заодно убираем состояние «не иду»: кнопка снята из интерфейса, а старые отметки
переводим в «без отметки» — иначе карточка показывала бы решение, которое больше
нельзя ни поставить, ни изменить.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import column_exists, table_exists

revision: str = "021_event_visit"
down_revision: Union[str, None] = "020_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("events"):
        return
    if not column_exists("events", "visit_date"):
        op.add_column("events", sa.Column("visit_date", sa.Date(), nullable=True))
    if not column_exists("events", "visit_time"):
        op.add_column("events", sa.Column("visit_time", sa.String(length=5), nullable=True))
    # «Не иду» из интерфейса убрано — следов решения не остаётся.
    op.execute("UPDATE events SET status = 'none' WHERE status = 'not_going'")
    # «Иду» теперь означает бронь с днём сеанса. Старые отметки без дня сбрасываем:
    # из них нельзя ни собрать событие календаря, ни показать «иду 5 окт», а
    # карточка иначе выглядела бы отмеченной и одновременно предлагала бронь.
    op.execute("UPDATE events SET status = 'none' WHERE status = 'going' AND visit_date IS NULL")


def downgrade() -> None:
    if not table_exists("events"):
        return
    if column_exists("events", "visit_time"):
        op.drop_column("events", "visit_time")
    if column_exists("events", "visit_date"):
        op.drop_column("events", "visit_date")
