"""Уникальность ключа импорта раздела «Читать».

Revision ID: 017_reading_import_unique
Revises: 016_reading_taxonomy_data
Create Date: 2026-09-23

Что делает: запрещает две записи чтения с одной парой imported_from +
external_id. Пара — это «откуда пришло» (favorites, read, instagram) и номер
исходного сообщения, поэтому уникальный индекс превращает повторный импорт
архива в безопасную операцию: вторая вставка той же записи падает, а не
плодит дубль.

Индекс частичный (WHERE imported_from IS NOT NULL AND external_id IS NOT
NULL): обычные записи, заведённые руками, в ограничение не попадают, потому
что у них этих полей нет.
"""
from alembic import op

revision = "017_reading_import_unique"
down_revision = "016_reading_taxonomy_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_shopping_items_import_key "
        "ON shopping_items (imported_from, external_id) "
        "WHERE imported_from IS NOT NULL AND external_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_shopping_items_import_key")
