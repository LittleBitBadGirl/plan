"""Тесты блока «Менеджеры»: виджет на дашборде, попап, записи с пруфами, удаление."""

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.database import async_session
from app.models.manager import Manager, ManagerFeedback

TEST_DB_URL = "/api/managers"


@pytest_asyncio.fixture(autouse=True)
async def clean_managers():
    """Пустые managers/manager_feedback между тестами."""
    async with async_session() as session:
        await session.execute(delete(ManagerFeedback))
        await session.execute(delete(Manager))
        await session.commit()
    yield


async def _make_manager(name: str = "Тестовый Менеджер", projects: str = "Тест") -> int:
    async with async_session() as session:
        manager = Manager(name=name, projects=projects, is_active=True, sort_order=1)
        session.add(manager)
        await session.commit()
        return manager.id


pytestmark = pytest.mark.asyncio


async def test_widget_renders_managers(client):
    await _make_manager("Алёна Тест", "Атол")

    response = await client.get("/managers/widget")

    assert response.status_code == 200
    assert "Алёна Тест" in response.text
    assert "0 записей" in response.text


async def test_dashboard_renders_managers_widget_server_side(client):
    """Блок менеджеров приходит с GET / (без hx-trigger load)."""
    await _make_manager("Алёна Тест", "Атол")

    response = await client.get("/")

    assert response.status_code == 200
    assert 'id="managers-widget"' in response.text
    assert "Алёна Тест" in response.text
    assert 'hx-trigger="load' not in response.text


async def test_create_manager_returns_widget(client):
    response = await client.post(
        f"{TEST_DB_URL}/create",
        data={"name": "Герман Тест", "projects": "Б24"},
    )

    assert response.status_code == 200
    assert "Герман Тест" in response.text

    async with async_session() as session:
        result = await session.execute(select(Manager).where(Manager.name == "Герман Тест"))
        assert result.scalar_one_or_none() is not None


async def test_feedback_saved_with_date_month_and_kind(client):
    manager_id = await _make_manager()

    response = await client.post(
        f"{TEST_DB_URL}/{manager_id}/feedback",
        data={
            "text": "Сорвал срок по ТЗ, узнала в последний момент",
            "kind": "minus",
            "project": "Атол",
            "links": "https://tracker.dalee.ru/browse/DSBTSUPP-1 мусор",
            "fb_date": "2026-09-11",
        },
    )

    assert response.status_code == 200
    assert "Сорвал срок по ТЗ" in response.text
    assert "Сентябрь 2026" in response.text

    async with async_session() as session:
        result = await session.execute(select(ManagerFeedback))
        record = result.scalar_one()
        assert record.kind == "minus"
        assert record.period_month == "2026-09"
        assert record.project == "Атол"
        assert record.date.isoformat() == "2026-09-11"
        assert "https://tracker.dalee.ru/browse/DSBTSUPP-1" in record.links
        assert "мусор" not in record.links


async def test_feedback_defaults_to_today(client):
    manager_id = await _make_manager()

    await client.post(f"{TEST_DB_URL}/{manager_id}/feedback", data={"text": "Заметка"})

    async with async_session() as session:
        result = await session.execute(select(ManagerFeedback))
        record = result.scalar_one()
        assert record.date == date.today()
        assert record.kind == "minus"


async def test_uploaded_proof_is_stored_and_served(client):
    manager_id = await _make_manager()
    payload = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    response = await client.post(
        f"{TEST_DB_URL}/{manager_id}/feedback",
        data={"text": "Скрин переписки", "kind": "minus"},
        files={"files": ("proof.png", payload, "image/png")},
    )

    assert response.status_code == 200
    async with async_session() as session:
        result = await session.execute(select(ManagerFeedback))
        record = result.scalar_one()
        assert record.files
        stored = record.files.strip('[]"')
        assert stored.startswith("feedback/")

    assert response.text.count("mf-proof-thumb") >= 1
    served = await client.get(f"/uploads/{stored}")
    assert served.status_code == 200


async def test_delete_feedback_removes_record_and_file(client):
    manager_id = await _make_manager()
    await client.post(
        f"{TEST_DB_URL}/{manager_id}/feedback",
        data={"text": "Удалить меня"},
        files={"files": ("proof.png", b"1234567890", "image/png")},
    )

    async with async_session() as session:
        result = await session.execute(select(ManagerFeedback))
        record = result.scalar_one()
        feedback_id = record.id

    response = await client.post(f"{TEST_DB_URL}/feedback/{feedback_id}/delete")

    assert response.status_code == 200
    assert "Записей пока нет" in response.text

    async with async_session() as session:
        result = await session.execute(select(ManagerFeedback))
        assert result.scalars().all() == []


async def test_feedback_groups_by_months(client):
    manager_id = await _make_manager()
    await client.post(
        f"{TEST_DB_URL}/{manager_id}/feedback",
        data={"text": "Август", "fb_date": "2026-08-05"},
    )
    await client.post(
        f"{TEST_DB_URL}/{manager_id}/feedback",
        data={"text": "Сентябрь", "fb_date": "2026-09-05"},
    )

    response = await client.get(f"/managers/{manager_id}/modal")

    assert response.status_code == 200
    assert "Сентябрь 2026" in response.text
    assert "Август 2026" in response.text
    # Свежий месяц выше старого
    assert response.text.index("Сентябрь 2026") < response.text.index("Август 2026")


async def test_empty_feedback_without_files_shows_error(client):
    manager_id = await _make_manager()

    response = await client.post(
        f"{TEST_DB_URL}/{manager_id}/feedback",
        data={"text": "   ", "kind": "minus"},
    )

    assert response.status_code == 200
    assert "Нужен текст или хотя бы один скрин" in response.text

    async with async_session() as session:
        result = await session.execute(select(ManagerFeedback))
        assert result.scalars().all() == []
