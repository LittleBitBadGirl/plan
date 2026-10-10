import pytest

from app.models.shopping import ShoppingItem
from app.services.shopping_service import archive_purchased_item, load_active_shopping


@pytest.mark.asyncio
async def test_archive_purchased_item(db):
    item = ShoppingItem(title="Молоко", item_kind="purchase")
    db.add(item)
    await db.commit()

    archive_purchased_item(item)
    assert item.is_purchased is True
    assert item.is_archived is True
    assert item.purchased_at is not None


@pytest.mark.asyncio
async def test_load_active_shopping_excludes_archived(db):
    db.add(ShoppingItem(title="В списке", item_kind="purchase"))
    db.add(
        ShoppingItem(
            title="В архиве",
            item_kind="purchase",
            is_purchased=True,
            is_archived=True,
        )
    )
    await db.commit()

    items = await load_active_shopping(db)
    titles = [i.title for i in items]
    assert titles == ["В списке"]


@pytest.mark.asyncio
async def test_toggle_shopping_archives_and_removes_row(client, db):
    item = ShoppingItem(title="Яйца", item_kind="purchase")
    db.add(item)
    await db.commit()

    response = await client.post(f"/api/shopping/{item.id}/toggle")
    assert response.status_code == 200
    assert "Яйца" not in response.text
    assert 'id="total-count"' in response.text
    assert 'hx-swap-oob="true"' in response.text

    await db.refresh(item)
    assert item.is_archived is True
    assert item.is_purchased is True


@pytest.mark.asyncio
async def test_toggle_shopping_htmx_delete_swap(client, db):
    """Кнопка «куплено» настроена на hx-swap=delete — в шаблоне есть разметка."""
    item = ShoppingItem(title="Хлеб", item_kind="purchase")
    db.add(item)
    await db.commit()

    page = await client.get("/shopping")
    assert page.status_code == 200
    assert 'hx-swap="delete"' in page.text
    assert f'/api/shopping/{item.id}/toggle' in page.text


def test_first_url_and_url_host():
    from app.web.deps import _first_url, _url_host

    text = "Смотри https://www.ozon.ru/product/kniga-493625957/ — норм цена"
    assert _first_url(text) == "https://www.ozon.ru/product/kniga-493625957/"
    assert _url_host(text) == "ozon.ru"
    # без схемы, но с www — ловим
    assert _first_url("www.wildberries.ru/catalog/179360695/detail.aspx") == (
        "https://www.wildberries.ru/catalog/179360695/detail.aspx"
    )
    # без схемы и www — тоже ловим по домену из списка зон
    assert _first_url("market.yandex.ru/product/123") == "https://market.yandex.ru/product/123"
    assert _first_url("Ссылка: ozon.ru/product/1/ .") == "https://ozon.ru/product/1/"
    # текст без ссылок и мусор из букв с точками — не ссылка
    assert _first_url("текст без ссылки, т.е. совсем") == ""
    # схема в верхнем регистре и ёлочка после ссылки
    assert _first_url("HTTPS://OZON.RU/product/1/") == "HTTPS://OZON.RU/product/1/"
    assert _first_url("смотри https://ozon.ru/product/1/» дальше") == "https://ozon.ru/product/1/"
    assert _first_url(None) == ""
    assert _url_host("") == ""


def test_first_url_rejects_unsafe_schemes():
    """В href не может попасть javascript:, data: или кавычка — только http(s)."""
    from app.web.deps import _first_url

    assert _first_url("javascript:alert(1)") == ""
    assert _first_url("data:text/html,<script>alert(1)</script>") == ""
    assert _first_url("vbscript:msgbox(1)") == ""
    # кавычка в URL обрезается регэкспом, в href не выйдет ничего лишнего
    unsafe = _first_url('http://evil.com/" onmouseover="alert(1)')
    assert '"' not in unsafe
    assert unsafe.startswith("http")


def test_shopping_partial_escapes_href():
    """Кавычки и углы в content не вылезают из атрибута href."""
    item = ShoppingItem(
        id=3,
        title="Товар",
        item_kind="purchase",
        content='http://evil.com/?a=1&b=2"><script>alert(1)</script>',
    )
    html = _render([item])
    # ссылка обрезана по кавычке и экранирована
    assert 'href="http://evil.com/?a=1&amp;b=2"' in html
    assert "<script>" not in html
    assert '"><script>' not in html


def _render(items):
    from types import SimpleNamespace
    from typing import cast

    from fastapi import Request

    from app.web.deps import _render_shopping_list

    request = cast(Request, SimpleNamespace(url_for=lambda *a, **k: "/"))
    return _render_shopping_list(request, items)


def test_shopping_partial_shows_link():
    """Ссылка из content выводится на карточке покупки кликабельной."""
    item = ShoppingItem(
        id=1,
        title="Полка напольная для книг",
        item_kind="purchase",
        content="https://www.wildberries.ru/catalog/179360695/detail.aspx\n\nИсточник: 12 ноября 2025",
    )
    html = _render([item])
    assert 'href="https://www.wildberries.ru/catalog/179360695/detail.aspx"' in html
    assert "wildberries.ru" in html
    assert 'rel="noopener noreferrer"' in html


def test_shopping_partial_without_link_has_no_anchor():
    item = ShoppingItem(
        id=2,
        title="Мото: силикагель-влагопоглотитель",
        item_kind="purchase",
        content=None,
    )
    html = _render([item])
    assert "Мото: силикагель-влагопоглотитель" in html
    assert "<a href=" not in html


@pytest.mark.asyncio
async def test_shopping_page_shows_product_link(client, db):
    item = ShoppingItem(
        title="Пижама для мальчика",
        item_kind="purchase",
        content="https://www.wildberries.ru/catalog/525808619/detail.aspx\nИсточник: 3 сентября 2025",
    )
    db.add(item)
    await db.commit()

    page = await client.get("/shopping")
    assert page.status_code == 200
    assert 'href="https://www.wildberries.ru/catalog/525808619/detail.aspx"' in page.text
