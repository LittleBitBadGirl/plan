from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from pydantic import BaseModel

from app.api.dependencies import get_db_session, verify_token
from app.models.category import Category

router = APIRouter(prefix="/api/categories", tags=["categories"], dependencies=[Depends(verify_token)])


class CategoryCreate(BaseModel):
    name: str
    is_global: bool = False
    parent_id: Optional[int] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    is_global: Optional[bool] = None
    parent_id: Optional[int] = None


@router.get("")
async def list_categories(
    db: AsyncSession = Depends(get_db_session),
):
    """Получить все категории"""
    result = await db.execute(select(Category).order_by(Category.is_global.desc(), Category.name))
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


@router.delete("/{category_id}")
async def delete_category(
    category_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Удалить категорию"""
    result = await db.execute(select(Category).where(Category.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    await db.delete(category)
    await db.flush()
    return {"message": "Category deleted"}
