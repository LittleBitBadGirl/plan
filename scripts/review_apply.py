"""Проставить категорию задаче + предложить «чистый» текст и вынесенную дату,
отправить Вере объединённое ревью с кнопками в Telegram.

БЕЗОПАСНО:
  - category_id применяется сразу (как в старом cat_apply.py);
  - чистый текст и дата НЕ применяются молча — кладутся в suggested_title /
    suggested_due_date и применяются ТОЛЬКО по кнопке «✓ применить» в боте.
Меняет ТОЛЬКО эти поля одной задачи. Никаких удалений.

Запуск:
  docker exec task_planner python3 scripts/review_apply.py \
      <task_id> <cat_id> <due_date|-> <clean_title|->

  due_date  — ISO YYYY-MM-DD или "-" (нет даты в тексте)
  clean_title — очищенный текст или "-" (чистить нечего)
"""
import os
import sys
import asyncio
from datetime import datetime, date

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.db.database import async_session
from app.models.task import Task
from app.models.category import Category
from app.config import settings


def _parse_due(s: str):
    """ISO YYYY-MM-DD -> date | None. Невалидное/'-' -> None (не падаем)."""
    if not s or s.strip() in ("-", ""):
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _clean(s: str):
    if not s or s.strip() in ("-", ""):
        return None
    return s.strip()


async def main(task_id: int, cat_id: int, due_raw: str, title_raw: str):
    new_due = _parse_due(due_raw)
    new_title = _clean(title_raw)

    async with async_session() as db:
        t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not t:
            print("ERR no task", task_id)
            return
        c = (await db.execute(select(Category).where(Category.id == cat_id))).scalar_one_or_none()
        if not c:
            print("ERR no category", cat_id)
            return

        # Категорию ставим сразу.
        t.category_id = cat_id

        # Текст: предлагаем только если реально отличается от текущего.
        sugg_title = None
        if new_title and new_title != (t.title or "").strip():
            sugg_title = new_title
        # Дата: предлагаем только если её ещё нет ИЛИ отличается.
        sugg_due = None
        if new_due and new_due != t.due_date:
            sugg_due = new_due

        t.suggested_title = sugg_title
        t.suggested_due_date = sugg_due
        await db.commit()

        orig_title = t.title
        cat_name = c.name

    has_suggestion = bool(sugg_title or sugg_due)

    # Текст сообщения
    lines = [f"\U0001f5c2 {orig_title}", f"\U0001f4c2 {cat_name}"]
    if sugg_title:
        lines.append(f"\u2702\ufe0f {sugg_title}")
    if sugg_due:
        lines.append(f"\U0001f4c5 {sugg_due.strftime('%d.%m.%Y')}")
    text = "\n".join(lines)

    # Кнопки
    if has_suggestion:
        keyboard = [[
            {"text": "\u2713 применить", "callback_data": f"txtok:{task_id}"},
            {"text": "\u270f\ufe0f категория", "callback_data": f"catchg:{task_id}"},
            {"text": "\u2716\ufe0f как есть", "callback_data": f"txtskip:{task_id}"},
        ]]
    else:
        keyboard = [[
            {"text": "\u2713 ок", "callback_data": f"catok:{task_id}"},
            {"text": "\u270f\ufe0f изменить", "callback_data": f"catchg:{task_id}"},
        ]]

    token = settings.telegram_bot_token
    chat = settings.telegram_admin_chat_id
    if not token or not chat:
        print("ERR telegram not configured")
        return

    payload = {
        "chat_id": chat,
        "text": text,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    async with httpx.AsyncClient() as cl:
        r = await cl.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload, timeout=10.0,
        )
        print("OK" if r.status_code == 200 else "ERR", r.status_code, r.text[:160])


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("usage: review_apply.py <task_id> <cat_id> <due_date|-> <clean_title|->")
        sys.exit(1)
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]))
