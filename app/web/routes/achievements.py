"""Раздел «Достижения»: ручные записи на двух полках — рабочее и личное.

Автоматику из задач не трогаем: «Карьерный капитал» живёт отдельно на /stats.
Здесь только то, что Вера записала сама (в том числе то, что ей назвали другие).
"""
from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.db.database import async_session
from app.models.achievement import SPHERE_KEYS, SPHERE_LABELS, Achievement
from app.web.deps import templates

router = APIRouter()

MAX_TEXT = 2000
MAX_TAG = 100
MAX_SOURCE = 100


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _cut(value: str | None, limit: int) -> str | None:
    text = _clean(value)
    return text[:limit] if text else None


def _sphere(value: str | None) -> str:
    return value if value in SPHERE_KEYS else "personal"


def _parse_date(value: str | None) -> date | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _order_by():
    """Свежие сверху; записи без даты — в конце полки."""
    return (
        Achievement.happened_on.is_(None),
        Achievement.happened_on.desc(),
        Achievement.created_at.desc(),
        Achievement.id.desc(),
    )


async def _load_shelves(db) -> dict[str, list[Achievement]]:
    result = await db.execute(
        select(Achievement)
        .where(Achievement.is_archived == False)  # noqa: E712
        .order_by(*_order_by())
    )
    items = list(result.scalars().all())
    return {key: [a for a in items if a.sphere == key] for key in SPHERE_KEYS}


def _board_context(shelves: dict[str, list[Achievement]]) -> dict:
    return {
        "shelves": shelves,
        "labels": SPHERE_LABELS,
        "total": sum(len(items) for items in shelves.values()),
    }


async def _render_board(request: Request) -> HTMLResponse:
    async with async_session() as db:
        shelves = await _load_shelves(db)
    return templates.TemplateResponse(
        request, "partials/achievements_board.html", _board_context(shelves)
    )


async def _render_widget(request: Request) -> HTMLResponse:
    async with async_session() as db:
        shelves = await _load_shelves(db)
    return templates.TemplateResponse(
        request, "partials/achievements_widget.html", _board_context(shelves)
    )


@router.get("/achievements", response_class=HTMLResponse)
async def achievements_page(request: Request):
    async with async_session() as db:
        shelves = await _load_shelves(db)
    return templates.TemplateResponse(
        request, "achievements.html", _board_context(shelves)
    )


@router.get("/achievements/board", response_class=HTMLResponse)
async def achievements_board(request: Request):
    """Полка целиком — ответ на добавление, правку и удаление."""
    return await _render_board(request)


@router.post("/achievements", response_class=HTMLResponse)
async def create_achievement(
    request: Request,
    text: str = Form(""),
    sphere: str = Form("personal"),
    tag: str = Form(""),
    happened_on: str = Form(""),
    source: str = Form(""),
    widget: str = Form(""),
):
    body = _clean(text)
    if body:
        async with async_session() as db:
            db.add(
                Achievement(
                    text=body[:MAX_TEXT],
                    sphere=_sphere(sphere),
                    tag=_cut(tag, MAX_TAG),
                    happened_on=_parse_date(happened_on),
                    source=_cut(source, MAX_SOURCE),
                )
            )
            await db.commit()
    return await (_render_widget(request) if widget else _render_board(request))


@router.post("/achievements/{ach_id}", response_class=HTMLResponse)
async def update_achievement(
    request: Request,
    ach_id: int,
    text: str = Form(""),
    sphere: str = Form("personal"),
    tag: str = Form(""),
    happened_on: str = Form(""),
    source: str = Form(""),
):
    body = _clean(text)
    if body:
        async with async_session() as db:
            item = await db.get(Achievement, ach_id)
            if item is not None:
                item.text = body[:MAX_TEXT]
                item.sphere = _sphere(sphere)
                item.tag = _cut(tag, MAX_TAG)
                item.happened_on = _parse_date(happened_on)
                item.source = _cut(source, MAX_SOURCE)
                await db.commit()
    return await _render_board(request)


@router.post("/achievements/{ach_id}/delete", response_class=HTMLResponse)
async def delete_achievement(request: Request, ach_id: int):
    async with async_session() as db:
        item = await db.get(Achievement, ach_id)
        if item is not None:
            await db.delete(item)
            await db.commit()
    return await _render_board(request)
