"""Пересказы и книги: связка + пауза чтения.

Revision ID: 026_retelling_books
Revises: 025_day_wins
Create Date: 2026-10-09

Две правки одним шагом.

1. ``shopping_items.reading_paused_at`` — кнопка «не читаю». Вера: «я могу
   временно не читать книгу и надо иметь возможность нажать кнопку не читаю и не
   потерять прогресс». Прогресс (``pages_read``/``pages_total``) не трогаем
   вовсе, а дата паузы нужна, чтобы на дашборде честно сказать, сколько книга
   уже лежит.

2. Таблицы пересказов ``retellings`` / ``retelling_thoughts`` и колонка
   ``retellings.book_item_id`` — связка пересказа с книгой из «Читать». На
   боевой базе эти таблицы уже есть: их 06.10.2026 завёл серверный Hermes, он же
   пишет туда голосовые. Содержимое не трогаем, добавляем только колонку книги.

Колонку на существующей таблице добавляем обычным ALTER, а не batch-пересборкой:
DDL этой таблицы создавал Hermes (там свои комментарии и триггеры), пересобирать
её целиком на живой базе — риск потерять содержимое.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import column_exists, table_exists

revision: str = "026_retelling_books"
down_revision: Union[str, None] = "025_day_wins"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. Пауза чтения -----------------------------------------------------
    if not column_exists("shopping_items", "reading_paused_at"):
        with op.batch_alter_table("shopping_items") as batch_op:
            batch_op.add_column(sa.Column("reading_paused_at", sa.DateTime(timezone=True), nullable=True))

    # --- 2. Пересказы --------------------------------------------------------
    if not table_exists("retellings"):
        op.create_table(
            "retellings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("recorded_at", sa.String(), nullable=False),
            sa.Column("habit_id", sa.Integer(), server_default="11"),
            sa.Column("source_type", sa.String(), server_default="book"),
            sa.Column("source_title", sa.String()),
            sa.Column("source_author", sa.String()),
            sa.Column("chapter", sa.String()),
            sa.Column("duration_sec", sa.Integer()),
            sa.Column("audio_path", sa.String()),
            sa.Column("transcript", sa.Text()),
            sa.Column("summary", sa.Text()),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", sa.String(), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("habit_log_id", sa.Integer(), sa.ForeignKey("habit_logs.id")),
            sa.Column("book_item_id", sa.Integer(), sa.ForeignKey("shopping_items.id")),
        )
        op.create_index("idx_retellings_recorded", "retellings", ["recorded_at"])
        op.create_index("idx_retellings_source", "retellings", ["source_title"])
    else:
        # Боевая база: таблица уже есть, добавляем только связку с книгой.
        if not column_exists("retellings", "book_item_id"):
            op.execute(
                "ALTER TABLE retellings ADD COLUMN book_item_id INTEGER "
                "REFERENCES shopping_items(id)"
            )
        if not column_exists("retellings", "habit_log_id"):
            op.execute(
                "ALTER TABLE retellings ADD COLUMN habit_log_id INTEGER "
                "REFERENCES habit_logs(id)"
            )

    if not table_exists("retelling_thoughts"):
        op.create_table(
            "retelling_thoughts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "retelling_id",
                sa.Integer(),
                sa.ForeignKey("retellings.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("thought", sa.Text(), nullable=False),
            sa.Column("quote", sa.Text()),
            sa.Column("page", sa.String()),
            sa.Column("created_at", sa.String(), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint(
                "retelling_id", "position", name="uq_retelling_thought_position"
            ),
        )
        op.create_index("idx_thoughts_retelling", "retelling_thoughts", ["retelling_id"])


def downgrade() -> None:
    # Связку убираем, сами пересказы не тронем никогда: это записи Веры, а не
    # схема. Удалить их откатом миграции значило бы потерять расшифровки.
    if table_exists("retellings") and column_exists("retellings", "book_item_id"):
        op.execute("ALTER TABLE retellings DROP COLUMN book_item_id")
    if column_exists("shopping_items", "reading_paused_at"):
        with op.batch_alter_table("shopping_items") as batch_op:
            batch_op.drop_column("reading_paused_at")
