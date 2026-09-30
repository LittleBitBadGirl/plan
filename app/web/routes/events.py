"""Мероприятия: страница-календарь, форма и блок «ближайшая неделя» на дашборде.

Страница:   GET  /events                     (сетка месяца + панель дня + списки)
Сетка:      GET  /events/board?month=&day=   (HTMX: месяц/день без перезагрузки)
Форма:      GET  /events/new-form            (пустая форма)
            POST /api/events/preview         (вставили ссылку — тянем данные)
            POST /api/events/create
            GET  /events/{id}/form
            POST /api/events/{id}/update
Решение:    POST /api/events/{id}/status      (снять отметку: брони нет)
Поход:      GET  /events/{id}/visit-form     (мини-форма: день и время сеанса)
            POST /api/events/{id}/visit       («Иду»: сохранить бронь билета)
            GET  /events/{id}/visit.ics       (файл календаря, напоминание за сутки)
Удаление:   POST /api/events/{id}/delete

Ответы на изменения отдают ОДИН корневой блок (тот, в котором нажали —
доска на странице или блок недели на дашборде) и второй блок через
hx-swap-oob, поэтому и страница, и дашборд обновляются одним ответом.
"""

from __future__ import annotations

from datetime import date
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from app.db.database import async_session
from app.models.event import STATUS_GOING, STATUS_NONE, Event
from app.services import event_service as evs
from app.web.deps import templates

router = APIRouter()


# --------------------------------------------------------------------------
# контексты
# --------------------------------------------------------------------------

async def _board_context(
    db, today: date, month_raw: Optional[str] = None, day_raw: Optional[str] = None
) -> dict:
    """Всё для доски: сетка месяца, выбранный день, «ближайшие» и «прошедшие»."""
    year, month = evs.month_from_query(month_raw, today)
    selected = evs.parse_iso_date(day_raw) or today
    first, last = evs.month_bounds(year, month)

    month_events = await evs.load_events_between(db, first, last)
    grid = evs.build_month_grid(year, month, month_events, today, selected)
    day_events = await evs.load_events_between(db, selected, selected)

    upcoming = await evs.load_upcoming_events(db, today)
    return {
        "grid": grid,
        "day": selected,
        "day_label": evs.weekday_label(selected),
        "day_items": evs.event_views(day_events, today),
        "upcoming": evs.event_views(upcoming, today),
        "upcoming_count": len(upcoming),
        "past": evs.event_views(await evs.load_past_events(db, today), today),
        "ev_month": f"{year}-{month:02d}",
        "ev_day": selected.isoformat(),
        "today": today,
    }


def _refresh(request: Request, board: bool, board_ctx: dict, week_ctx: dict, form_ctx: Optional[dict] = None):
    """Ответ на изменение: корневой блок + второй через OOB."""
    context = {
        "request": request,
        "ev_board_root": board,
        **board_ctx,
        **week_ctx,
    }
    if form_ctx:
        context.update(form_ctx)
        context["ev_form_error"] = True
    response = templates.TemplateResponse(request, "partials/events_refresh.html", context)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def _wants_board(board: str) -> bool:
    return (board or "1").strip() not in ("0", "false", "")


# --------------------------------------------------------------------------
# страница и сетка
# --------------------------------------------------------------------------

@router.get("/events", response_class=HTMLResponse)
async def events_page(request: Request, month: Optional[str] = None, day: Optional[str] = None):
    """Календарь мероприятий: где я, что идёт и когда заканчивается."""
    today = date.today()
    async with async_session() as db:
        context = await _board_context(db, today, month, day)
        context["week"] = await evs.week_block(db, today)
    context["active_count"] = context["upcoming_count"]
    return templates.TemplateResponse(
        request, "events.html", {"request": request, "today": today, **context}
    )


@router.get("/events/board", response_class=HTMLResponse)
async def events_board(request: Request, month: Optional[str] = None, day: Optional[str] = None):
    """HTMX: доска целиком — переключение месяца и выбор дня."""
    today = date.today()
    async with async_session() as db:
        context = await _board_context(db, today, month, day)
        context["week"] = await evs.week_block(db, today)
    context["active_count"] = context["upcoming_count"]
    response = templates.TemplateResponse(
        request, "partials/events_board.html", {"request": request, **context}
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# --------------------------------------------------------------------------
# форма
# --------------------------------------------------------------------------

def _form_context(
    values: dict,
    *,
    error: str = "",
    note: str = "",
    mode: str = "add",
    event_id: Optional[int] = None,
    has_file: bool = False,
) -> dict:
    return {
        "form": values,
        "form_error": error,
        "form_note": note,
        "form_mode": mode,
        "form_event_id": event_id,
        "form_has_file": has_file,
    }


EMPTY_FORM = {
    "title": "",
    "url": "",
    "description": "",
    "location": "",
    "start_date": "",
    "end_date": "",
    "start_time": "",
    "image_url": "",
    "image": "",
}


def _event_form_values(ev: Event) -> dict:
    return {
        "title": ev.title or "",
        "url": ev.url or "",
        "description": ev.description or "",
        "location": ev.location or "",
        "start_date": ev.start_date.isoformat() if ev.start_date else "",
        "end_date": ev.end_date.isoformat() if ev.end_date else "",
        "start_time": ev.start_time or "",
        "image_url": ev.image_url or "",
        "image": evs.event_image(ev),
    }


@router.get("/events/new-form", response_class=HTMLResponse)
async def events_new_form(request: Request):
    """Пустая форма — «заполнить руками»."""
    return templates.TemplateResponse(
        request,
        "partials/events_form.html",
        {"request": request, **_form_context(dict(EMPTY_FORM))},
    )


@router.post("/api/events/preview", response_class=HTMLResponse)
async def events_preview(request: Request, url: str = Form("")):
    """Вставили ссылку — читаем страницу и отдаём заполненную форму."""
    link = (url or "").strip()
    if not link:
        return templates.TemplateResponse(
            request,
            "partials/events_form.html",
            {
                "request": request,
                **_form_context(dict(EMPTY_FORM), error="Вставь ссылку или заполни руками"),
            },
        )

    meta = await evs.fetch_link_metadata(link)
    values = dict(EMPTY_FORM)
    values.update(
        {
            "title": meta["title"],
            "url": meta["url"],
            "description": meta["description"],
            "location": meta["location"],
            "start_date": meta["start_date"],
            "end_date": meta["end_date"],
            "start_time": meta["start_time"],
            "image_url": meta["image_url"],
            "image": meta["image_url"],
        }
    )
    source = meta["site"] or meta["url"]
    note = f"Страница прочитана: {source}" if source else ""
    if not meta["start_date"]:
        note += ". Даты на странице не нашлись — поставь вручную"
    return templates.TemplateResponse(
        request,
        "partials/events_form.html",
        {"request": request, **_form_context(values, error=meta["error"], note=note)},
    )


async def _apply_form(
    ev: Event,
    *,
    title: str,
    url: str,
    description: str,
    location: str,
    start_date: str,
    end_date: str,
    start_time: str,
    image_url: str,
    image: Optional[UploadFile],
    remove_image: bool,
) -> str:
    """Перенести форму в запись. Возвращает текст ошибки или пустую строку."""
    clean_title = (title or "").strip()
    if not clean_title:
        return "Нужно название мероприятия"
    start = evs.parse_form_date(start_date)
    if not start:
        return "Нужна дата начала"
    finish = evs.parse_form_date(end_date)
    if finish and finish < start:
        return "Дата окончания раньше даты начала — для выставки поставь последний день"

    ev.title = clean_title[:500]
    ev.start_date = start
    ev.end_date = finish if finish and finish != start else None
    ev.start_time = evs.parse_time(start_time)
    # Статус и поход формой не меняются: «Иду» ставится кнопкой в карточке, и
    # только вместе с днём сеанса (см. events_visit). Из формы получался бы «иду»
    # без дня, а из такого состояния событие календаря не собрать.
    ev.description = (description or "").strip() or None
    ev.location = (location or "").strip()[:500] or None
    ev.url = evs.normalize_url(url)
    ev.image_url = evs.safe_image_url(image_url)

    if remove_image:
        evs.delete_upload(ev.image_file)
        ev.image_file = None
    saved = await evs.save_image(image) if image is not None else None
    if saved:
        evs.delete_upload(ev.image_file)
        ev.image_file = saved
    return ""


@router.post("/api/events/create", response_class=HTMLResponse)
async def events_create(
    request: Request,
    title: str = Form(""),
    url: str = Form(""),
    description: str = Form(""),
    location: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    start_time: str = Form(""),
    image_url: str = Form(""),
    board: str = Form("1"),
    month: str = Form(""),
    day: str = Form(""),
    image: UploadFile = File(None),
):
    """Создать мероприятие. Ошибка возвращается в форму, а не 500-й."""
    today = date.today()
    async with async_session() as db:
        ev = Event(source="web", is_archived=0)
        error = await _apply_form(
            ev,
            title=title,
            url=url,
            description=description,
            location=location,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            image_url=image_url,
            image=image,
            remove_image=False,
        )
        if not error:
            db.add(ev)
            await db.commit()
        board_ctx = await _board_context(db, today, month, day)
        week_ctx = await evs.week_block(db, today)

    form_ctx = None
    if error:
        values = dict(EMPTY_FORM)
        values.update(
            {
                "title": title,
                "url": url,
                "description": description,
                "location": location,
                "start_date": start_date,
                "end_date": end_date,
                "start_time": start_time,
                "image_url": image_url,
            }
        )
        form_ctx = _form_context(values, error=error)
    response = _refresh(request, _wants_board(board), board_ctx, week_ctx, form_ctx)
    if not error:
        response.headers["HX-Trigger"] = "planner-event-saved"
    return response


@router.get("/events/{event_id}/form", response_class=HTMLResponse)
async def events_edit_form(request: Request, event_id: int):
    """Форма правки нужного мероприятия."""
    async with async_session() as db:
        ev = await _load_event(db, event_id)
        values = _event_form_values(ev)
        own_file = bool(ev.image_file)
    return templates.TemplateResponse(
        request,
        "partials/events_form.html",
        {
            "request": request,
            **_form_context(values, mode="edit", event_id=event_id, has_file=own_file),
        },
    )


@router.post("/api/events/{event_id}/update", response_class=HTMLResponse)
async def events_update(
    request: Request,
    event_id: int,
    title: str = Form(""),
    url: str = Form(""),
    description: str = Form(""),
    location: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    start_time: str = Form(""),
    image_url: str = Form(""),
    remove_image: str = Form(""),
    board: str = Form("1"),
    month: str = Form(""),
    day: str = Form(""),
    image: UploadFile = File(None),
):
    """Изменить мероприятие."""
    today = date.today()
    async with async_session() as db:
        ev = await _load_event(db, event_id)
        error = await _apply_form(
            ev,
            title=title,
            url=url,
            description=description,
            location=location,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            image_url=image_url,
            image=image,
            remove_image=bool((remove_image or "").strip()),
        )
        if not error:
            await db.commit()
        board_ctx = await _board_context(db, today, month, day)
        week_ctx = await evs.week_block(db, today)

    form_ctx = None
    if error:
        values = _event_form_values(ev)
        values.update({"title": title, "start_date": start_date, "end_date": end_date})
        form_ctx = _form_context(values, error=error, mode="edit", event_id=event_id)
    response = _refresh(request, _wants_board(board), board_ctx, week_ctx, form_ctx)
    if not error:
        response.headers["HX-Trigger"] = "planner-event-saved"
    return response


@router.post("/api/events/{event_id}/status", response_class=HTMLResponse)
async def events_status(
    request: Request,
    event_id: int,
    board: str = Form("1"),
    month: str = Form(""),
    day: str = Form(""),
):
    """Снять отметку: брони нет, поход отменён.

    Постановка «Иду» живёт в /visit: там спрашивается день и время сеанса, без
    них событие в календаре собрать не из чего.
    """
    today = date.today()
    async with async_session() as db:
        ev = await _load_event(db, event_id)
        ev.status = STATUS_NONE
        ev.visit_date = None
        ev.visit_time = None
        await db.commit()
        board_ctx = await _board_context(db, today, month, day)
        week_ctx = await evs.week_block(db, today)

    return _refresh(request, _wants_board(board), board_ctx, week_ctx)


# --------------------------------------------------------------------------
# поход: «иду» = билет на руках, значит есть день и время сеанса
# --------------------------------------------------------------------------

def _range_label(ev: Event) -> str:
    """«30.09.2026 — 13.10.2026» или одна дата — для текста ошибки."""
    start = evs.date_label(ev.start_date)
    edge = ev.last_day or ev.start_date
    return start if edge == ev.start_date else f"{start} — {evs.date_label(edge)}"


def _visit_context(
    ev: Event,
    today: date,
    *,
    board: str = "1",
    month: str = "",
    day: str = "",
    error: str = "",
) -> dict:
    """Значения мини-формы похода.

    День по умолчанию: уже сохранённый, иначе сегодня или первый день
    мероприятия — для будущей выставки это её открытие. Время: из брони, из
    начала мероприятия, иначе 12:00.
    """
    default_day = ev.visit_date or max(today, ev.start_date)
    edge = max(ev.last_day or ev.start_date, today)
    return {
        "visit_id": ev.id,
        "visit_title": ev.title,
        "visit_date_value": default_day.isoformat(),
        "visit_time_value": ev.visit_time or ev.start_time or "12:00",
        "visit_min": min(ev.start_date, today).isoformat(),
        "visit_max": edge.isoformat(),
        "visit_error": error,
        "visit_board": board,
        "visit_month": month,
        "visit_day": day,
    }


@router.get("/events/{event_id}/visit-form", response_class=HTMLResponse)
async def events_visit_form(
    request: Request,
    event_id: int,
    board: str = "1",
    month: str = "",
    day: str = "",
):
    """Мини-форма похода: открывается нажатием «Иду»."""
    today = date.today()
    async with async_session() as db:
        ev = await _load_event(db, event_id)
        context = _visit_context(ev, today, board=board, month=month, day=day)
    return templates.TemplateResponse(
        request, "partials/event_visit_form.html", {"request": request, **context}
    )


@router.post("/api/events/{event_id}/visit", response_class=HTMLResponse)
async def events_visit(
    request: Request,
    event_id: int,
    visit_date: str = Form(""),
    visit_time: str = Form(""),
    board: str = Form("1"),
    month: str = Form(""),
    day: str = Form(""),
):
    """«Иду»: сохранить бронь — день и время сеанса, и собрать событие календаря."""
    today = date.today()
    async with async_session() as db:
        ev = await _load_event(db, event_id)
        moment = evs.parse_form_date(visit_date)
        edge = ev.last_day or ev.start_date
        if not moment:
            context = _visit_context(
                ev,
                today,
                board=board,
                month=month,
                day=day,
                error="Нужен день похода: по нему собирается событие в календаре",
            )
            return templates.TemplateResponse(
                request, "partials/event_visit_form.html", {"request": request, **context}
            )
        if moment < ev.start_date or moment > edge:
            # Проверка не только в форме: прямой запрос мимо <input type=date>
            # иначе положил бы в календарь поход вне мероприятия.
            context = _visit_context(
                ev,
                today,
                board=board,
                month=month,
                day=day,
                error=(
                    f"День {evs.date_label(moment)} вне мероприятия "
                    f"({_range_label(ev)})"
                ),
            )
            return templates.TemplateResponse(
                request, "partials/event_visit_form.html", {"request": request, **context}
            )
        ev.visit_date = moment
        ev.visit_time = evs.parse_time(visit_time)
        ev.status = STATUS_GOING
        await db.commit()
        board_ctx = await _board_context(db, today, month, day)
        week_ctx = await evs.week_block(db, today)

    return _visit_refresh(request, board_ctx, week_ctx)


def _visit_refresh(request: Request, board_ctx: dict, week_ctx: dict):
    """Ответ на бронь: мини-форма закрывается, календарь и дашборд обновляются.

    Всё уезжает через hx-swap-oob: тогда htmx не трогает слот формы (он пустеет
    сам, потому что в ответе приезжает пустой слот), а обновляются оба блока —
    и доска на /events, и блок «мероприятия» на дашборде.
    """
    context = {
        "request": request,
        **board_ctx,
        **week_ctx,
    }
    response = templates.TemplateResponse(
        request, "partials/events_visit_refresh.html", context
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@router.get("/events/{event_id}/visit.ics")
async def events_visit_ics(event_id: int):
    """Файл календаря на поход: внутри событие и напоминание за сутки."""
    async with async_session() as db:
        ev = await _load_event(db, event_id)
        body = evs.build_ics(ev)
    if not body:
        raise HTTPException(
            status_code=404,
            detail="Сначала поставь «Иду» и день похода — тогда будет что положить в календарь",
        )
    # Имя файла: «Любовь — это....ics». Кириллицу отдаём через filename*, а
    # ASCII-вариант оставляем для старых клиентов.
    name = evs.ics_filename(ev)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="event-{event_id}.ics"; '
                f"filename*=UTF-8''{quote(name)}"
            )
        },
    )


@router.post("/api/events/{event_id}/delete", response_class=HTMLResponse)
async def events_delete(
    request: Request,
    event_id: int,
    board: str = Form("1"),
    month: str = Form(""),
    day: str = Form(""),
):
    """Удалить мероприятие вместе со своей картинкой."""
    today = date.today()
    async with async_session() as db:
        ev = await _load_event(db, event_id)
        evs.delete_upload(ev.image_file)
        await db.delete(ev)
        await db.commit()
        board_ctx = await _board_context(db, today, month, day)
        week_ctx = await evs.week_block(db, today)

    return _refresh(request, _wants_board(board), board_ctx, week_ctx)


async def _load_event(db, event_id: int) -> Event:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Мероприятие не найдено")
    return event
