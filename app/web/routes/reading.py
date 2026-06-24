from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.db.database import async_session
from app.models.shopping import ShoppingItem
from app.web.deps import _reading_list_response

router = APIRouter()

from app.services.shopping_service import archive_purchased_item


@router.post("/api/reading/create", response_class=HTMLResponse)
async def create_reading_item(request: Request, title: str = Form(...)):
    """Добавить пункт в список «Читать» (ссылка или название книги)."""
    clean = (title or "").strip()
    async with async_session() as db:
        if clean:
            db.add(ShoppingItem(title=clean, item_kind="reading"))
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
