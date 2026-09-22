"""Категории и теги для раздела «Читать».

Revision ID: 015_reading_taxonomy
Revises: 014_task_is_archived
Create Date: 2026-09-23

Что делает:
1. Добавляет записи чтения (shopping_items, item_kind='reading') три поля:
   category_id (категория темы), reading_format (книга, статья, видео и так
   далее), imported_from + external_id (откуда запись пришла при импорте
   архива Telegram и номер исходного сообщения — ключ идемпотентности,
   чтобы повторный импорт не плодил дубли).
2. Создаёт таблицы tags и shopping_item_tags: тег отдельной сущностью,
   иначе фильтр по тегам превращается в LIKE по строке.
3. Заводит 11 категорий чтения (type='reading') в общую таблицу categories.
   Остальные выборки категорий в проекте фильтруют по type='task' или
   type='finance', поэтому в задачи и финансы эти категории не попадают.

Про FK на categories.id: колонка добавлена как обычный Integer. В SQLite
внешние ключи по умолчанию не проверяются, а batch_alter_table с FK на
живой базе пересобирает таблицу целиком — лишний риск ради ограничения,
которое всё равно не работает. Связь держит ORM (ShoppingItem.category).

Про формат по умолчанию: NULL означает «формат не определён». При импорте
архивов такие записи получают тег «разобрать» и разбираются вручную.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import add_column_if_missing, column_exists, table_exists

revision: str = "015_reading_taxonomy"
down_revision: Union[str, None] = "014_task_is_archived"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Порядок важен: он задаёт порядок «полок» на странице чтения.
READING_CATEGORIES = [
    "Менеджмент и команда",
    "Продукт и процессы",
    "Карьера",
    "ИИ и агенты",
    "Коммуникация и речь",
    "Финансы и инвестиции",
    "Мышление и психология",
    "Художественная литература",
    "Рабочие материалы",
    "Досуг",
    "Путешествия",
]


def upgrade() -> None:
    if table_exists("shopping_items"):
        add_column_if_missing("shopping_items", sa.Column("category_id", sa.Integer, nullable=True))
        add_column_if_missing("shopping_items", sa.Column("reading_format", sa.String(30), nullable=True))
        add_column_if_missing("shopping_items", sa.Column("imported_from", sa.String(30), nullable=True))
        add_column_if_missing("shopping_items", sa.Column("external_id", sa.String(60), nullable=True))

        indexes = {row[1] for row in op.get_bind().execute(sa.text("PRAGMA index_list(shopping_items)"))}
        if "ix_shopping_items_category_id" not in indexes:
            op.create_index("ix_shopping_items_category_id", "shopping_items", ["category_id"])
        if "ix_shopping_items_reading_format" not in indexes:
            op.create_index("ix_shopping_items_reading_format", "shopping_items", ["reading_format"])
        if "ix_shopping_items_imported_from" not in indexes:
            op.create_index("ix_shopping_items_imported_from", "shopping_items", ["imported_from"])
        if "ix_shopping_items_external_id" not in indexes:
            op.create_index("ix_shopping_items_external_id", "shopping_items", ["external_id"])

    if not table_exists("tags"):
        op.create_table(
            "tags",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(60), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("name", name="uq_tags_name"),
        )
        op.create_index("ix_tags_name", "tags", ["name"])

    if not table_exists("shopping_item_tags"):
        op.create_table(
            "shopping_item_tags",
            sa.Column("item_id", sa.Integer, sa.ForeignKey("shopping_items.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
            sa.UniqueConstraint("item_id", "tag_id", name="uq_shopping_item_tag"),
        )

    if not table_exists("categories"):
        return

    bind = op.get_bind()
    for name in READING_CATEGORIES:
        exists = bind.execute(
            sa.text("SELECT id FROM categories WHERE name = :name AND type = 'reading'"),
            {"name": name},
        ).first()
        if exists:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO categories (name, is_global, parent_id, type) "
                "VALUES (:name, 1, NULL, 'reading')"
            ),
            {"name": name},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if table_exists("shopping_item_tags"):
        op.drop_table("shopping_item_tags")
    if table_exists("tags"):
        op.drop_table("tags")
    if table_exists("shopping_items"):
        for index in (
            "ix_shopping_items_external_id",
            "ix_shopping_items_imported_from",
            "ix_shopping_items_reading_format",
            "ix_shopping_items_category_id",
        ):
            try:
                op.drop_index(index, table_name="shopping_items")
            except Exception:  # индекс мог не создаться — это не ошибка отката
                pass
        for column in ("external_id", "imported_from", "reading_format", "category_id"):
            if column_exists("shopping_items", column):
                with op.batch_alter_table("shopping_items") as batch_op:
                    batch_op.drop_column(column)
    if table_exists("categories"):
        bind.execute(sa.text("DELETE FROM categories WHERE type = 'reading'"))
