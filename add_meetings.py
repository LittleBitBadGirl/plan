import asyncio
from app.db.database import async_session
from app.models.category import Category
from app.models.task import Task
from sqlalchemy import select
from datetime import date

async def main():
    async with async_session() as db:
        # Ищем категорию "Работа" (ищем по части названия, чтобы найти даже с эмодзи)
        result = await db.execute(select(Category).where(Category.name.contains('Работа')))
        work_cat = result.scalar_one_or_none()
        
        if not work_cat:
            print("❌ Категория 'Работа' не найдена!")
            return
        print(f"🔍 Найдена: {work_cat.name}")

        # Создаем подкатегорию "Созвоны"
        sub_result = await db.execute(select(Category).where(
            Category.name == 'Созвоны', 
            Category.parent_id == work_cat.id
        ))
        sub_cat = sub_result.scalar_one_or_none()
        
        if not sub_cat:
            sub_cat = Category(name='Созвоны', is_global=False, parent_id=work_cat.id)
            db.add(sub_cat)
            await db.flush()
            print("✅ Создана: Созвоны")

        meetings = [
            ("13.04 Пн 11:00 Планирование", date(2026, 4, 13)),
            ("13.04 Пн 12:00 Группхедский статус", date(2026, 4, 13)),
            ("13.04 Пн 14:30 Зеленый марафон", date(2026, 4, 13)),
            ("13.04 Пн 16:00 Зеленый марафон", date(2026, 4, 13)),
            ("14.04 Вт 09:00 Риторика", date(2026, 4, 14)),
            ("14.04 Вт 12:00 Общая встреча", date(2026, 4, 14)),
            ("15.04 Ср 11:00 Встреча", date(2026, 4, 15)),
            ("15.04 Ср 12:00 1-1 Лышков", date(2026, 4, 15)),
            ("15.04 Ср 16:00 Dalee Atol Статус", date(2026, 4, 15)),
            ("16.04 Чт 13:00 1-1 Лена", date(2026, 4, 16)),
            ("16.04 Чт 14:30 Зеленый марафон", date(2026, 4, 16)),
            ("17.04 Пт 10:30 Битровые тесты", date(2026, 4, 17)),
            ("17.04 Пт 12:00 гр Осолодкина", date(2026, 4, 17)),
            ("17.04 Пт 14:30 Лидирование", date(2026, 4, 17)),
        ]

        for title, d_date in meetings:
            task = Task(title=title, category_id=sub_cat.id, due_date=d_date, source='calendar_import', status='новая')
            db.add(task)
        
        await db.commit()
        print(f"🎉 Добавлено {len(meetings)} встреч!")

asyncio.run(main())
