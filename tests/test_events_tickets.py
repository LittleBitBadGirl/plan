"""Билеты мероприятий: легко загрузить и потом легко найти.

Вера 11.10.2026: «у меня 25 числа хоккей и есть билет — я бы хотела его легко
получить… билеты и на самолёт туда бы прикрепляла». PDF лежит в закрытой папке
tickets/, открывается по клику с карточки, отдаёт его маршрут под входом.

Здесь проверяется ровно то, на чём эта затея держится: файл доехал до диска,
открывается с карточки, чужому не отдаётся, лишний тип не принимается, второй
билет не затирает первый, удаление убирает и файл.
"""

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.event import Event
from app.services import event_service as evs

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


def _form(**kwargs) -> dict:
    data = {
        "title": "Хоккей",
        "url": "",
        "description": "",
        "location": "ЦСКА Арена",
        "start_date": (date.today() + timedelta(days=13)).isoformat(),
        "end_time_placeholder": "",
        "end_date": "",
        "start_time": "19:00",
        "image_url": "",
    }
    data.update(kwargs)
    return data


async def _only_event(db) -> Event:
    result = await db.execute(select(Event))
    return result.scalars().first()


@pytest.mark.asyncio
async def test_ticket_uploads_lands_on_disk_and_opens_from_card(client, db):
    response = await client.post(
        "/api/events/create",
        data=_form(),
        files={"tickets": ("билет хоккей.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 200

    event = await _only_event(db)
    assert event is not None
    tickets = evs.ticket_views(event.ticket_files)
    assert len(tickets) == 1
    assert tickets[0]["name"] == "билет хоккей.pdf"
    assert tickets[0]["path"].endswith(".pdf")

    path = evs.safe_ticket_path(tickets[0]["path"])
    assert path is not None and path.is_file()
    assert path.read_bytes() == PDF

    opened = await client.get(f"/events/{event.id}/tickets/0")
    assert opened.status_code == 200
    assert opened.content == PDF
    # inline — открывается в браузере, а не падает в «Загрузки».
    assert "inline" in opened.headers["content-disposition"]

    board = await client.get("/events")
    assert f"/events/{event.id}/tickets/0" in board.text
    assert "билет хоккей.pdf" in board.text

    # И в форме правки билет виден списком — иначе его не заменить, только добавить.
    form = await client.get(f"/events/{event.id}/form")
    assert "билет хоккей.pdf" in form.text
    assert f"/events/{event.id}/tickets/0" in form.text


@pytest.mark.asyncio
async def test_second_ticket_does_not_overwrite_the_first(client, db):
    """Билет на самолёт и билет на хоккей — разные файлы одного мероприятия."""
    response = await client.post(
        "/api/events/create",
        data=_form(title="Поездка"),
        files=[
            ("tickets", ("самолёт.pdf", PDF, "application/pdf")),
            ("tickets", ("фото.jpeg", b"\xff\xd8\xff\xe0photo", "image/jpeg")),
        ],
    )
    assert response.status_code == 200

    event = await _only_event(db)
    tickets = evs.ticket_views(event.ticket_files)
    assert [t["name"] for t in tickets] == ["самолёт.pdf", "фото.jpeg"]
    for ticket in tickets:
        path = evs.safe_ticket_path(ticket["path"])
        assert path is not None and path.is_file()


@pytest.mark.asyncio
async def test_ticket_of_wrong_type_is_ignored_but_event_is_saved(client, db):
    response = await client.post(
        "/api/events/create",
        data=_form(),
        files={"tickets": ("заметка.txt", b"not a ticket", "text/plain")},
    )
    assert response.status_code == 200
    event = await _only_event(db)
    assert event is not None
    assert evs.ticket_views(event.ticket_files) == []
    assert event.ticket_files is None


@pytest.mark.asyncio
async def test_ticket_delete_removes_file_from_disk(client, db):
    await client.post(
        "/api/events/create",
        data=_form(),
        files={"tickets": ("билет.pdf", PDF, "application/pdf")},
    )
    event = await _only_event(db)
    ticket = evs.ticket_views(event.ticket_files)[0]
    path = evs.safe_ticket_path(ticket["path"])
    assert path.is_file()

    response = await client.post(f"/api/events/{event.id}/tickets/0/delete")
    assert response.status_code == 200
    assert not path.exists()

    await db.refresh(event)
    assert evs.ticket_views(event.ticket_files) == []
    gone = await client.get(f"/events/{event.id}/tickets/0")
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_event_delete_takes_ticket_files_with_it(client, db):
    await client.post(
        "/api/events/create",
        data=_form(),
        files={"tickets": ("билет.pdf", PDF, "application/pdf")},
    )
    event = await _only_event(db)
    ticket = evs.ticket_views(event.ticket_files)[0]
    path = evs.safe_ticket_path(ticket["path"])
    assert path.is_file()

    response = await client.post(f"/api/events/{event.id}/delete")
    assert response.status_code == 200
    assert not path.exists()
    assert await _only_event(db) is None


@pytest.mark.asyncio
async def test_ticket_file_is_not_public(client, db):
    """Без входа билет не отдаётся: в нём фамилия, иногда паспорт."""
    await client.post(
        "/api/events/create",
        data=_form(),
        files={"tickets": ("билет.pdf", PDF, "application/pdf")},
    )
    event = await _only_event(db)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as anon:
        response = await anon.get(f"/events/{event.id}/tickets/0")

    assert response.status_code in (302, 401, 403)
    if response.status_code == 302:
        assert "/login" in response.headers.get("location", "")


@pytest.mark.asyncio
async def test_card_markup_keeps_remove_button_inside_ticket_row(client, db):
    """Кнопка «убрать билет» должна оставаться в строке билетов.

    Внутри <p> браузер закрывает абзац перед <form>, и кнопка выпадает из
    контейнера (нашли разбором DOM). Обёртка — div.
    """
    await client.post(
        "/api/events/create",
        data=_form(),
        files=[
            ("tickets", ("билет1.pdf", PDF, "application/pdf")),
            ("tickets", ("билет2.pdf", PDF, "application/pdf")),
        ],
    )
    board = await client.get("/events")
    assert '<div class="ev-card-item__tickets">' in board.text
    assert '<p class="ev-card-item__tickets">' not in board.text
    assert board.text.count("/tickets/0/delete") == 1
    assert board.text.count("/tickets/1/delete") == 1


@pytest.mark.asyncio
async def test_delete_one_of_two_tickets_keeps_the_other(client, db):
    await client.post(
        "/api/events/create",
        data=_form(),
        files=[
            ("tickets", ("первый.pdf", PDF, "application/pdf")),
            ("tickets", ("второй.pdf", b"%PDF-1.4 second\n%%EOF\n", "application/pdf")),
        ],
    )
    event = await _only_event(db)
    first, second = evs.ticket_views(event.ticket_files)
    first_path = evs.safe_ticket_path(first["path"])
    second_path = evs.safe_ticket_path(second["path"])

    response = await client.post(f"/api/events/{event.id}/tickets/0/delete")
    assert response.status_code == 200

    await db.refresh(event)
    left = evs.ticket_views(event.ticket_files)
    assert [t["name"] for t in left] == ["второй.pdf"]
    assert not first_path.exists()
    assert second_path.is_file()
    # Второго билета по старому номеру больше нет — 404, а не чужой файл.
    assert (await client.get(f"/events/{event.id}/tickets/1")).status_code == 404
    assert (await client.get(f"/events/{event.id}/tickets/0")).status_code == 200


@pytest.mark.asyncio
async def test_edit_adds_second_ticket_without_losing_first(client, db):
    await client.post(
        "/api/events/create",
        data=_form(),
        files={"tickets": ("первый.pdf", PDF, "application/pdf")},
    )
    event = await _only_event(db)

    response = await client.post(
        f"/api/events/{event.id}/update",
        data=_form(),
        files={"tickets": ("второй.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 200

    await db.refresh(event)
    assert [t["name"] for t in evs.ticket_views(event.ticket_files)] == [
        "первый.pdf",
        "второй.pdf",
    ]


@pytest.mark.asyncio
async def test_plain_edit_keeps_tickets(client, db):
    """Обычная правка (сменил название) не должна стирать билеты."""
    await client.post(
        "/api/events/create",
        data=_form(),
        files={"tickets": ("билет.pdf", PDF, "application/pdf")},
    )
    event = await _only_event(db)
    ticket = evs.ticket_views(event.ticket_files)[0]
    path = evs.safe_ticket_path(ticket["path"])

    response = await client.post(
        f"/api/events/{event.id}/update", data=_form(title="Хоккей на ЦСКА")
    )
    assert response.status_code == 200

    await db.refresh(event)
    assert event.title == "Хоккей на ЦСКА"
    assert [t["name"] for t in evs.ticket_views(event.ticket_files)] == ["билет.pdf"]
    assert path.is_file()


@pytest.mark.asyncio
async def test_form_can_drop_one_ticket_and_add_another(client, db):
    """В форме у каждого билета своя галочка «убрать» — не только «все разом»."""
    await client.post(
        "/api/events/create",
        data=_form(),
        files=[
            ("tickets", ("старый.pdf", PDF, "application/pdf")),
            ("tickets", ("оставить.pdf", PDF, "application/pdf")),
        ],
    )
    event = await _only_event(db)
    old, keep = evs.ticket_views(event.ticket_files)
    old_path = evs.safe_ticket_path(old["path"])

    response = await client.post(
        f"/api/events/{event.id}/update",
        data={**_form(), "drop_tickets": "0"},
        files={"tickets": ("новый.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 200

    await db.refresh(event)
    assert [t["name"] for t in evs.ticket_views(event.ticket_files)] == [
        "оставить.pdf",
        "новый.pdf",
    ]
    assert not old_path.exists()
    assert evs.safe_ticket_path(keep["path"]).is_file()


@pytest.mark.asyncio
async def test_mixed_upload_keeps_valid_and_skips_invalid(client, db):
    await client.post(
        "/api/events/create",
        data=_form(),
        files=[
            ("tickets", ("билет.pdf", PDF, "application/pdf")),
            ("tickets", ("заметка.txt", b"not a ticket", "text/plain")),
        ],
    )
    event = await _only_event(db)
    assert [t["name"] for t in evs.ticket_views(event.ticket_files)] == ["билет.pdf"]


@pytest.mark.asyncio
async def test_ticket_of_another_event_is_not_served(client, db):
    """Номер билета — внутри своего мероприятия: чужой файл по номеру не отдать."""
    await client.post(
        "/api/events/create",
        data=_form(title="Первое"),
        files={"tickets": ("первый.pdf", b"%PDF-1.4 first\n%%EOF\n", "application/pdf")},
    )
    await client.post(
        "/api/events/create",
        data=_form(title="Второе"),
        files={"tickets": ("второй.pdf", b"%PDF-1.4 second\n%%EOF\n", "application/pdf")},
    )
    result = await db.execute(select(Event).order_by(Event.id))
    first_event, second_event = result.scalars().all()

    mine = await client.get(f"/events/{second_event.id}/tickets/0")
    assert mine.status_code == 200
    assert mine.content == b"%PDF-1.4 second\n%%EOF\n"
    assert mine.content != b"%PDF-1.4 first\n%%EOF\n"
    assert (
        await client.get(f"/events/{first_event.id}/tickets/0")
    ).content == b"%PDF-1.4 first\n%%EOF\n"
    assert (await client.get(f"/events/{first_event.id}/tickets/1")).status_code == 404
