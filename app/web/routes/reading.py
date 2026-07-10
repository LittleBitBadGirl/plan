from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.db.database import async_session
from app.models.shopping import ShoppingItem
from app.web.deps import _reading_list_response

router = APIRouter()

from app.services.shopping_service import archive_purchased_item


@router.post("/api/reading/create", response_class=HTMLResponse)
async def create_reading_item(request: Request, title: str = Form(...), content: str = Form("")):
    """Добавить пункт в список «Читать» (ссылка, книга или пост с заметками)."""
    clean = (title or "").strip()
    async with async_session() as db:
        if clean:
            item = ShoppingItem(title=clean, item_kind="reading")
            if content.strip():
                item.content = content.strip()
            db.add(item)
            await db.commit()
        return await _reading_list_response(request, db)


@router.post("/api/reading/{item_id}/toggle", response_class=HTMLResponse)
async def mark_reading_done(request: Request, item_id: int):
    """Отметить прочитанным → убрать из списка, отправить в архив."""
    async with async_session() as db:
        result = await db.execute(
            select(ShoppingItem).where(
                ShoppingItem.id == item_id,
                ShoppingItem.item_kind == "reading",
                ShoppingItem.is_archived == False,
            )
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Пункт не найден")
        archive_purchased_item(item)
        await db.commit()
        # Строка удаляется на фронте через hx-swap="delete"
        return HTMLResponse("")


@router.post("/api/reading/{item_id}/progress", response_class=HTMLResponse)
async def toggle_reading_progress(request: Request, item_id: int):
    """Переключить статус чтения: want_to_read → reading → done."""
    async with async_session() as db:
        result = await db.execute(
            select(ShoppingItem).where(
                ShoppingItem.id == item_id,
                ShoppingItem.item_kind == "reading",
                ShoppingItem.is_archived == False,
            )
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Пункт не найден")

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
        result = await db.execute(
            select(ShoppingItem).where(
                ShoppingItem.id == item_id,
                ShoppingItem.item_kind == "reading",
                ShoppingItem.is_archived == False,
            )
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Пункт не найден")

        if pages_total is not None:
            item.pages_total = pages_total
        if pages_read is not None:
            item.pages_read = pages_read
        await db.commit()
        return await _reading_list_response(request, db)


@router.delete("/api/reading/{item_id}", response_class=HTMLResponse)
async def delete_reading_item(request: Request, item_id: int):
    """Удалить активный пункт из списка «Читать»."""
    async with async_session() as db:
        result = await db.execute(
            select(ShoppingItem).where(
                ShoppingItem.id == item_id,
                ShoppingItem.item_kind == "reading",
                ShoppingItem.is_archived == False,
            )
        )
        item = result.scalar_one_or_none()
        if item:
            await db.delete(item)
            await db.commit()
            return await _reading_list_response(request, db)
    raise HTTPException(status_code=404, detail="Пункт не найден")
