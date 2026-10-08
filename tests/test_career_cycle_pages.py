"""Карьерный капитал и цикл — отдельные страницы, а не блоки на аналитике.

Блоки уехали с /stats, чтобы у каждого числа был один хозяин: список достижений
и данные цикла считаются только на своих страницах, на аналитике от них ссылки.
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_stats_page_keeps_only_links_to_career_and_cycle(client):
    html = (await client.get("/stats")).text
    assert 'href="/career"' in html and 'href="/cycle"' in html
    # Содержимое блоков на аналитику больше не рендерится.
    assert "На базе задачи" not in html
    assert "Текст для врача" not in html
    assert "Поток задач" in html, "остальная аналитика на месте"


async def test_career_page_renders_and_exports(client):
    response = await client.get("/career")

    assert response.status_code == 200
    html = response.text
    assert "Карьерный капитал" in html
    assert 'href="/api/career/export"' in html
    # Старый блок со списком записей на странице больше не главный: разбор
    # по активам рисуется из снимка, а прежние записи убраны под сноску.
    assert 'id="impact-results"' not in html
    assert "Прежние записи" in html or "Разбор ещё не собирался" in html


async def test_cycle_page_renders_or_shows_empty_state(client):
    response = await client.get("/cycle")

    assert response.status_code == 200
    html = response.text
    assert "Цикл — для врача" in html
    assert 'href="/stats"' in html
    # Пустая база — понятный пустой экран, а не падение шаблона.
    assert ("Текст для врача" in html) or ("Данных пока нет" in html)


async def test_career_categories_cover_clients_and_team(client):
    """Состав и регистр карьерных категорий: прежний список терял целые куски работы.

    «команда» против «Команда» в базе не находилось вовсе, а «Майоли» и «АТОЛ»
    (клиентские проекты) в списке отсутствовали — их вклад не попадал в достижения.
    """
    from app.web.routes.career import CAREER_CATEGORIES

    for name in ("Майоли", "АТОЛ", "Команда", "Конференции", "Личный бренд"):
        assert name in CAREER_CATEGORIES, f"«{name}» потеряна из карьерных категорий"

    # Отбор идёт по точному совпадению имени, поэтому регистр и пробелы важны.
    assert "команда" not in CAREER_CATEGORIES
    assert all(name == name.strip() for name in CAREER_CATEGORIES)
    assert len(set(CAREER_CATEGORIES)) == len(CAREER_CATEGORIES)


async def test_career_and_cycle_are_in_the_menu(client):
    html = (await client.get("/")).text
    for href in ('href="/career"', 'href="/cycle"'):
        assert href in html, f"в меню нет {href}"


async def test_cycle_doctor_text_counts_real_cycles(client):
    """Текст для врача обязан считать циклы и разброс по настоящим записям.

    Регрессия переноса: шаблон остался без `{% set lengths %}`, и Jinja молча
    подставляла пустую переменную — в тексте стояло «Отслежено 0 полных цикл(ов)»,
    а строка про разброс и σ не выводилась никогда.
    """
    from datetime import date, timedelta

    from sqlalchemy import delete

    from app.db.database import async_session
    from app.models.period_entry import PeriodEntry

    starts = [date(2026, 6, 1), date(2026, 6, 29), date(2026, 7, 28)]
    async with async_session() as db:
        await db.execute(delete(PeriodEntry))
        for start in starts:
            for offset in range(4):
                db.add(PeriodEntry(date=start + timedelta(days=offset), has_pain=False, is_spotting=False))
        await db.commit()

    try:
        response = await client.get("/cycle")
        html = response.text

        assert response.status_code == 200
        # Три цикла в истории, два полных интервала между ними.
        assert "Отслежено 2 полных цикл(ов)" in html
        assert "разброс 28–29" in html
        assert "σ = 0.7" in html
        assert html.count("Цикл 3") >= 1
    finally:
        async with async_session() as db:
            await db.execute(delete(PeriodEntry))
            await db.commit()
