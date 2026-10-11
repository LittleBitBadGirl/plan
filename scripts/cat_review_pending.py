"""Вывести JSON: задачи без категории + категории-листья — для крон-категоризатора.
Запуск: docker exec task_planner python3 scripts/cat_review_pending.py

Правило отбора живёт в app/services/categorize_pending_service.py (там же
комментарий, почему задачи из регулярных с уже присвоенной категорией не
показываются повторно).
"""
import os
import sys
import json
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import async_session
from app.services.categorize_pending_service import (
    load_leaf_categories,
    load_pending_tasks,
)


async def main():
    async with async_session() as db:
        tasks = await load_pending_tasks(db)
        cats = await load_leaf_categories(db)
    print(json.dumps({
        "tasks": [{"id": r[0], "title": r[1]} for r in tasks],
        "categories": [{"id": r[0], "name": r[1]} for r in cats],
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
