"""Страница «Читать»: список переехал с низа дашборда в отдельный раздел."""

import pytest

from app.models.shopping import ShoppingItem


@pytest.mark.asyncio
async def test_reading_page_renders_list_and_form(client, db):
    db.add(ShoppingItem(title="Бизнес по-русски", item_kind="reading", reading_status="reading"))
    db.add(ShoppingItem(title="Русская модель управления", item_kind="reading"))
    await db.commit()

    resp = await client.get("/reading")
    assert resp.status_code == 200
    html = resp.text

    # обе книги на месте, добавление и попап «подробнее» тоже
    assert "Бизнес по-русски" in html
    assert "Русская модель управления" in html
    assert 'hx-post="/api/reading/create"' in html
    assert 'id="reading-list"' in html
    assert 'id="reading-popup-overlay"' in html
    assert "readings" not in html  # на странице нет старых надписей дашборда


@pytest.mark.asyncio
async def test_reading_page_shows_pdf_viewer_for_pdf_links(client, db):
    db.add(
        ShoppingItem(
            title="http://example.com/book.pdf",
            item_kind="reading",
            reading_status="reading",
        )
    )
    await db.commit()

    html = (await client.get("/reading")).text
    assert "читать здесь" in html
    assert "reading-pdf__frame" in html or "reading-pdf__box" in html


@pytest.mark.asyncio
async def test_reading_page_counts_in_lead(client, db):
    db.add(ShoppingItem(title="Читаю сейчас", item_kind="reading", reading_status="reading"))
    db.add(ShoppingItem(title="В очереди", item_kind="reading"))
    await db.commit()

    html = (await client.get("/reading")).text
    assert "читаю 1" in html
    assert "к прочтению 1" in html


@pytest.mark.asyncio
async def test_dashboard_has_reading_shortcut_not_list(client, db):
    db.add(ShoppingItem(title="Книга на дашборде", item_kind="reading", reading_status="reading"))
    await db.commit()

    html = (await client.get("/")).text
    # компактная кнопка ведёт на страницу
    assert 'href="/reading"' in html
    assert "reading-shortcut" in html
    # тесного списка внизу дашборда больше нет
    assert 'id="reading-list"' not in html
    assert 'hx-post="/api/reading/create"' not in html


@pytest.mark.asyncio
async def test_nav_links_to_reading(client):
    html = (await client.get("/")).text
    assert 'href="/reading"' in html
