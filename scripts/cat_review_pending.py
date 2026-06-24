"""Вывести JSON: задачи без категории + категории-листья — для крон-категоризатора.
Запуск: docker exec task_planner python3 scripts/cat_review_pending.py
"""
import json
import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.models.task import Task
from app.models.category import Category

LIMIT = 15


async def main():
    async with async_session() as db:
        tasks = (await db.execute(
            select(Task.id, Task.title)
            .where(
                Task.category_id.is_(None),
                Task.item_kind == "task",
                Task.parent_task_id.is_(None),
            )
            .order_by(Task.id.desc())
            .limit(LIMIT)
        )).all()
        cats = (await db.execute(
            select(Category.id, Category.name)
            .where(Category.type == "task", Category.is_global == False)
            .order_by(Category.name)
        )).all()
    print(json.dumps({
        "tasks": [{"id": r[0], "title": r[1]} for r in tasks],
        "categories": [{"id": r[0], "name": r[1]} for r in cats],
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
