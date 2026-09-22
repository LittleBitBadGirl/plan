"""Тесты раздела «Достижения»: ручные записи, две полки, правка, удаление, виджет."""

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.database import async_session
from app.models.achievement import Achievement

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def clean_achievements():
    """Пустая таблица достижений между тестами."""
    async with async_session() as session:
        await session.execute(delete(Achievement))
        await session.commit()
    yield


async def _add(text: str, sphere: str = "work", **kwargs) -> int:
    async with async_session() as session:
        item = Achievement(text=text, sphere=sphere, **kwargs)
        session.add(item)
        await session.commit()
        return item.id


async def _all() -> list[Achievement]:
    async with async_session() as session:
        result = await session.execute(select(Achievement).order_by(Achievement.id))
        return list(result.scalars().all())


async def test_page_renders_two_empty_shelves(client):
    response = await client.get("/achievements")

    assert response.status_code == 200
    html = response.text
    assert "Достижения" in html
    assert ">Рабочее<" in html
    assert ">Личное<" in html
    assert html.count('class="ach-empty"') == 2


async def test_created_achievement_lands_on_chosen_shelf(client):
    response = await client.post(
        "/achievements",
        data={"text": "Собрала три сайта", "sphere": "personal", "tag": "Проекты"},
    )

    assert response.status_code == 200
    assert "Собрала три сайта" in response.text
    assert "Проекты" in response.text

    items = await _all()
    assert len(items) == 1
    assert items[0].sphere == "personal"
    assert items[0].tag == "Проекты"


async def test_work_and_personal_do_not_mix(client):
    await _add("Выиграла тендер", sphere="work")
    await _add("Купила мотоцикл", sphere="personal")

    html = (await client.get("/achievements/board")).text

    work_block, personal_block = html.split('data-sphere="personal"', 1)
    assert "Выиграла тендер" in work_block
    assert "Купила мотоцикл" not in work_block
    assert "Купила мотоцикл" in personal_block
    assert "Выиграла тендер" not in personal_block


async def test_empty_text_creates_nothing(client):
    response = await client.post("/achievements", data={"text": "   ", "sphere": "work"})

    assert response.status_code == 200
    assert await _all() == []


async def test_unknown_sphere_falls_back_to_personal(client):
    await client.post("/achievements", data={"text": "Что-то", "sphere": "неизвестно"})

    items = await _all()
    assert len(items) == 1
    assert items[0].sphere == "personal"


async def test_edit_moves_achievement_to_other_shelf_and_updates_fields(client):
    ach_id = await _add("Черновик", sphere="personal")

    await client.post(
        f"/achievements/{ach_id}",
        data={
            "text": "Выступала на конференции",
            "sphere": "work",
            "tag": "Выступления",
            "happened_on": "2026-05-14",
        },
    )

    items = await _all()
    assert len(items) == 1
    assert items[0].text == "Выступала на конференции"
    assert items[0].sphere == "work"
    assert items[0].happened_on == date(2026, 5, 14)


async def test_form_has_no_source_field(client):
    """Поле «Откуда запись» убрано из формы: ни в добавлении, ни в правке его нет."""
    page = await client.get("/achievements")
    assert page.status_code == 200
    assert 'name="source"' not in page.text, "поле «Откуда запись» вернулось в разметку"

    ach_id = await _add("Запись")
    edit_form = await client.get(f"/achievements/{ach_id}/edit-form")
    assert 'name="source"' not in edit_form.text


async def test_edit_with_empty_text_keeps_old_value(client):
    ach_id = await _add("Как было", sphere="work")

    await client.post(f"/achievements/{ach_id}", data={"text": "", "sphere": "personal"})

    items = await _all()
    assert items[0].text == "Как было"
    assert items[0].sphere == "work"


async def test_delete_removes_achievement(client):
    ach_id = await _add("Временная запись")

    response = await client.post(f"/achievements/{ach_id}/delete")

    assert response.status_code == 200
    assert "Временная запись" not in response.text
    assert await _all() == []


async def test_achievements_with_date_go_first(client):
    await _add("Без даты", sphere="work")
    await _add("С датой", sphere="work", happened_on=date(2026, 3, 1))

    html = (await client.get("/achievements/board")).text

    assert html.index("С датой") < html.index("Без даты")


async def test_html_in_text_is_escaped(client):
    await client.post("/achievements", data={"text": "<script>alert(1)</script>", "sphere": "work"})

    html = (await client.get("/achievements/board")).text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


async def test_widget_returns_widget_markup(client):
    await client.post(
        "/achievements",
        data={"text": "Записано из дашборда", "sphere": "work", "widget": "1"},
    )

    response = await client.post(
        "/achievements",
        data={"text": "Ещё одно из дашборда", "sphere": "personal", "widget": "1"},
    )

    assert 'id="achievements-widget"' in response.text
    assert 'id="achievements-board"' not in response.text
    assert "Записано из дашборда" in response.text
    assert "Ещё одно из дашборда" in response.text
