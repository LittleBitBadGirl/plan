"""Мердж календаря в Бэклог: вкладки «Задачи» / «Календарь», старый /calendar редиректит."""

import re

import pytest

pytestmark = pytest.mark.asyncio


def _nav_class(html: str, href: str) -> str:
    """Классы ссылки меню по её href (для проверки подсветки)."""
    match = re.search(r'<a href="' + re.escape(href) + r'"[^>]*class="([^"]*)"', html)
    assert match, f"в меню нет ссылки {href}"
    return match.group(1)


async def test_old_calendar_page_redirects_into_backlog_tab(client):
    response = await client.get("/calendar", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/backlog?view=calendar"


async def test_backlog_default_view_shows_tasks_not_month(client):
    response = await client.get("/backlog")

    assert response.status_code == 200
    assert response.text.count('id="calendar-days"') == 0
    assert '/backlog/create' in response.text


async def test_calendar_tab_renders_month_grid(client):
    response = await client.get("/backlog?view=calendar")

    assert response.status_code == 200
    html = response.text
    assert response.text.count('id="calendar-days"') == 1
    assert 'id="prev-month"' in html
    assert 'id="next-month"' in html
    assert 'id="month-title"' in html
    assert 'id="date-tasks-list"' in html
    assert 'id="selected-date-title"' in html


async def test_calendar_tab_hides_task_quick_add(client):
    response = await client.get("/backlog?view=calendar")

    assert "/backlog/create" not in response.text
    # липкая полоса быстрого добавления в зоне большого пальца
    assert "lg:hidden fixed bottom-0" not in response.text


async def test_calendar_script_loads_only_with_calendar_tab(client):
    tasks_html = (await client.get("/backlog")).text
    calendar_html = (await client.get("/backlog?view=calendar")).text

    assert "calLoadDateTasks" not in tasks_html
    assert "calLoadDateTasks" in calendar_html


async def test_calendar_script_declares_state_once(client):
    """Скрипт календаря больше не дублируется: раньше вторая копия валила страницу."""
    html = (await client.get("/backlog?view=calendar")).text

    assert html.count("let currentDate") == 1
    assert html.count("function renderCalendar") == 1
    assert html.count("const monthNames") == 1


async def test_tabs_present_on_both_views_and_marked_active(client):
    tasks_html = (await client.get("/backlog")).text
    calendar_html = (await client.get("/backlog?view=calendar")).text

    for html in (tasks_html, calendar_html):
        assert 'href="/backlog"' in html
        assert 'href="/backlog?view=calendar"' in html

    assert "is-active" in tasks_html
    assert "is-active" in calendar_html


async def test_sidebar_highlights_current_tab(client):
    """Меню подсвечивает текущую часть раздела (у меню два уровня)."""
    tasks_html = (await client.get("/backlog")).text
    calendar_html = (await client.get("/backlog?view=calendar")).text
    categories_html = (await client.get("/backlog?view=categories")).text

    assert "is-active" in _nav_class(tasks_html, "/backlog")
    assert "is-active" not in _nav_class(calendar_html, "/backlog")

    assert "is-active" in _nav_class(calendar_html, "/backlog?view=calendar")
    assert "is-active" not in _nav_class(tasks_html, "/backlog?view=calendar")

    assert "is-active" in _nav_class(categories_html, "/backlog?view=categories")
    assert "is-active" not in _nav_class(tasks_html, "/backlog?view=categories")


async def test_unknown_view_falls_back_to_tasks(client):
    response = await client.get("/backlog?view=что-то")

    assert response.status_code == 200
    assert response.text.count('id="calendar-days"') == 0
    assert "/backlog/create" in response.text


async def test_old_calendar_links_follow_through_to_month_grid(client):
    """Ссылка из меню ведёт на рабочую вкладку, а не в пустоту."""
    response = await client.get("/backlog?view=calendar", follow_redirects=True)

    assert response.status_code == 200
    assert 'id="calendar-days"' in response.text
