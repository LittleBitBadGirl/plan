"""Раскладка существующих записей чтения по категориям, форматам и тегам.

Revision ID: 016_reading_taxonomy_data
Revises: 015_reading_taxonomy
Create Date: 2026-09-23

Содержание:
- 45 записей, которые лежали в списке «Читать» до появления категорий,
  получают категорию (type=reading), формат и теги. Раскладка согласована
  с Верой по макету страницы чтения.
- Где категория неочевидна, она НЕ ставится: запись остаётся без категории
  и получает тег «разобрать» (правило Веры: не уверен — откладывай).
- Служебное: удаляется тестовая запись «Проверка со страницы» и дубль книги
  про двери (id 69, остаётся id 64); двум записям, лежавшим голой ссылкой,
  дописано человеческое название перед адресом.

Идемпотентность: раскладка применяется только к записям, у которых ещё нет
ни категории, ни формата. Повторный прогон ничего не меняет.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import table_exists

revision: str = "016_reading_taxonomy_data"
down_revision: Union[str, None] = "015_reading_taxonomy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (id, категория или None, формат или None, теги)
ASSIGNMENT = [
    (10, 'Продукт и процессы', 'статья', ['NMT', 'для выступления', 'продукт']),
    (11, 'Продукт и процессы', 'статья', ['метрики', 'продукт']),
    (13, 'Менеджмент и команда', 'книга', ['команда', 'роли']),
    (14, 'Менеджмент и команда', 'книга', ['Россия', 'управление']),
    (15, 'Мышление и психология', 'книга', ['игры', 'стратегия']),
    (16, 'Художественная литература', 'книга', ['wishlist', 'роман']),
    (17, 'Мышление и психология', 'книга', ['wishlist', 'интеллект']),
    (18, 'Мышление и психология', 'книга', ['wishlist', 'креативность']),
    (19, 'Мышление и психология', 'книга', ['мышление', 'экономика']),
    (20, 'Финансы и инвестиции', 'статья', ['золото', 'инвестиции']),
    (21, 'Финансы и инвестиции', 'статья', ['инвестиции', 'пенсия']),
    (22, 'Мышление и психология', 'книга', ['саморазвитие']),
    (23, 'Карьера', 'подкаст', ['Лапшина', 'интервью']),
    (24, 'Финансы и инвестиции', 'плейлист', ['инвестиции', 'ликвидность']),
    (25, 'Мышление и психология', 'плейлист', ['мировоззрение']),
    (26, 'Мышление и психология', 'статья', ['общество']),
    (27, 'Мышление и психология', 'статья', ['будущее', 'навыки']),
    (28, 'Мышление и психология', 'видео', ['СДВГ', 'здоровье']),
    (29, None, 'подкаст', ['разобрать', 'технологии']),
    (30, 'ИИ и агенты', 'статья', ['HR', 'рефрейминг']),
    (31, 'ИИ и агенты', 'разбор PDF', ['Anthropic', 'агенты', 'воркфлоу']),
    (32, 'ИИ и агенты', 'разбор PDF', ['a16z', 'агенты', 'стратегия']),
    (34, 'ИИ и агенты', 'видео', ['агенты', 'прогнозы']),
    (35, 'Коммуникация и речь', 'конспект', ['директора', 'презентации']),
    (36, 'Менеджмент и команда', 'книга', ['инженерный менеджмент']),
    (37, 'Менеджмент и команда', 'статья', ['руководителю']),
    (38, 'Менеджмент и команда', 'статья', ['промпты', 'спринты']),
    (39, 'ИИ и агенты', 'статья', ['ИИ', 'профессия']),
    (40, 'Карьера', 'тест', ['hh', 'сертификация']),
    (41, 'Менеджмент и команда', 'книга', ['практика', 'управление']),
    (60, 'Карьера', 'подборка', ['Лапшина', 'подборка']),
    (62, 'Карьера', 'видео', ['Лапшина', 'повышение']),
    (63, 'Финансы и инвестиции', 'статья', ['ОФЗ', 'портфель']),
    (64, 'Менеджмент и команда', 'книга', ['психология управления']),
    (65, None, 'видео', ['разобрать']),
    (67, 'Финансы и инвестиции', 'статья', ['Транснефть', 'дивиденды']),
    (70, 'Карьера', 'видео', ['Лапшина', 'блог']),
    (71, 'Мышление и психология', 'книга', ['Чалдини', 'влияние']),
    (76, None, None, ['разобрать']),
    (79, 'ИИ и агенты', 'разбор PDF', ['Hermes', 'агенты', 'медиа']),
    (80, 'Художественная литература', 'книга', ['роман']),
    (81, 'Мышление и психология', 'книга', ['навыки', 'практика']),
    (82, 'Коммуникация и речь', 'разбор PDF', ['Блэр Эннс', 'переговоры', 'продажи']),
    (83, 'Коммуникация и речь', 'разбор PDF', ['Блэр Эннс', 'переговоры', 'разговоры']),
    (84, 'Коммуникация и речь', 'конспект', ['выступление', 'для сайта', 'речь']),
]

# Голые ссылки, которым дописали название. Меняем только если адрес на месте,
# иначе название уже поправлено человеком.
RENAMES = {
    10: "Природа продукта (NMT) https://nextmovetheory.com/library/the-nature-of-product",
    11: "Product-Market Fit как скоропортящийся товар (NMT) https://nextmovetheory.com/blog/product-market-fit-is-a-perishable-good",
}

# Мусор и дубль: удаляем строго по признакам, а не по одному номеру.
JUNK_TITLE = "Проверка со страницы"
DUPLICATE_ID = 69
DUPLICATE_KEEP_ID = 64



def _tag_id(bind, cache: dict, name: str) -> int:
    """Id тега по имени, создаёт при необходимости."""
    if name in cache:
        return cache[name]
    row = bind.execute(sa.text("SELECT id FROM tags WHERE name = :name"), {"name": name}).first()
    if row:
        cache[name] = row[0]
        return row[0]
    bind.execute(sa.text("INSERT INTO tags (name) VALUES (:name)"), {"name": name})
    new_id = bind.execute(sa.text("SELECT id FROM tags WHERE name = :name"), {"name": name}).scalar_one()
    cache[name] = new_id
    return new_id


def upgrade() -> None:
    if not table_exists("shopping_items") or not table_exists("tags"):
        return
    bind = op.get_bind()

    # 1. Чистка: тестовая запись и дубль книги.
    bind.execute(
        sa.text(
            "DELETE FROM shopping_items WHERE item_kind = 'reading' AND title = :title "
            "AND (content IS NULL OR content = '')"
        ),
        {"title": JUNK_TITLE},
    )
    keeper = bind.execute(
        sa.text("SELECT id FROM shopping_items WHERE id = :id AND item_kind = 'reading'"),
        {"id": DUPLICATE_KEEP_ID},
    ).first()
    if keeper:
        bind.execute(
            sa.text("DELETE FROM shopping_items WHERE id = :id AND item_kind = 'reading'"),
            {"id": DUPLICATE_ID},
        )

    # 2. Человеческие названия вместо голых адресов.
    for item_id, title in RENAMES.items():
        bind.execute(
            sa.text(
                "UPDATE shopping_items SET title = :title WHERE id = :id AND item_kind = 'reading' "
                "AND title LIKE '%http%'"
            ),
            {"title": title, "id": item_id},
        )

    # 3. Категории чтения по именам (заведены миграцией 015).
    category_ids = {
        row[0]: row[1]
        for row in bind.execute(
            sa.text("SELECT name, id FROM categories WHERE type = 'reading'")
        ).all()
    }

    tag_cache: dict = {}
    for item_id, category, reading_format, tags in ASSIGNMENT:
        row = bind.execute(
            sa.text(
                "SELECT id FROM shopping_items WHERE id = :id AND item_kind = 'reading' "
                "AND category_id IS NULL AND reading_format IS NULL"
            ),
            {"id": item_id},
        ).first()
        if not row:
            continue
        category_id = category_ids.get(category) if category else None
        bind.execute(
            sa.text(
                "UPDATE shopping_items SET category_id = :category_id, reading_format = :reading_format "
                "WHERE id = :id"
            ),
            {"category_id": category_id, "reading_format": reading_format, "id": item_id},
        )
        for tag in tags:
            tag_id = _tag_id(bind, tag_cache, tag)
            link = bind.execute(
                sa.text("SELECT 1 FROM shopping_item_tags WHERE item_id = :i AND tag_id = :t"),
                {"i": item_id, "t": tag_id},
            ).first()
            if not link:
                bind.execute(
                    sa.text("INSERT INTO shopping_item_tags (item_id, tag_id) VALUES (:i, :t)"),
                    {"i": item_id, "t": tag_id},
                )


def downgrade() -> None:
    """Раскладку не откатываем: удалённую тестовую запись и дубль не вернуть,
    а снять категории и теги безопаснее руками на странице чтения."""
    pass
