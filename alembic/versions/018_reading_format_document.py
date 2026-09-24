"""Формат «документ» вместо «разбор PDF» в разделе «Читать».

Revision ID: 018_reading_format_document
Revises: 017_reading_import_unique
Create Date: 2026-09-24

Что делает: у записей чтения формат «разбор PDF» переименовывается в
«документ». Причина: «разбор PDF» — это упаковка файла, а не жанр материала,
и как вариант в списке форматов он затягивал в себя всё, что лежит в PDF.

Список допустимых форматов живёт в коде (app/services/reading_service.py,
READING_FORMATS) — эта миграция приводит данные в соответствие с ним, чтобы
старые записи не остались с форматом, которого больше нет в выпадающем списке.

Идемпотентность: обновляются только строки со старым значением, повторный
прогон ничего не меняет.

Оговорка про downgrade: он вернёт в «разбор PDF» ВСЕ записи чтения с форматом
«документ», включая те, что заведены уже новым форматом после этой миграции —
отличить их в базе нечем. Откат делать только до того, как появятся такие
записи, иначе «документ» придётся проставлять руками.
"""

import sqlalchemy as sa
from alembic import op

revision = "018_reading_format_document"
down_revision = "017_reading_import_unique"
branch_labels = None
depends_on = None

OLD = "разбор PDF"
NEW = "документ"


def upgrade() -> None:
    bind = op.get_bind()
    changed = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM shopping_items WHERE item_kind = 'reading' "
            "AND reading_format = :old"
        ),
        {"old": OLD},
    ).scalar_one()
    bind.execute(
        sa.text(
            "UPDATE shopping_items SET reading_format = :new "
            "WHERE item_kind = 'reading' AND reading_format = :old"
        ),
        {"new": NEW, "old": OLD},
    )
    print("018: формат «%s» -> «%s», записей %s" % (OLD, NEW, changed))


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE shopping_items SET reading_format = :old "
            "WHERE item_kind = 'reading' AND reading_format = :new"
        ),
        {"new": NEW, "old": OLD},
    )
