"""Мероприятия: создание, статусы, диапазоны (выставки), метаданные по ссылке."""

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db.database import async_session
from app.models.event import STATUS_GOING, STATUS_NONE, STATUS_NOT_GOING, Event
from app.services import event_service as evs

TODAY = date.today()


def _form(**overrides) -> dict:
    data = {
        "title": "Конференция по ИИ",
        "url": "https://example.com/ai",
        "description": "Про ИИ в работе",
        "location": "Москва",
        "start_date": TODAY.isoformat(),
        "end_date": "",
        "start_time": "19:00",
        "status": STATUS_NONE,
        "board": "1",
        "month": TODAY.strftime("%Y-%m"),
        "day": TODAY.isoformat(),
    }
    data.update(overrides)
    return data


async def _events(db) -> list[Event]:
    result = await db.execute(select(Event).order_by(Event.id))
    return list(result.scalars().all())


async def _fresh_events() -> list[Event]:
    """Свежая сессия: сессия теста держит снимок, снятый до чужих коммитов."""
    async with async_session() as fresh:
        result = await fresh.execute(select(Event).order_by(Event.id))
        return list(result.scalars().all())


# ---------------------------------------------------------------- создание

@pytest.mark.asyncio
async def test_create_event_shows_up_on_page_and_dashboard(client):
    """Созданное мероприятие видно и в календаре, и в блоке недели."""
    response = await client.post("/api/events/create", data=_form())
    assert response.status_code == 200
    assert "Конференция по ИИ" in response.text
    # статус ответа — заголовок для закрытия формы
    assert response.headers.get("HX-Trigger") == "planner-event-saved"

    page = await client.get("/events")
    assert page.status_code == 200
    assert "Конференция по ИИ" in page.text
    assert "19:00" in page.text

    dashboard = await client.get("/")
    assert dashboard.status_code == 200
    assert "events-week-block" in dashboard.text
    assert "Конференция по ИИ" in dashboard.text
    assert 'href="/events"' in dashboard.text


@pytest.mark.asyncio
async def test_create_requires_title_and_dates(client, db):
    """Форма без названия и без даты не создаёт запись, а отвечает ошибкой."""
    empty_title = await client.post("/api/events/create", data=_form(title="   "))
    assert "Нужно название мероприятия" in empty_title.text
    assert "HX-Trigger" not in empty_title.headers

    no_date = await client.post("/api/events/create", data=_form(start_date=""))
    assert "Нужна дата начала" in no_date.text

    assert await _events(db) == []


@pytest.mark.asyncio
async def test_end_date_before_start_is_rejected(client, db):
    """Дата окончания раньше начала — ошибка в форме, а не сломанная запись."""
    data = _form(
        start_date=(TODAY + timedelta(days=5)).isoformat(),
        end_date=(TODAY + timedelta(days=1)).isoformat(),
    )
    response = await client.post("/api/events/create", data=data)
    assert "Дата окончания раньше даты начала" in response.text
    assert await _events(db) == []


@pytest.mark.asyncio
async def test_single_day_event_has_no_end_date(client, db):
    """Одно мероприятие — одна дата: end_date не заполняется той же датой."""
    same_day = TODAY.isoformat()
    await client.post("/api/events/create", data=_form(end_date=same_day))
    events = await _events(db)
    assert len(events) == 1
    assert events[0].end_date is None
    assert events[0].is_range is False


# ---------------------------------------------------------------- статусы

@pytest.mark.asyncio
async def test_status_button_toggles_and_resets(client, db):
    """«Иду» → «Не иду» → повторное нажатие снимает отметку."""
    await client.post("/api/events/create", data=_form())
    event = (await _events(db))[0]

    going = await client.post(
        f"/api/events/{event.id}/status", data={"value": STATUS_GOING, "board": "1"}
    )
    assert going.status_code == 200
    assert "ev-status--going is-on" in going.text

    not_going = await client.post(
        f"/api/events/{event.id}/status", data={"value": STATUS_NOT_GOING, "board": "1"}
    )
    assert "ev-status--no is-on" in not_going.text

    reset = await client.post(
        f"/api/events/{event.id}/status",
        data={"value": STATUS_NOT_GOING, "board": "0"},
    )
    assert "ev-status--no is-on" not in reset.text
    # board=0 — корневым блоком идёт блок недели (дашборд)
    assert "events-week-block" in reset.text


@pytest.mark.asyncio
async def test_status_from_dashboard_updates_page_block_too(client, db):
    """Нажатие в блоке недели обновляет и доску (она приезжает OOB)."""
    await client.post("/api/events/create", data=_form())
    event = (await _events(db))[0]

    response = await client.post(
        f"/api/events/{event.id}/status", data={"value": STATUS_GOING, "board": "0"}
    )
    body = response.text
    assert 'id="events-week-block"' in body
    assert 'id="events-board"' in body
    assert 'hx-swap-oob="true"' in body


# ---------------------------------------------------------------- диапазоны

@pytest.mark.asyncio
async def test_exhibition_inside_week_gets_end_date_label(client, db):
    """Выставка, открытая ещё неделю, попадает в блок недели с датой окончания."""
    start = TODAY - timedelta(days=2)
    finish = TODAY + timedelta(days=5)
    await client.post(
        "/api/events/create",
        data=_form(
            title="Выставка роботов",
            start_date=start.isoformat(),
            end_date=finish.isoformat(),
        ),
    )
    page = await client.get("/events")
    assert "Выставка роботов" in page.text
    # дата окончания видна в списках: «до 5 окт» (формат короткой даты)
    assert f"до {evs.short_date(finish)}" in page.text
    # и в календарной сетке дни периода помечены полосой
    assert "ev-day__bar" in page.text

    dashboard = await client.get("/")
    assert "Выставка роботов" in dashboard.text


@pytest.mark.asyncio
async def test_finished_exhibition_leaves_week_block(client, db):
    """Вчера закончившаяся выставка в ближайшую неделю не попадает."""
    await client.post(
        "/api/events/create",
        data=_form(
            title="Прошедшая выставка",
            start_date=(TODAY - timedelta(days=10)).isoformat(),
            end_date=(TODAY - timedelta(days=1)).isoformat(),
        ),
    )
    page = await client.get("/events")
    assert "Прошедшая выставка" in page.text  # в свёрнутых «Прошедших»
    week_items = await evs.load_week_events(db, TODAY)
    assert week_items == []


@pytest.mark.asyncio
async def test_week_selection_covers_ongoing_and_starting_events(client, db):
    """В окно недели попадают и начинающиеся, и длящиеся мероприятия."""
    starting = TODAY + timedelta(days=3)
    await client.post(
        "/api/events/create",
        data=_form(title="Через три дня", start_date=starting.isoformat()),
    )
    await client.post(
        "/api/events/create",
        data=_form(
            title="Идёт всю неделю",
            start_date=(TODAY - timedelta(days=1)).isoformat(),
            end_date=(TODAY + timedelta(days=20)).isoformat(),
        ),
    )
    await client.post(
        "/api/events/create",
        data=_form(
            title="Далеко потом",
            start_date=(TODAY + timedelta(days=40)).isoformat(),
        ),
    )

    titles = [ev.title for ev in await evs.load_week_events(db, TODAY)]
    assert titles == ["Идёт всю неделю", "Через три дня"]

    block = await evs.week_block(db, TODAY)
    assert block["week_count"] == 2
    assert block["week_items"][0]["is_range"] is True
    assert block["week_items"][0]["is_now"] is True


@pytest.mark.asyncio
async def test_month_grid_marks_every_day_of_range(client, db):
    """Период красит все свои дни, а не только день открытия."""
    # Месяц берём целиком внутри себя: конец периода не должен уезжать
    # в следующий месяц, иначе в сетке будет виден только один день.
    month = date(2026, 10, 1)
    start = date(2026, 10, 5)
    await client.post(
        "/api/events/create",
        data=_form(
            title="Выставка",
            start_date=start.isoformat(),
            end_date=(start + timedelta(days=2)).isoformat(),
        ),
    )
    events = await _events(db)
    grid = evs.build_month_grid(month.year, month.month, events, TODAY, start)
    marked = [cell for cell in grid["days"] if cell["has_events"]]
    assert len(marked) == 3
    assert all(cell["has_range"] for cell in marked)
    assert marked[0]["range_start"] is True
    assert marked[-1]["range_end"] is True
    assert marked[0]["state"] == STATUS_NONE


@pytest.mark.asyncio
async def test_grid_state_prefers_going(client, db):
    """Если в дне есть «иду» и «не иду», день помечается как «иду»."""
    month = date(2026, 10, 1)
    going_day = date(2026, 10, 7).isoformat()
    await client.post("/api/events/create", data=_form(title="Пойду", start_date=going_day))
    await client.post("/api/events/create", data=_form(title="Не пойду", start_date=going_day))
    first, second = await _events(db)
    await client.post(f"/api/events/{first.id}/status", data={"value": STATUS_GOING})
    await client.post(f"/api/events/{second.id}/status", data={"value": STATUS_NOT_GOING})

    # Свежая сессия: в сессии теста статусы ещё прежние, и день выглядел бы
    # «без отметки» — проверка ловила бы не логику, а снимок.
    events = await _fresh_events()
    grid = evs.build_month_grid(month.year, month.month, events, TODAY, None)
    cell = [c for c in grid["days"] if c["date_iso"] == going_day][0]
    assert cell["state"] == STATUS_GOING
    assert cell["count"] == 2


# ---------------------------------------------------------------- правка

@pytest.mark.asyncio
async def test_edit_form_is_prefilled_and_update_saves(client, db):
    """Форма правки приходит заполненной, сохранение меняет запись."""
    await client.post("/api/events/create", data=_form())
    event = (await _events(db))[0]

    form = await client.get(f"/events/{event.id}/form")
    assert f'value="{event.title}"' in form.text
    assert 'name="title"' in form.text

    updated = await client.post(
        f"/api/events/{event.id}/update",
        data=_form(title="Конференция перенесена", start_time="10:30"),
    )
    assert updated.status_code == 200
    assert "HX-Trigger" in updated.headers

    fresh = (await _fresh_events())[0]
    assert fresh.title == "Конференция перенесена"
    assert fresh.start_time == "10:30"

    page = await client.get("/events")
    assert "Конференция перенесена" in page.text


@pytest.mark.asyncio
async def test_delete_event_removes_it(client, db):
    await client.post("/api/events/create", data=_form())
    event = (await _events(db))[0]
    response = await client.post(f"/api/events/{event.id}/delete", data={"board": "1"})
    assert response.status_code == 200
    assert await _events(db) == []


@pytest.mark.asyncio
async def test_url_without_scheme_gets_https(client, db):
    """«afisha.ru/x» сохраняется как ссылка, а мусор не сохраняется вовсе."""
    await client.post("/api/events/create", data=_form(url="afisha.ru/abc"))
    await client.post("/api/events/create", data=_form(title="Без ссылки", url="не ссылка"))
    events = await _events(db)
    assert events[0].url == "https://afisha.ru/abc"
    assert events[1].url is None


# ---------------------------------------------------------------- метаданные

HTML_SAMPLE = """
<html><head>
<title>Заголовок страницы</title>
<meta property="og:title" content="Фестиваль &quot;Лето&quot;">
<meta property="og:description" content="Музыка и лекции на открытом воздухе">
<meta property="og:image" content="/img/poster.jpg">
<meta property="og:site_name" content="Афиша">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Event","name":"Фестиваль Лето",
 "startDate":"2026-10-12T19:00:00+03:00","endDate":"2026-10-15T23:00:00+03:00",
 "location":{"@type":"Place","name":"Парк Горького"},"image":"https://cdn/afisha.jpg"}
</script></head><body></body></html>
"""


def test_parse_metadata_reads_opengraph_and_jsonld():
    meta = evs.parse_metadata(HTML_SAMPLE, "https://afisha.ru/event/1")
    assert meta["title"] == 'Фестиваль "Лето"'
    assert meta["description"] == "Музыка и лекции на открытом воздухе"
    assert meta["image_url"] == "https://afisha.ru/img/poster.jpg"
    assert meta["site"] == "Афиша"
    assert meta["start_date"] == "2026-10-12"
    assert meta["end_date"] == "2026-10-15"
    assert meta["start_time"] == "19:00"
    assert meta["location"] == "Парк Горького"


def test_parse_metadata_falls_back_to_title_tag():
    meta = evs.parse_metadata(
        "<html><head><title>  Просто   страница </title></head></html>",
        "https://example.com/x",
    )
    assert meta["title"] == "Просто страница"
    assert meta["start_date"] == ""


def test_parse_metadata_reads_dates_without_ld_type():
    """Билетные сайты кладут даты в разметку без @type — берём и оттуда."""
    meta = evs.parse_metadata(
        '<html><body><script>{"startDate":"2026-11-01","endDate":"2026-11-20"}</script></body></html>',
        "https://example.com/y",
    )
    assert meta["start_date"] == "2026-11-01"
    assert meta["end_date"] == "2026-11-20"


def test_hosts_allowed_blocks_local_and_non_http():
    for bad in (
        "file:///etc/passwd",
        "http://localhost:8000/x",
        "http://127.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/x",
    ):
        allowed, reason = evs.hosts_allowed(bad)
        assert not allowed, bad
        assert reason
    allowed, _ = evs.hosts_allowed("https://afisha.ru/event/1")
    assert allowed


@pytest.mark.asyncio
async def test_preview_without_url_asks_for_it(client):
    response = await client.post("/api/events/preview", data={"url": ""})
    assert "Вставь ссылку или заполни руками" in response.text


# ---------------------------------------------------------------- помощники

def test_parse_time_and_dates():
    assert evs.parse_time("19:00") == "19:00"
    assert evs.parse_time("9") == "09:00"
    assert evs.parse_time("19.30") == "19:30"
    assert evs.parse_time("25:00") is None
    assert evs.parse_time("") is None
    assert evs.parse_form_date("2026-10-12") == date(2026, 10, 12)
    assert evs.parse_form_date("12.10.2026") == date(2026, 10, 12)
    assert evs.parse_form_date("мусор") is None


def test_relative_and_range_labels():
    today = date(2026, 10, 12)
    assert evs.relative_label(today, today) == "сегодня"
    assert evs.relative_label(today + timedelta(days=1), today) == "завтра"
    assert evs.relative_label(today + timedelta(days=3), today) == "через 3 дня"
    assert evs.relative_label(today + timedelta(days=10), today) == ""

    event = Event(
        title="Выставка",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=4),
        status=STATUS_GOING,
    )
    view = evs.event_view(event, today)
    assert view["is_range"] is True
    assert view["when"] == "идёт до 16 окт"
    assert view["period_hint"] == "осталось 4 дня"
    assert view["status_label"] == "Иду"

    last_day = Event(title="Последний день", start_date=today, end_date=today, status=STATUS_NONE)
    assert evs.event_view(last_day, today)["period_hint"] == "сегодня"


@pytest.mark.asyncio
async def test_save_image_writes_file_and_rejects_wrong_type():
    class FakeUpload:
        def __init__(self, filename, payload):
            self.filename = filename
            self._payload = payload

        async def read(self):
            return self._payload

    saved = await evs.save_image(FakeUpload("poster.PNG", b"\x89PNG\r\n"))
    assert saved and saved.startswith("events/")
    path = evs.safe_upload_path(saved)
    assert path and path.is_file()

    assert await evs.save_image(FakeUpload("virus.exe", b"MZ")) is None
    assert await evs.save_image(FakeUpload("", b"x")) is None

    evs.delete_upload(saved)
    assert not path.exists()


def test_safe_upload_path_blocks_traversal():
    assert evs.safe_upload_path("../../etc/passwd") is None
    assert evs.safe_upload_path("") is None


@pytest.mark.asyncio
async def test_archived_events_are_hidden(client, db):
    """Запись в архиве не показывается ни в календаре, ни в блоке недели."""
    await client.post("/api/events/create", data=_form(title="Скрытое"))
    event = (await _events(db))[0]
    event.is_archived = 1
    await db.commit()

    page = await client.get("/events")
    assert "Скрытое" not in page.text
    assert await evs.load_week_events(db, TODAY) == []

@pytest.mark.asyncio
async def test_exhibition_last_day_today_still_in_week_block(client):
    """Последний день выставки — сегодня: она ещё в блоке и это подписано."""
    await client.post(
        "/api/events/create",
        data=_form(
            title="Выставка до сегодня",
            start_date=(TODAY - timedelta(days=5)).isoformat(),
            end_date=TODAY.isoformat(),
        ),
    )
    dashboard = await client.get("/")
    assert "Выставка до сегодня" in dashboard.text
    assert "последний день" in dashboard.text


@pytest.mark.asyncio
async def test_week_window_edges(client, db):
    """Шестой день ещё попадает в неделю, седьмой — уже нет."""
    await client.post(
        "/api/events/create",
        data=_form(title="На шестой день", start_date=(TODAY + timedelta(days=6)).isoformat()),
    )
    await client.post(
        "/api/events/create",
        data=_form(title="На седьмой день", start_date=(TODAY + timedelta(days=7)).isoformat()),
    )

    block = await evs.week_block(db, TODAY)
    items = block.get("week_items") or block.get("items") or []
    titles = [item["title"] for item in items]
    assert "На шестой день" in titles
    assert "На седьмой день" not in titles


# --------------------------------------------------------- ссылка и картинки

@pytest.mark.asyncio
async def test_preview_fills_form_from_link(client, monkeypatch):
    """Путь «вставила ссылку → форма заполнилась»: выборку подменяем, сети нет."""
    async def fake_fetch(url: str) -> dict:
        return {
            "url": url,
            "title": "Выставка роботов",
            "description": "Промышленные манипуляторы",
            "image_url": "https://example.com/robots.jpg",
            "site": "example.com",
            "location": "Экспоцентр",
            "start_date": "2026-10-05",
            "end_date": "2026-10-10",
            "start_time": "",
            "error": "",
        }

    monkeypatch.setattr(evs, "fetch_link_metadata", fake_fetch)

    response = await client.post(
        "/api/events/preview", data={"url": "https://example.com/robots"}
    )
    assert response.status_code == 200
    assert "Выставка роботов" in response.text
    assert "2026-10-05" in response.text
    assert "Страница прочитана" in response.text


@pytest.mark.asyncio
async def test_preview_reports_error_in_form(client, monkeypatch):
    """Ссылка не прочиталась — форма открывается с текстом ошибки, не падает."""
    async def fake_fetch(url: str) -> dict:
        return {
            "url": url,
            "title": "",
            "description": "",
            "image_url": "",
            "site": "",
            "location": "",
            "start_date": "",
            "end_date": "",
            "start_time": "",
            "error": "Страница ответила 404",
        }

    monkeypatch.setattr(evs, "fetch_link_metadata", fake_fetch)

    response = await client.post("/api/events/preview", data={"url": "https://example.com/no"})
    assert response.status_code == 200
    assert "Страница ответила 404" in response.text


@pytest.mark.asyncio
async def test_redirect_to_internal_address_is_refused(monkeypatch):
    """Редирект на внутренний адрес не выполняется: проверяется каждый переход."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/admin"})
        return httpx.Response(200, text="<html><title>Внутренний сервис</title></html>")

    async def fake_allow(url: str) -> tuple[bool, str]:
        if "127.0.0.1" in url or "169.254" in url:
            return False, "Локальные адреса не поддерживаются"
        return True, ""

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        evs.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    monkeypatch.setattr(evs, "ensure_public_url", fake_allow)

    result = await evs.fetch_link_metadata("https://example.com/expo")
    assert "Локальн" in result["error"]
    assert len(seen) == 1, "запрос ушёл по внутреннему адресу из редиректа"


@pytest.mark.asyncio
async def test_huge_page_is_refused_by_content_length(monkeypatch):
    """Ссылка на большой файл не читается целиком: лимит по заголовку длины."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": str(50 * 1024 * 1024)},
            content=b"<html></html>",
        )

    async def fake_allow(url: str) -> tuple[bool, str]:
        return True, ""

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        evs.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    monkeypatch.setattr(evs, "ensure_public_url", fake_allow)

    result = await evs.fetch_link_metadata("https://example.com/big")
    assert "слишком большая" in result["error"]


def test_safe_image_url_allows_only_pictures():
    assert evs.safe_image_url("https://a.test/b.png") == "https://a.test/b.png"
    assert evs.safe_image_url("data:image/png;base64,AAA") == "data:image/png;base64,AAA"
    assert evs.safe_image_url("javascript:alert(1)") is None
    assert evs.safe_image_url("file:///etc/passwd") is None
    assert evs.safe_image_url("") is None


@pytest.mark.asyncio
async def test_image_url_scheme_filtered_in_form(client):
    """Схему картинки из формы проверяем так же, как схему самой ссылки."""
    await client.post(
        "/api/events/create",
        data=_form(title="Плохая картинка", image_url="javascript:alert(1)"),
    )
    await client.post(
        "/api/events/create",
        data=_form(title="Хорошая картинка", image_url="https://a.test/b.png"),
    )
    saved = {ev.title: ev.image_url for ev in await _fresh_events()}
    assert saved["Плохая картинка"] is None
    assert saved["Хорошая картинка"] == "https://a.test/b.png"


@pytest.mark.asyncio
async def test_status_from_dashboard_updates_board_and_week_block(client):
    """Смена статуса с дашборда обновляет и витрину, и блок недели."""
    await client.post("/api/events/create", data=_form())
    event = (await _fresh_events())[0]

    response = await client.post(
        f"/api/events/{event.id}/status",
        data={"value": STATUS_GOING, "board": "1", "month": "", "day": ""},
    )
    assert response.status_code == 200
    assert "events-week-block" in response.text
    assert "events-board" in response.text
