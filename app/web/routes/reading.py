from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.db.database import async_session
from app.models.shopping import ShoppingItem
from app.services import reading_service as rs
from app.services.shopping_service import archive_purchased_item
from app.web.deps import (
    _reading_list_response,
    build_reading_context,
    reading_filters_from_request,
    templates,
)

router = APIRouter()


async def _reading_item(db, item_id: int) -> ShoppingItem:
    """Запись чтения по номеру, включая прочитанные (архив)."""
    result = await db.execute(
        select(ShoppingItem).where(
            ShoppingItem.id == item_id,
            ShoppingItem.item_kind == "reading",
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Пункт не найден")
    return item


@router.get("/reading", response_class=HTMLResponse)
async def reading_page(request: Request):
    """Страница «Читать»: полки категорий, теги и фильтры.

    Фильтры живут в строке запроса, поэтому срез можно сохранить в закладки:
    /reading?cat=ИИ+и+агенты&tag=агенты
    """
    filters = await reading_filters_from_request(request)
    async with async_session() as db:
        context = await build_reading_context(db, filters)
        # Подсказки для поля тегов: уже использованные теги.
        tag_options = [name for name, _ in await rs.tag_counts(db, None)]
    reading_now = sum(
        1
        for shelf in context["shelves"]
        for card in shelf["cards"]
        if card["status"] == "reading"
    )
    return templates.TemplateResponse(
        request,
        "reading.html",
        {
            **context,
            "tag_options": tag_options,
            "reading_now_count": reading_now,
        },
    )


@router.get("/reading/list", response_class=HTMLResponse)
async def reading_list(request: Request):
    """Только список: htmx подменяет его при смене фильтров."""
    async with async_session() as db:
        return await _reading_list_response(request, db)


@router.post("/api/reading/create", response_class=HTMLResponse)
async def create_reading_item(
    request: Request,
    title: str = Form(...),
    content: str = Form(""),
    category: str = Form(""),
    reading_format: str = Form(""),
    tags: str = Form(""),
    imported_from: str = Form(""),
    external_id: str = Form(""),
):
    """Добавить пункт в список «Читать» с категорией, форматом и тегами.

    Для импорта архивов: если пара imported_from + external_id уже есть,
    запись не создаётся второй раз — повторный прогон безопасен.
    """
    clean = (title or "").strip()
    source = (imported_from or "").strip()
    ext = (external_id or "").strip()
    async with async_session() as db:
        existing = None
        if source and ext:
            found = await db.execute(
                select(ShoppingItem).where(
                    ShoppingItem.imported_from == source,
                    ShoppingItem.external_id == ext,
                )
            )
            existing = found.scalars().first()
        if clean and not existing:
            item = ShoppingItem(
                title=clean,
                item_kind="reading",
                imported_from=source or None,
                external_id=ext or None,
            )
            if content.strip():
                item.content = content.strip()
            db.add(item)
            await db.flush()
            await rs.apply_taxonomy(db, item, category, reading_format, tags)
            await db.commit()
        return await _reading_list_response(request, db)


@router.post("/api/reading/{item_id}/meta", response_class=HTMLResponse)
async def update_reading_meta(
    request: Request,
    item_id: int,
    category: str = Form(""),
    reading_format: str = Form(""),
    tags: str = Form(""),
):
    """Изменить категорию, формат и теги записи чтения."""
    async with async_session() as db:
        item = await _reading_item(db, item_id)
        await rs.apply_taxonomy(db, item, category, reading_format, tags)
        await db.commit()
        return await _reading_list_response(request, db)


@router.post("/api/reading/{item_id}/toggle", response_class=HTMLResponse)
async def mark_reading_done(request: Request, item_id: int):
    """Отметить прочитанным → убрать из списка, отправить в архив."""
    async with async_session() as db:
        item = await _reading_item(db, item_id)
        archive_purchased_item(item)
        await db.commit()
        # Строка удаляется на фронте через hx-swap="delete"
        return HTMLResponse("")


@router.post("/api/reading/{item_id}/progress", response_class=HTMLResponse)
async def toggle_reading_progress(request: Request, item_id: int):
    """Переключить статус чтения: want_to_read → reading → done."""
    async with async_session() as db:
        item = await _reading_item(db, item_id)

        # Cycle: want_to_read → reading → done (archived)
        current = item.reading_status or "want_to_read"
        if current == "want_to_read":
            item.reading_status = "reading"
        elif current == "reading":
            archive_purchased_item(item)
        await db.commit()
        return await _reading_list_response(request, db)


@router.post("/api/reading/{item_id}/pages", response_class=HTMLResponse)
async def update_reading_pages(
    request: Request,
    item_id: int,
    pages_total: int = Form(None),
    pages_read: int = Form(None),
):
    """Обновить общее число страниц и/или прочитанные страницы."""
    async with async_session() as db:
        item = await _reading_item(db, item_id)
        if pages_total is not None:
            item.pages_total = pages_total
        if pages_read is not None:
            item.pages_read = pages_read
        await db.commit()
        return await _reading_list_response(request, db)


@router.delete("/api/reading/{item_id}", response_class=HTMLResponse)
async def delete_reading_item(request: Request, item_id: int):
    """Удалить пункт из списка «Читать» (в том числе из архива)."""
    async with async_session() as db:
        item = await _reading_item(db, item_id)
        await db.delete(item)
        await db.commit()
        return await _reading_list_response(request, db)
