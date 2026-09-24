"""Категории, форматы и теги раздела «Читать»: фильтры и правка таксономии."""

import pytest
from sqlalchemy import select

from app.models.category import Category
from app.models.shopping import ShoppingItem
from app.models.tag import Tag


async def _add(client, title, **fields):
    payload = {"title": title, **fields}
    resp = await client.post("/api/reading/create", data=payload)
    assert resp.status_code == 200
    return resp


@pytest.mark.asyncio
async def test_create_assigns_category_format_and_tags(client, db):
    await _add(client, "Паттерны AI-агентов", category="ИИ и агенты", reading_format="документ",
               tags="агенты, Anthropic")

    item = (await db.execute(select(ShoppingItem).where(ShoppingItem.title.like("%Паттерны%")))).scalar_one()
    assert item.category is not None and item.category.name == "ИИ и агенты"
    assert item.reading_format == "документ"
    assert sorted(tag.name for tag in item.tags) == ["Anthropic", "агенты"]


@pytest.mark.asyncio
async def test_new_formats_are_allowed(client, db):
    """Новые форматы чтения (24.09.2026): гайд, отчёт, презентация, аудио, документ.

    Старый «разбор PDF» больше не проходит: значение вне списка не сохраняется.
    """
    for fmt in ("гайд", "отчёт", "презентация", "аудио", "документ"):
        await _add(client, f"Материал формата {fmt}", reading_format=fmt)
    await _add(client, "Старый формат", reading_format="разбор PDF")

    async def fmt_of(title):
        row = (await db.execute(select(ShoppingItem).where(ShoppingItem.title == title))).scalar_one()
        return row.reading_format

    for fmt in ("гайд", "отчёт", "презентация", "аудио", "документ"):
        assert await fmt_of(f"Материал формата {fmt}") == fmt
    assert await fmt_of("Старый формат") is None


@pytest.mark.asyncio
async def test_unknown_category_leaves_item_without_category(client, db):
    """Правило Веры: не уверен в категории — не ставим её."""
    await _add(client, "Непонятный материал", category="Такой категории нет", tags="разобрать")

    item = (await db.execute(select(ShoppingItem).where(ShoppingItem.title == "Непонятный материал"))).scalar_one()
    assert item.category_id is None
    assert [tag.name for tag in item.tags] == ["разобрать"]


@pytest.mark.asyncio
async def test_meta_endpoint_updates_taxonomy(client, db):
    await _add(client, "Материал для правки")
    item = (await db.execute(select(ShoppingItem).where(ShoppingItem.title == "Материал для правки"))).scalar_one()

    resp = await client.post(
        f"/api/reading/{item.id}/meta",
        data={"category": "Карьера", "reading_format": "видео", "tags": "Лапшина, оффер"},
    )
    assert resp.status_code == 200

    await db.refresh(item)
    assert item.category.name == "Карьера"
    assert item.reading_format == "видео"
    assert sorted(tag.name for tag in item.tags) == ["Лапшина", "оффер"]

    # повторное сохранение тех же тегов не создаёт второй связи и второй тег
    await client.post(
        f"/api/reading/{item.id}/meta",
        data={"category": "Карьера", "reading_format": "видео", "tags": "Лапшина, оффер"},
    )
    await db.refresh(item)
    assert len(item.tags) == 2
    tags = (await db.execute(select(Tag))).scalars().all()
    assert len([t for t in tags if t.name == "Лапшина"]) == 1


@pytest.mark.asyncio
async def test_filter_by_category(client, db):
    await _add(client, "Книга по менеджменту", category="Менеджмент и команда", reading_format="книга")
    await _add(client, "Статья про агентов", category="ИИ и агенты", reading_format="статья")

    html = (await client.get("/reading", params={"cat": "ИИ и агенты"})).text
    assert "Статья про агентов" in html
    assert "Книга по менеджменту" not in html


@pytest.mark.asyncio
async def test_filter_by_tag_and_format_and_search(client, db):
    await _add(client, "Разбор про агентов", category="ИИ и агенты", reading_format="статья", tags="агенты")
    await _add(client, "Книга про переговоры", category="Коммуникация и речь", reading_format="книга",
               tags="переговоры")

    by_tag = (await client.get("/reading", params={"tag": "агенты"})).text
    assert "Разбор про агентов" in by_tag
    assert "Книга про переговоры" not in by_tag

    by_format = (await client.get("/reading", params={"fmt": "книга"})).text
    assert "Книга про переговоры" in by_format
    assert "Разбор про агентов" not in by_format

    found = (await client.get("/reading", params={"q": "переговор"})).text
    assert "Книга про переговоры" in found
    assert "Разбор про агентов" not in found


@pytest.mark.asyncio
async def test_archived_items_hidden_by_default(client, db):
    db.add(ShoppingItem(title="Уже прочитано", item_kind="reading", is_archived=True,
                        reading_status="reading"))
    db.add(ShoppingItem(title="Ещё в планах", item_kind="reading"))
    await db.commit()

    active = (await client.get("/reading")).text
    assert "Ещё в планах" in active
    assert "Уже прочитано" not in active

    archived = (await client.get("/reading", params={"status": "arch"})).text
    assert "Уже прочитано" in archived
    assert "Ещё в планах" not in archived


@pytest.mark.asyncio
async def test_filter_by_no_category_shelf(client, db):
    """Полка «без категории» — это фильтр по отсутствию категории."""
    await _add(client, "Понятный материал", category="Карьера")
    await _add(client, "Неопознанный материал", tags="разобрать")

    html = (await client.get("/reading", params={"cat": "без категории"})).text
    assert "Неопознанный материал" in html
    assert "Понятный материал" not in html


@pytest.mark.asyncio
async def test_all_tags_are_rendered_and_extra_ones_hidden_by_css(client, db):
    """Все теги есть в разметке: на широком экране видны все, лишние прячет CSS.

    Срез на сервере убран — иначе на десктопе теги без причины сворачивались
    в кнопку «ещё N тегов», хотя влезают целиком. Класс reading-chip--extra
    помечает чипсы за пределами TAG_CHIPS_LIMIT; их показывает медиазапрос
    от 900px, а кнопка «ещё N тегов» на широком экране скрыта.
    """
    tags = ", ".join(f"тег{i}" for i in range(20))
    await _add(client, "Материал с кучей тегов", tags=tags)

    # Считаем только чипсы фильтра (чекбоксы), а не теги на карточках:
    # на карточке тег — это кнопка с тем же именем поля.
    chip = 'type="checkbox" name="tag"'
    short = (await client.get("/reading")).text
    assert short.count(chip) == 20
    assert short.count("reading-chip--extra") == 8
    assert "ещё 8 тегов" in short

    full = (await client.get("/reading", params={"all_tags": "1"})).text
    assert full.count(chip) == 20
    assert full.count("reading-chip--extra") == 0
    assert "свернуть теги" in full


@pytest.mark.asyncio
async def test_readings_without_category_go_to_their_own_shelf(client, db):
    await _add(client, "Понятная запись", category="Карьера")
    await _add(client, "Непонятная запись", tags="разобрать")

    html = (await client.get("/reading")).text
    assert "без категории" in html
    # полка «без категории» идёт после тематических: у неё больше позиция в разметке
    assert html.index("Карьера") < html.index("Непонятная запись")


@pytest.mark.asyncio
async def test_list_endpoint_returns_partial_only(client, db):
    await _add(client, "Пункт списка")
    html = (await client.get("/reading/list")).text
    assert "Пункт списка" in html
    assert "<html" not in html.lower()


@pytest.mark.asyncio
async def test_filters_survive_actions_within_list(client, db):
    """Фильтры, приложенные к кнопке внутри списка, доезжают до ответа."""
    await _add(client, "Первая запись", category="Карьера")
    await _add(client, "Вторая запись", category="ИИ и агенты")
    item = (await db.execute(select(ShoppingItem).where(ShoppingItem.title == "Вторая запись"))).scalar_one()

    resp = await client.post(
        f"/api/reading/{item.id}/progress",
        data={"cat": "ИИ и агенты", "status": "active"},
    )
    assert resp.status_code == 200
    html = resp.text
    assert "Вторая запись" in html
    assert "Первая запись" not in html


@pytest.mark.asyncio
async def test_api_categories_hides_reading_by_default(client, db):
    """Категории чтения не должны попадать в списки задач."""
    default = {c["name"] for c in (await client.get("/api/categories")).json()}
    assert "ИИ и агенты" not in default

    reading = {c["name"] for c in (await client.get("/api/categories", params={"type": "reading"})).json()}
    assert "ИИ и агенты" in reading

    every = {c["name"] for c in (await client.get("/api/categories", params={"type": "all"})).json()}
    assert reading < every


@pytest.mark.asyncio
async def test_backlog_does_not_offer_reading_categories(client, db):
    """В бэклоге задач категорий чтения быть не должно."""
    html = (await client.get("/backlog")).text
    assert "ИИ и агенты" not in html


@pytest.mark.asyncio
async def test_delete_reading_category_frees_items(client, db):
    """Удалили категорию чтения — записи уезжают на полку «без категории»."""
    await _add(client, "Материал про агентов", category="ИИ и агенты", reading_format="статья")
    item = (await db.execute(select(ShoppingItem).where(ShoppingItem.title == "Материал про агентов"))).scalar_one()
    cat = (await db.execute(
        select(Category).where(Category.name == "ИИ и агенты", Category.type == "reading")
    )).scalar_one()

    resp = await client.delete(f"/api/categories/{cat.id}")
    assert resp.status_code == 200

    # Смотрим свежей сессией: удаление пришло из другого соединения.
    from app.db.database import async_session

    async with async_session() as fresh:
        row = (await fresh.execute(
            select(ShoppingItem).where(ShoppingItem.id == item.id)
        )).scalar_one()
        assert row.category_id is None

    html = (await client.get("/reading", params={"cat": "без категории"})).text
    assert "Материал про агентов" in html
    filtered = (await client.get("/reading", params={"cat": "ИИ и агенты"})).text
    assert "Материал про агентов" not in filtered


@pytest.mark.asyncio
async def test_repeat_import_does_not_create_duplicates(client, db):
    """Повторный импорт того же сообщения архива дубля не создаёт."""
    payload = {"title": "Сообщение из архива", "imported_from": "favorites", "external_id": "123456"}

    first = await client.post("/api/reading/create", data=payload)
    second = await client.post("/api/reading/create", data=payload)
    assert first.status_code == 200 and second.status_code == 200

    items = (await db.execute(select(ShoppingItem).where(ShoppingItem.external_id == "123456"))).scalars().all()
    assert len(items) == 1
    assert items[0].imported_from == "favorites"


@pytest.mark.asyncio
async def test_import_key_is_unique_in_db(client, db):
    """Ключ импорта защищён уникальным индексом, а не только кодом."""
    db.add(ShoppingItem(title="Первое", item_kind="reading", imported_from="read", external_id="777"))
    await db.commit()

    db.add(ShoppingItem(title="Второе", item_kind="reading", imported_from="read", external_id="777"))
    with pytest.raises(Exception):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_seed_does_not_resurrect_deleted_reading_category(client, db):
    """Удалённую категорию чтения досев при старте не возвращает."""
    from app.db.seed import seed_reading_categories

    cat = (await db.execute(
        select(Category).where(Category.name == "Досуг", Category.type == "reading")
    )).scalar_one()
    await db.delete(cat)
    await db.commit()

    await seed_reading_categories(db)
    await db.commit()

    left = (await db.execute(
        select(Category).where(Category.name == "Досуг", Category.type == "reading")
    )).scalars().all()
    assert left == []


@pytest.mark.asyncio
async def test_archived_items_keep_their_category_shelves(client, db):
    """В архиве раскладка по категориям та же, что и счётчики на чипсах."""
    category = (await db.execute(
        select(Category).where(Category.name == "Карьера", Category.type == "reading")
    )).scalar_one()
    db.add(ShoppingItem(title="Прочитанная статья", item_kind="reading", is_archived=True,
                        reading_status="done", category_id=category.id))
    db.add(ShoppingItem(title="Активная статья", item_kind="reading", category_id=category.id))
    await db.commit()

    html = (await client.get("/reading", params={"status": "arch"})).text
    assert "Прочитанная статья" in html
    assert "Активная статья" not in html
    assert "Карьера" in html


@pytest.mark.asyncio
async def test_limit_caps_cards_and_offers_more(client, db):
    """Страница рисует порцией, дальше — кнопка «показать ещё»."""
    for num in range(5):
        await _add(client, f"Материал {num}")

    default = (await client.get("/reading")).text
    assert default.count('class="reading-card"') == 5      # порция большая, влезли все

    capped = (await client.get("/reading", params={"limit": 2})).text
    assert capped.count('class="reading-card"') == 2
    assert "показать ещё" in capped
    assert "показано 2" in capped

    full = (await client.get("/reading", params={"limit": 10})).text
    assert full.count('class="reading-card"') == 5
    assert "показать ещё" not in full
