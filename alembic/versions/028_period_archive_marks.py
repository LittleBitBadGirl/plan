"""Архив отметок цикла: помечаем отметки старого приложения.

Планер ведёт отметки с 27.09.2026; всё, что раньше, перенесено из старого
приложения и не должно входить в средние, текущий цикл и календарь дашборда —
такие отметки получают is_archival=1 и показываются отдельным блоком «Архив»
на странице /cycle.

Колонка заводилась миграцией 027_period_archive; здесь только данные.
Заодно пустое значение приводим к 0: пока в колонке возможен NULL, одно место
читает его как «не архив», а другое (SQL-сравнение с False) выбрасывает строку
целиком — это и была рассинхронизация карточки дашборда и средних.

Revision ID: 028_period_archive_marks
Revises: 027_period_archive
Create Date: 2026-10-09

"""
from typing import Sequence, Union

from alembic import op

revision: str = "028_period_archive_marks"
down_revision: Union[str, None] = "027_period_archive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Первая отметка в этом Планере — 27.09.2026: всё раньше считаем старым приложением.
NEW_APP_FROM = "2026-09-01"

NORMALIZE_NULLS_SQL = (
    "UPDATE period_entries SET is_archival = 0 WHERE is_archival IS NULL"
)
MARK_ARCHIVAL_SQL = (
    'UPDATE period_entries SET is_archival = 1 '
    'WHERE "date" < \'{d}\' AND (is_archival IS NULL OR is_archival = 0)'.format(
        d=NEW_APP_FROM
    )
)
UNMARK_ARCHIVAL_SQL = (
    'UPDATE period_entries SET is_archival = 0 WHERE "date" < \'{d}\''.format(
        d=NEW_APP_FROM
    )
)


def upgrade() -> None:
    op.execute(NORMALIZE_NULLS_SQL)
    op.execute(MARK_ARCHIVAL_SQL)


def downgrade() -> None:
    op.execute(UNMARK_ARCHIVAL_SQL)
