from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.category import Category


# Структура категорий из спецификации
CATEGORIES = [
    # Глобальные категории
    {"name": "🏢 Работа", "is_global": True},
    {"name": "🏠 Личное", "is_global": True},
    {"name": "📚 Обучение", "is_global": True},
    {"name": "🔄 Периодические", "is_global": True},
]

SUBCATEGORIES = [
    # Работа
    {"name": "Финансы", "parent": "🏢 Работа"},
    {"name": "Документы", "parent": "🏢 Работа"},
    {"name": "Проекты", "parent": "🏢 Работа"},
    {"name": "Инфраструктура", "parent": "🏢 Работа"},
    {"name": "Контент", "parent": "🏢 Работа"},

    # Личное
    {"name": "Семья", "parent": "🏠 Личное"},
    {"name": "Здоровье", "parent": "🏠 Личное"},
    {"name": "Финансы", "parent": "🏠 Личное"},
    {"name": "Цифровая гигиена", "parent": "🏠 Личное"},
    {"name": "Покупки", "parent": "🏠 Личное"},
    {"name": "Социум", "parent": "🏠 Личное"},
    {"name": "Пет-проекты", "parent": "🏠 Личное"},
    {"name": "Свои сайты", "parent": "🏠 Личное"},

    # Обучение
    {"name": "Курсы", "parent": "📚 Обучение"},
    {"name": "Инструменты", "parent": "📚 Обучение"},
    {"name": "Контент", "parent": "📚 Обучение"},

    # Периодические
    {"name": "Встречи с друзьями", "parent": "🔄 Периодические"},
    {"name": "Забота о себе", "parent": "🔄 Периодические"},
]


async def seed_categories(db: AsyncSession):
    """Посев начальных категорий, если БД пуста"""
    result = await db.execute(select(Category))
    existing = result.scalars().all()
    if existing:
        return  # Уже есть категории

    # Создаём глобальные категории
    global_categories = {}
    for cat_data in CATEGORIES:
        cat = Category(name=cat_data["name"], is_global=True)
        db.add(cat)
        global_categories[cat_data["name"]] = cat

    await db.flush()

    # Создаём подкатегории
    for sub_data in SUBCATEGORIES:
        parent_name = sub_data["parent"]
        parent = global_categories.get(parent_name)
        if parent:
            sub = Category(
                name=sub_data["name"],
                is_global=False,
                parent_id=parent.id,
            )
            db.add(sub)

    await db.flush()
