import sys
import asyncio
import json
from datetime import datetime
from app.db.database import async_session
from app.models.category import Category
from app.models.task import Task
from sqlalchemy import select

async def main():
    if len(sys.argv) < 2:
        print("Usage: python import_meetings.py '[{\"title\": \"...\", \"date\": \"YYYY-MM-DD\"}, ...]'")
        return

    try:
        meetings_data = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print("❌ Invalid JSON format")
        return

    async with async_session() as db:
        # 1. Find Category "Работа"
        result = await db.execute(select(Category).where(Category.name.ilike('%работа%')))
        work_cat = result.scalar_one_or_none()
        if not work_cat:
            print("❌ Category 'Работа' not found!")
            return

        # 2. Find Subcategory "Созвоны"
        sub_result = await db.execute(select(Category).where(Category.name == 'Созвоны', Category.parent_id == work_cat.id))
        sub_cat = sub_result.scalar_one_or_none()
        if not sub_cat:
            print("❌ Subcategory 'Созвоны' not found! Create it first.")
            return

        count = 0
        for item in meetings_data:
            title = item['title']
            date_str = item['date']
            due_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            # Check for duplicates
            exists = await db.execute(select(Task).where(Task.title == title, Task.due_date == due_date))
            if not exists.scalar_one_or_none():
                task = Task(
                    title=title, 
                    category_id=sub_cat.id, 
                    due_date=due_date, 
                    source='import', 
                    status='новая'
                )
                db.add(task)
                count += 1
        
        await db.commit()
        print(f"✅ Added {count} meetings to 'Созвоны'!")
        if count == 0:
            print("ℹ️ All meetings were already in the plan.")

asyncio.run(main())
