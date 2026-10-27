"""Удаление категории без потеряшек.

Категорию нельзя просто удалить, если на неё ссылаются транзакции, задачи,
привычки, регулярные задачи, цели, записи чтения или подкатегории: строка
остаётся с `category_id`, которого нет в справочнике, и пропадает из сводок
(финансовая сводка собирается INNER JOIN, списки фильтруют по категории).
Так в базе появились 29 «осиротевших» id на 277 тыс ₽ и сиротская задача.
Внешние ключи в SQLite не включены (`db/database.py`), база такое не ловит —
значит проверять обязан код.

Правило: если категория используется, сначала спрашиваем, куда перенести.
Без явной цели сервер ничего не удаляет и отвечает 409 с составом
использования и списком кандидатов — интерфейс показывает выбор.

Цель может быть:
  * id категории — переносим туда (тот же type, не сама, не её потомок);
  * `0` — «без категории»: записи остаются с NULL, подкатегории в корень;
  * отсутствует — для обычных категорий 409, для чтения та же «полка
    без категории», потому что спрашивать не о чем.
"""

from typing import Optional

from sqlalchemy import func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.finance import Transaction
from app.models.goal import FinancialGoal
from app.models.habit import Habit
from app.models.recurring import RecurringTask
from app.models.shopping import ShoppingItem
from app.models.task import Task

# Что держится за категорией: (модель, ключ в ответе 409)
LINKED_MODELS = (
    (Transaction, "transactions"),
    (Task, "tasks"),
    (Habit, "habits"),
    (RecurringTask, "recurring"),
    (FinancialGoal, "goals"),
    (ShoppingItem, "items"),
)


async def category_usage(db: AsyncSession, category_id: int) -> dict:
    """Сколько записей держится за категорией, по каждому виду."""
    usage = {}
    for model, key in LINKED_MODELS:
        count = (
            await db.execute(
                select(func.count()).select_from(model).where(model.category_id == category_id)
            )
        ).scalar() or 0
        usage[key] = int(count)
    usage["children"] = int(
        (
            await db.execute(
                select(func.count()).select_from(Category).where(Category.parent_id == category_id)
            )
        ).scalar()
        or 0
    )
    return usage


def usage_total(usage: dict) -> int:
    """Всё, что потеряется, если удалить категорию молча."""
    return sum(value for key, value in usage.items() if key != "children") + usage["children"]


async def _descendant_ids(db: AsyncSession, category_id: int) -> set:
    """Все потомки категории: перенос внутрь собственного поддерева даёт петлю."""
    ids = set()
    frontier = {category_id}
    while frontier:
        rows = (
            await db.execute(select(Category.id).where(Category.parent_id.in_(frontier)))
        ).scalars().all()
        new = set(rows) - ids - {category_id}
        if not new:
            break
        ids |= new
        frontier = new
    return ids


async def candidate_targets(db: AsyncSession, category: Category, only_global: bool = False) -> list:
    """Куда можно перенести записи: категории того же типа, кроме себя и потомков.

    Если у категории есть подкатегории, отдаём только глобальные цели: тогда
    дети уезжают на один уровень, а не под подкатегорию (третий уровень
    в Планере не поддерживается).
    """
    excluded = await _descendant_ids(db, category.id)
    excluded.add(category.id)
    rows = (
        await db.execute(
            select(Category)
            .where(Category.type == category.type)
            .order_by(Category.is_global.desc(), Category.name)
        )
    ).scalars().all()
    parents = {c.id: c.name for c in rows}
    out = []
    for c in rows:
        if c.id in excluded:
            continue
        if only_global and not c.is_global:
            continue
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "is_global": bool(c.is_global),
                "parent_name": parents.get(c.parent_id or 0, ""),
            }
        )
    return out


async def default_target(db: AsyncSession, category: Category) -> Optional[int]:
    """Куда переносить по умолчанию: родитель, иначе «Прочее» того же типа."""
    if category.parent_id:
        return category.parent_id
    prochee = (
        await db.execute(
            select(Category.id)
            .where(
                Category.name == "Прочее",
                Category.type == category.type,
                Category.is_global == True,  # noqa: E712 — SQL-сравнение, не Python
                Category.id != category.id,
            )
            .order_by(Category.id)
        )
    ).scalars().first()
    return prochee


async def conflict_payload(db: AsyncSession, category: Category, reason: str = "in_use") -> dict:
    """Тело ответа 409: что мешает удалить и куда можно перенести."""
    usage = await category_usage(db, category.id)
    return {
        "error": "invalid_target" if reason == "invalid_target" else "category_in_use",
        "reason": reason,
        "detail": (
            "Так переносить нельзя: выбери другую категорию"
            if reason == "invalid_target"
            else "Категория используется: выбери, куда перенести записи"
        ),
        "category": {"id": category.id, "name": category.name, "type": category.type},
        "usage": usage,
        "allow_null": True,
        "default_move_to": await default_target(db, category),
        "candidates": await candidate_targets(db, category, only_global=bool(usage["children"])),
    }


async def delete_category_with_move(
    db: AsyncSession,
    category: Category,
    move_to: Optional[int] = None,
    null_target: bool = False,
) -> Optional[dict]:
    """Удалить категорию, перенеся записи.

    Возвращает None при успехе и payload конфликта, если нужен выбор цели.
    `move_to=0` и `null_target=True` — «без категории».
    """
    usage = await category_usage(db, category.id)
    reading = category.type == "reading"

    if move_to == 0:
        null_target = True
        move_to = None

    target = None
    if move_to is not None:
        target = (
            await db.execute(select(Category).where(Category.id == move_to))
        ).scalar_one_or_none()
        if target is None or target.id == category.id:
            return await conflict_payload(db, category, reason="invalid_target")
        if target.type != category.type:
            return await conflict_payload(db, category, reason="invalid_target")
        if target.id in await _descendant_ids(db, category.id):
            return await conflict_payload(db, category, reason="invalid_target")

    # Спрашиваем там, где есть что терять и цель не выбрана.
    # Категории чтения не спрашиваем: их записи уезжают на полку «без категории».
    if target is None and not null_target and not reading and usage_total(usage):
        return await conflict_payload(db, category)

    new_id = target.id if target is not None else None
    for model, _key in LINKED_MODELS:
        await db.execute(
            sa_update(model).where(model.category_id == category.id).values(category_id=new_id)
        )
    await db.execute(
        sa_update(Category).where(Category.parent_id == category.id).values(parent_id=new_id)
    )

    # Цель могли удалить параллельно, пока мы переносили: перенос в мёртвый id
    # вернул бы ту же болезнь, которую лечим. Проверяем и откатываемся.
    if new_id is not None:
        alive = (
            await db.execute(select(Category.id).where(Category.id == new_id))
        ).scalar_one_or_none()
        if alive is None:
            await db.rollback()
            return await conflict_payload(db, category, reason="invalid_target")

    await db.delete(category)
    await db.flush()
    return None
