"""Двухуровневое меню: «Дашборд» и «Финансы» — разделы-хабы со своими частями.

Название раздела — ссылка на страницу (клик по «Дашборду» сразу открывает
дашборд), раскрытие частей — отдельная кнопка-шеврон справа. Категории
разделены: задачи живут в Бэклоге, финансовые — в Финансах.
"""

import re

import pytest

pytestmark = pytest.mark.asyncio

DASH_PARTS = [
    "/backlog",
    "/backlog?view=calendar",
    "/backlog?view=categories",
    "/recurring",
    "/archive",
]
FIN_PARTS = ["/finance?view=categories", "/finance?view=portfolio"]


async def test_dashboard_is_the_default_page(client):
    response = await client.get("/")

    assert response.status_code == 200


async def test_menu_shows_hubs_and_their_parts(client):
    html = (await client.get("/")).text

    assert 'aria-label="Разделы дашборда"' in html
    assert 'aria-label="Разделы финансов"' in html
    for href in DASH_PARTS + FIN_PARTS:
        assert f'href="{href}"' in html, f"в меню нет пункта {href}"
    for href in ('href="/"', 'href="/stats"', 'href="/achievements"'):
        assert href in html, f"в меню нет {href}"
    # старые плоские пункты уехали внутрь разделов
    assert 'href="/categories"' not in html
    assert 'href="/portfolio"' not in html


async def test_hub_name_is_link_and_toggle_is_separate_button(client):
    html = (await client.get("/")).text

    assert re.search(r'<a href="/"[^>]*class="pnav__link', html), "«Дашборд» должен быть ссылкой"
    assert re.search(r'<a href="/finance"[^>]*class="pnav__link', html), "«Финансы» должны быть ссылкой"
    for controls in ("nav-dash-children", "nav-fin-children"):
        assert re.search(
            r'<button[^>]*class="pnav__chev"[^>]*aria-controls="' + controls + '"', html
        ), f"нет отдельной кнопки раскрытия для {controls}"

    # части раздела лежат внутри своей группы, а не плоским списком меню
    dash_block = html.split('id="nav-dash-children"', 1)[1].split('id="nav-fin-children"', 1)[0]
    for href in DASH_PARTS:
        assert href in dash_block, f"{href} не внутри группы дашборда"
    fin_block = html.split('id="nav-fin-children"', 1)[1].split("</nav>", 1)[0]
    for href in FIN_PARTS:
        assert href in fin_block, f"{href} не внутри группы финансов"


async def test_group_with_current_page_is_expanded(client):
    home = (await client.get("/")).text
    assert home.count('x-data="{ open: true }"') == 1, "на дашборде раскрыт только его раздел"

    finance = (await client.get("/finance")).text
    assert finance.count('x-data="{ open: true }"') == 1, "на финансах раскрыт только их раздел"

    backlog = (await client.get("/backlog")).text
    assert backlog.count('x-data="{ open: true }"') == 1


async def test_task_categories_live_inside_backlog(client):
    redirect = await client.get("/categories", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "/backlog?view=categories"

    page = await client.get("/backlog?view=categories")
    assert page.status_code == 200
    assert 'name="type" value="task"' in page.text, "форма создаёт категорию задачи"
    assert "Категории задач" in page.text


async def test_finance_categories_live_inside_finance(client):
    page = await client.get("/finance?view=categories")

    assert page.status_code == 200
    assert 'name="type" value="finance"' in page.text, "форма создаёт финансовую категорию"
    assert "Фин. категории" in page.text


async def test_portfolio_lives_inside_finance(client):
    redirect = await client.get("/portfolio", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"].startswith("/finance?view=portfolio")

    tab = await client.get("/finance?view=portfolio")
    assert tab.status_code == 200
    assert "portfolioRoot" in tab.text
    assert "portfolio-analytics.js" in tab.text

    # тяжёлый скрипт портфеля не грузится на других вкладках финансов
    overview = await client.get("/finance")
    assert "portfolio-analytics.js" not in overview.text


async def _active_tab_label(html: str) -> str:
    """Подпись вкладки с классом is-active (после него идут aria-атрибуты)."""
    assert html.count('class="btab is-active"') == 1, "активной должна быть ровно одна вкладка"
    chunk = html.split('class="btab is-active"', 1)[1]
    return chunk.split(">", 1)[1].split("</a>", 1)[0].strip()


async def test_finance_tabs_highlight_current_one(client):
    for url, label in (
        ("/finance", "Обзор"),
        ("/finance?view=categories", "Фин. категории"),
        ("/finance?view=portfolio", "Портфель"),
    ):
        page = await client.get(url)
        assert page.status_code == 200, url
        assert label in await _active_tab_label(page.text), url


async def test_backlog_tabs_highlight_current_one(client):
    page = await client.get("/backlog?view=categories")

    assert "Категории" in await _active_tab_label(page.text)
