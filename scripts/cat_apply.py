"""Проставить категорию задаче и отправить Вере ревью с кнопками в Telegram.
БЕЗОПАСНО: меняет ТОЛЬКО category_id одной задачи, никаких удалений.
Запуск: docker exec task_planner python3 scripts/cat_apply.py <task_id> <cat_id>
"""
import sys
import asyncio
import httpx
from sqlalchemy import select
from app.db.database import async_session
from app.models.task import Task
from app.models.category import Category
from app.config import settings


async def main(task_id: int, cat_id: int):
    async with async_session() as db:
        t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not t:
            print("ERR no task", task_id)
            return
        c = (await db.execute(select(Category).where(Category.id == cat_id))).scalar_one_or_none()
        if not c:
            print("ERR no category", cat_id)
            return
        t.category_id = cat_id
        await db.commit()
        cat_name = c.name
        title = t.title

    token = settings.telegram_bot_token
    chat = settings.telegram_admin_chat_id
    if not token or not chat:
        print("ERR telegram not configured")
        return

    payload = {
        "chat_id": chat,
        "text": f"🗂 {title}\n📂 {cat_name}",
        "reply_markup": {"inline_keyboard": [[
            {"text": "✓ ок", "callback_data": f"catok:{task_id}"},
            {"text": "✏️ изменить", "callback_data": f"catchg:{task_id}"},
        ]]},
    }
    async with httpx.AsyncClient() as cl:
        r = await cl.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload, timeout=10.0,
        )
        print("OK" if r.status_code == 200 else "ERR", r.status_code, r.text[:150])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: cat_apply.py <task_id> <cat_id>")
        sys.exit(1)
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
