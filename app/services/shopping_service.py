from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shopping import ShoppingItem


def active_shopping_filter():
    return (ShoppingItem.is_archived == False) & (ShoppingItem.item_kind == "purchase")


def active_reading_filter():
    return (ShoppingItem.is_archived == False) & (ShoppingItem.item_kind == "reading")


async def load_active_shopping(db: AsyncSession) -> list[ShoppingItem]:
    result = await db.execute(
        select(ShoppingItem)
        .where(active_shopping_filter())
        .order_by(ShoppingItem.created_at.desc())
    )
    return list(result.scalars().all())


async def load_active_reading(db: AsyncSession) -> list[ShoppingItem]:
    result = await db.execute(
        select(ShoppingItem)
        .where(active_reading_filter())
        .order_by(ShoppingItem.created_at.desc())
    )
    return list(result.scalars().all())


def archive_purchased_item(item: ShoppingItem) -> None:
    item.is_purchased = True
    item.is_archived = True
    item.purchased_at = datetime.utcnow()
