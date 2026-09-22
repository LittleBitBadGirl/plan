from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from pydantic import BaseModel

from app.api.dependencies import get_db_session, verify_token
from app.models.category import Category

router = APIRouter(prefix="/api/categories", tags=["categories"], dependencies=[Depends(verify_token)])


class MessageResponse(BaseModel):
    """Ответ операций без тела категории."""

    message: str


class CategoryCreate(BaseModel):
    name: str
    is_global: bool = False
    parent_id: Optional[int] = None
    # Тип категории: task (по умолчанию), reading, finance
    type: str = "task"


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    is_global: Optional[bool] = None
    parent_id: Optional[int] = None


@router.get("")
async def list_categories(
    category_type: Optional[str] = Query(default=None, alias="type"),
    db: AsyncSession = Depends(get_db_session),
):
    """Категории задач по умолчанию.

    ?type=reading — категории чтения, ?type=finance — финансовые,
    ?type=all — все подряд. Раньше метод отдавал вообще все категории, из-за
    чего категории чтения попадали в списки задач.
    """
    stmt = select(Category)
    if category_type != "all":
        stmt = stmt.where(Category.type == (category_type or "task"))
    result = await db.execute(stmt.order_by(Category.is_global.desc(), Category.name))
    return result.scalars().all()


@router.post("")
async def create_category(
    category_data: CategoryCreate,
    db: AsyncSession = Depends(get_db_session),
):
    """Создать категорию"""
    category = Category(
        name=category_data.name,
        is_global=category_data.is_global,
        parent_id=category_data.parent_id,
        type=category_data.type,
    )
    db.add(category)
    await db.flush()
    await db.refresh(category)
    return category


@router.put("/{category_id}")
async def update_category(
    category_id: int,
    category_data: CategoryUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    """Обновить категорию"""
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    update_data = category_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(category, key, value)

    await db.flush()
    await db.refresh(category)
    return category


@router.delete("/{category_id}", response_model=MessageResponse)
async def delete_category(
    category_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Удалить категорию.

    У категорий чтения сначала обнуляем связь с записями: иначе записи
    остаются со ссылкой на несуществующую категорию и висят мёртвым грузом —
    на странице чтения они уезжают на полку «без категории».
    """
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    if category.type == "reading":
        from sqlalchemy import update as sa_update

        from app.models.shopping import ShoppingItem

        await db.execute(
            sa_update(ShoppingItem)
            .where(ShoppingItem.category_id == category_id)
            .values(category_id=None)
        )

    await db.delete(category)
    await db.flush()
    await db.commit()
    return {"message": "Category deleted"}
