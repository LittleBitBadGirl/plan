import os
import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from sqlalchemy import select, update

from app.utils.logger import app_logger
from app.services.ai_service import ai_service
from app.db.database import async_session
from app.models.missed import MissedMessage
from app.models.task import Task
from app.models.category import Category
from app.models.finance import Transaction
from app.models.goal import FinancialGoal
from app.config import settings

import httpx
import json
import re

router = Router()

# ─── Intent Detection ────────────────────────────────────────────────────────

# Слова-маркеры завершения задачи (в конце фразы или после дефиса/тире)
_DONE_WORDS = (
    r"сделала?|выполнила?|выполнено|готово|завершила?|закончила?|done|ок|окей"
)
_DONE_SUFFIX = re.compile(
    rf"\s*[-–—]\s*({_DONE_WORDS})\s*$", re.IGNORECASE
)
_DONE_PREFIX = re.compile(
    rf"^\s*({_DONE_WORDS})\s*[-–—:]\s*", re.IGNORECASE
)

# Маркеры списков для bulk-режима
_BULLET = re.compile(r"^\s*[-•*]\s+", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)


def _detect_intent(text: str) -> dict:
    """
    Определяет намерение пользователя.

    Возвращает dict с полями:
      intent: 'complete' | 'bulk_add' | 'add'
      task_name: str  (для complete — что выполнено)
      tasks: list[str]  (для bulk_add — список задач)
    """
    text = text.strip()

    # 1. Завершение: "X — сделала" или "сделала X"
    m = _DONE_SUFFIX.search(text)
    if m:
        task_name = text[: m.start()].strip().rstrip("-–—").strip()
        return {"intent": "complete", "task_name": task_name}

    m = _DONE_PREFIX.match(text)
    if m:
        task_name = text[m.end():].strip()
        return {"intent": "complete", "task_name": task_name}

    # 2. Bulk-add: многострочный текст с буллетами/нумерацией или просто несколько строк
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) > 1:
        # Убираем маркеры списков
        clean = [_BULLET.sub("", _NUMBERED.sub("", l)).strip() for l in lines]
        clean = [t for t in clean if t]
        if len(clean) > 1:
            return {"intent": "bulk_add", "tasks": clean}

    # 3. Bulk через запятую (для голосовых: "позвонить маме, написать отчёт, купить молоко")
    if text.count(",") >= 2:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) >= 2:
            return {"intent": "bulk_add", "tasks": parts}

    # 4. Обычное добавление
    return {"intent": "add", "task_name": text}

async def send_daily_plan(bot: Bot):
    """Отправка плана на день в 09:00 — структура совпадает с дашбордом."""
    from app.models.recurring import RecurringTask
    import json as _json

    admin_id = 163394712
    today = date.today()
    weekday_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
    today_weekday = weekday_map[today.weekday()]

    async with async_session() as db:
        # 1. Обычные задачи (как левая колонка дашборда)
        regular_result = await db.execute(
            select(Task).where(
                Task.due_date == today,
                Task.status.in_(["новая", "в_работе"]),
                Task.is_archived == False,
                Task.source.is_distinct_from("recurring"),
            ).order_by(Task.sort_order.asc())
        )
        regular_tasks = regular_result.scalars().all()

        # 2. Периодические задачи активные сегодня (как правая колонка Регулярные)
        recur_result = await db.execute(
            select(RecurringTask).where(RecurringTask.is_active == True)
        )
        all_recurring = recur_result.scalars().all()

        # Выполненные recurring сегодня — не показываем
        done_result = await db.execute(
            select(Task.title, Task.category_id).where(
                Task.due_date == today,
                Task.is_archived == True,
                Task.status == "выполнена",
            )
        )
        done_today = set((t.title, t.category_id) for t in done_result.all())

        recurring_today = []
        for rt in all_recurring:
            if (rt.title, rt.category_id) in done_today:
                continue
            if rt.end_date and today > rt.end_date:
                continue
            if today < rt.start_date:
                continue
            if rt.recurrence_type == "daily":
                recurring_today.append(rt)
            elif rt.recurrence_type == "weekly":
                days = rt.recurrence_days
                if isinstance(days, str):
                    try:
                        days = _json.loads(days)
                    except Exception:
                        days = []
                if days and today_weekday in days:
                    recurring_today.append(rt)
            elif rt.recurrence_type == "monthly":
                if today.day == rt.start_date.day:
                    recurring_today.append(rt)

    if not regular_tasks and not recurring_today:
        await bot.send_message(admin_id, "🌅 Доброе утро! На сегодня планов пока нет. Отличный день!")
        return

    text = f"🌅 Доброе утро! Твой план на сегодня ({today.strftime('%d.%m')}):\n\n"

    for t in regular_tasks:
        time_str = f" ⏰ {t.due_time.strftime('%H:%M')}" if getattr(t, 'due_time', None) else ""
        text += f"🔸 {t.title}{time_str}\n"

    if recurring_today:
        text += "\n🔄 Регулярные:\n"
        for rt in recurring_today:
            text += f"🔹 {rt.title}\n"

    text += "\nХорошего дня! 🚀"
    await bot.send_message(admin_id, text)


async def transcribe_audio_groq(file_path: Path) -> str:
    """Транскрибация аудио через Groq API (Whisper)"""
    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY не установлен")
        
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    
    with open(file_path, "rb") as audio_file:
        files = {"file": (file_path.name, audio_file, "audio/ogg")}
        data = {"model": "whisper-large-v3", "temperature": "0.0", "language": "ru"}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, files=files, data=data)
            response.raise_for_status()
            result = response.json()
            return result.get("text", "")


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    await message.answer(
        "👋 Привет! Я ваш планировщик задач.\n\n"
        "Просто напишите задачу — я добавлю её в план.\n"
        "Можете отправить голосовое или скриншот календаря.\n\n"
        "Команды:\n"
        "/plan — план на сегодня\n"
        "/tasks — все задачи на сегодня\n"
        "/stats — статистика\n"
    )

@router.message(Command("plan"))
async def cmd_plan(message: Message):
    """Утренний план (сводка на день)"""
    async with async_session() as db:
        today = date.today()
        result = await db.execute(select(Task).where(Task.due_date == today, Task.status != "выполнена"))
        tasks = result.scalars().all()
        
        if not tasks:
            await message.answer("🌅 Доброе утро! На сегодня пока нет запланированных задач.")
            return
            
        text = f"🌅 Доброе утро! План на сегодня ({today.strftime('%d.%m')}):\n\n"
        for t in tasks:
            time_str = f" ⏰ {t.due_time.strftime('%H:%M')}" if getattr(t, 'due_time', None) else ""
            text += f"🔸 {t.title}{time_str}\n"
            
        await message.answer(text)

@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    """Список задач с кнопками для выполнения"""
    async with async_session() as db:
        today = date.today()
        result = await db.execute(select(Task).where(Task.due_date == today, Task.status != "выполнена"))
        tasks = result.scalars().all()
        
        if not tasks:
            await message.answer("📋 На сегодня активных задач нет!")
            return
            
        await message.answer("📋 Ваши задачи на сегодня:")
        for task in tasks:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done_{task.id}")
            ]])
            time_str = f" [{task.due_time.strftime('%H:%M')}]" if getattr(task, 'due_time', None) else ""
            await message.answer(f"🔸 {task.title}{time_str}", reply_markup=kb)

@router.callback_query(F.data.startswith("done_"))
async def process_task_done(callback: CallbackQuery):
    """Обработка кнопки выполнения задачи"""
    task_id = int(callback.data.split("_")[1])
    async with async_session() as db:
        task_res = await db.execute(select(Task).where(Task.id == task_id))
        task = task_res.scalar_one_or_none()
        if task and task.status != "выполнена":
            task.status = "выполнена"
            task.completed_at = datetime.now()
            await db.commit()
            await callback.message.edit_text(f"✅ ~~{task.title}~~ (Выполнено)")
        else:
            await callback.message.answer("Задача уже выполнена или не найдена.")
    await callback.answer("Отмечено как выполненное!")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Команда /stats — статистика"""
    async with async_session() as db:
        today = date.today()
        result_total = await db.execute(select(Task).where(Task.due_date == today))
        tasks = result_total.scalars().all()
        completed = sum(1 for t in tasks if t.status == "выполнена")
        total = len(tasks)
        
        await message.answer(
            f"📊 Статистика за сегодня:\n\n"
            f"Выполнено: {completed}/{total}\n"
        )

async def _complete_task_by_name(task_name: str, message: Message) -> None:
    """Найти задачу по fuzzy-имени и пометить выполненной."""
    today = date.today()
    async with async_session() as db:
        result = await db.execute(
            select(Task).where(
                Task.due_date == today,
                Task.is_archived == False,
                Task.status.in_(["новая", "в_работе"]),
                Task.title.ilike(f"%{task_name}%"),
            )
        )
        matches = result.scalars().all()

    if not matches:
        # Ничего не нашли — предлагаем добавить как новую задачу
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="➕ Добавить как новую", callback_data=f"add_new:{task_name[:60]}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"),
        ]])
        await message.answer(
            f"🔍 Задача «{task_name}» не найдена в плане на сегодня.\n"
            "Добавить как новую задачу?",
            reply_markup=kb,
        )
        return

    if len(matches) == 1:
        task = matches[0]
        async with async_session() as db:
            res = await db.execute(select(Task).where(Task.id == task.id))
            t = res.scalar_one()
            t.status = "выполнена"
            t.completed_at = datetime.now()
            t.is_archived = True
            await db.commit()
        await message.answer(f"✅ Выполнено: {task.title}")
        return

    # Несколько совпадений — показываем кнопки выбора
    buttons = [
        [InlineKeyboardButton(text=f"✅ {t.title[:40]}", callback_data=f"complete_task:{t.id}")]
        for t in matches
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"🔍 Нашла {len(matches)} похожих задачи. Какую отметить выполненной?",
        reply_markup=kb,
    )


async def _process_bulk_tasks(tasks: list[str], message: Message, source: str = "telegram") -> None:
    """Создать несколько задач за один раз."""
    created = []
    skipped = []

    for raw in tasks:
        raw = raw.strip()
        if not raw:
            continue
        # Используем существующую логику — но без ответа на каждую задачу
        try:
            async with async_session() as db:
                cat_result = await db.execute(select(Category).where(Category.type == 'task'))
                categories = cat_result.scalars().all()
                cat_list = [{"id": c.id, "name": c.name, "is_global": c.is_global} for c in categories]

                clean_title = raw
                for word in ["завтра", "сегодня", "послезавтра"]:
                    clean_title = clean_title.replace(word, "").replace(word.capitalize(), "").strip()

                # dedup
                dup = await db.execute(
                    select(Task).where(
                        Task.title == clean_title,
                        Task.due_date == date.today(),
                        Task.is_archived == False,
                    )
                )
                if dup.scalar_one_or_none():
                    skipped.append(clean_title)
                    continue

                ai_result = await ai_service.categorize(raw, cat_list)
                category_id = ai_result.get("category_id")
                due_date_str = ai_result.get("due_date")
                task_due_date = date.fromisoformat(due_date_str) if due_date_str else date.today()

                task = Task(
                    title=clean_title,
                    category_id=category_id,
                    source=source,
                    due_date=task_due_date,
                    tags=", ".join(ai_result.get("tags", [])) or None,
                )
                db.add(task)
                await db.commit()
                created.append(clean_title)

        except Exception as e:
            app_logger.error(f"❌ Bulk task error ({raw}): {e}", exc_info=True)
            skipped.append(raw)

    lines = [f"✅ Добавлено {len(created)} задач:"]
    for t in created:
        lines.append(f"  🔸 {t}")
    if skipped:
        lines.append(f"\nℹ️ Уже были в плане (пропущено): {', '.join(skipped)}")

    await message.answer("\n".join(lines))


# ─── Callback handlers ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("complete_task:"))
async def cb_complete_task(callback: CallbackQuery):
    """Отметить конкретную задачу выполненной (из кнопок fuzzy-поиска)"""
    task_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        task = res.scalar_one_or_none()
        if task:
            task.status = "выполнена"
            task.completed_at = datetime.now()
            task.is_archived = True
            await db.commit()
            await callback.message.edit_text(f"✅ Выполнено: {task.title}")
        else:
            await callback.message.edit_text("❌ Задача не найдена.")
    await callback.answer()


@router.callback_query(F.data.startswith("add_new:"))
async def cb_add_new_task(callback: CallbackQuery):
    """Добавить задачу как новую (из кнопки 'не найдено')"""
    task_name = callback.data.split(":", 1)[1]
    await callback.message.edit_text(f"➕ Добавляю «{task_name}»...")
    await _process_and_create_task(task_name, callback.message, source="telegram")
    await callback.answer()


@router.callback_query(F.data == "cancel_action")
async def cb_cancel(callback: CallbackQuery):
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()


async def _process_and_create_task(text: str, message: Message, source: str = "telegram"):
    """Общая логика создания задачи из текста"""
    try:
        app_logger.info(f"📨 Обработка: \"{text}\" от chat_id={message.chat.id}")
        
        async with async_session() as db:
            message_hash = hashlib.sha256(f"{text}{message.date}".encode()).hexdigest()
            missed = MissedMessage(
                telegram_chat_id=message.chat.id,
                message_text=text,
                message_type=source,
                message_hash=message_hash,
            )
            db.add(missed)
            await db.flush()

            # Получаем все категории для Groq
            cat_result = await db.execute(select(Category).where(Category.type == 'task'))
            categories = cat_result.scalars().all()
            cat_list = [{"id": c.id, "name": c.name, "is_global": c.is_global} for c in categories]

            # Предварительный dedup: не создаём если такая задача уже есть на ту же дату
            due_date_preview = date.today()  # будет уточнена после AI, но для dedup берём сегодня
            clean_title_preview = text
            for word in ["завтра", "сегодня", "послезавтра"]:
                clean_title_preview = clean_title_preview.replace(word, "").replace(word.capitalize(), "").strip()

            dup_check = await db.execute(
                select(Task).where(
                    Task.title == clean_title_preview,
                    Task.due_date == due_date_preview,
                    Task.is_archived == False,
                )
            )
            if dup_check.scalar_one_or_none():
                await message.answer(f"ℹ️ «{clean_title_preview}» уже есть в плане на {due_date_preview.strftime('%d.%m.%Y')}")
                return

            # AI категоризация
            result = await ai_service.categorize(text, cat_list)
            category_id = result.get("category_id")
            tags_list = result.get("tags", [])
            tags_str = ", ".join(tags_list) if tags_list else None
            
            due_date_str = result.get("due_date")
            task_due_date = date.fromisoformat(due_date_str) if due_date_str else date.today()

            clean_title = text
            for word in ["завтра", "сегодня", "послезавтра"]:
                clean_title = clean_title.replace(word, "").replace(word.capitalize(), "").strip()

            task = Task(
                title=clean_title,
                category_id=category_id,
                source=source,
                due_date=task_due_date,
                tags=tags_str
            )
            db.add(task)
            await db.commit()
            
            cat_name = "Без категории"
            if category_id:
                cat_obj = next((c for c in categories if c.id == category_id), None)
                if cat_obj: cat_name = cat_obj.name

            app_logger.info(f"✅ Задача создана: ID={task.id} \"{clean_title}\" → {cat_name} | Date: {task_due_date}")
            resp_text = f"✅ Добавлено: {clean_title}\n📂 Категория: {cat_name}\n📅 Дата: {task_due_date.strftime('%d.%m.%Y')}"
            if tags_str: resp_text += f"\n🏷️ Теги: {tags_str}"
            await message.answer(resp_text)
            
    except Exception as e:
        app_logger.error(f"❌ Ошибка при создании задачи: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")

@router.message(F.text)
async def handle_text(message: Message):
    """Обработка текстового сообщения с определением намерения"""
    text = message.text.strip()
    if text.startswith("/"):
        return

    intent = _detect_intent(text)

    if intent["intent"] == "complete":
        await _complete_task_by_name(intent["task_name"], message)
    elif intent["intent"] == "bulk_add":
        await _process_bulk_tasks(intent["tasks"], message, source="telegram")
    else:
        await _process_and_create_task(text, message, source="telegram")

@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot):
    """Обработка голосового с определением намерения"""
    msg = await message.answer("🎙 Транскрибирую голосовое...")
    try:
        file_id = message.voice.file_id
        file = await bot.get_file(file_id)
        uploads_dir = settings.uploads_dir / "voice"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        file_path = uploads_dir / f"{file_id}.ogg"
        await bot.download_file(file.file_path, destination=file_path)
        text = await transcribe_audio_groq(file_path)
        await msg.edit_text(f"📝 Распознано:\n_{text}_", parse_mode="Markdown")

        if text.strip():
            intent = _detect_intent(text)
            if intent["intent"] == "complete":
                await _complete_task_by_name(intent["task_name"], message)
            elif intent["intent"] == "bulk_add":
                await _process_bulk_tasks(intent["tasks"], message, source="voice")
            else:
                await _process_and_create_task(text, message, source="voice")

        if file_path.exists():
            os.remove(file_path)
    except Exception as e:
        app_logger.error(f"❌ Ошибка обработки голосового: {e}", exc_info=True)
        await msg.edit_text("❌ Ошибка при транскрибации.")

@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot):
    """Обработка фото через Vision-модель (прямое зрение ИИ)"""
    msg = await message.answer("👁 Бот внимательно смотрит на картинку...")
    file_path = None
    try:
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        uploads_dir = settings.uploads_dir / "screenshots"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        
        file_ext = file.file_path.split('.')[-1] if '.' in file.file_path else 'jpg'
        filename = f"{file_id}.{file_ext}"
        file_path = uploads_dir / filename
        await bot.download_file(file.file_path, destination=file_path)
        
        # Прямой анализ зрения через Vision-модель
        vision_result = await ai_service.vision_analyze_screenshot(str(file_path))
        verdict = vision_result.get("type", "other")
        data = vision_result.get("data", {})
        
        app_logger.info(f"👁 Vision Verdict: {verdict}")

        if verdict == 'finance':
            items = data.get("items", [])
            if not items:
                await msg.edit_text("💰 Похоже на финансы, но я не смог разобрать детали. Сохранил для ручного ввода.")
                await _save_screenshot_to_db(file_path, "vision_finance_empty", message)
                return

            async with async_session() as db:
                count = 0
                for item in items:
                    amount = float(item.get("amount", 0))
                    if amount == 0: continue
                    
                    tx_date_str = item.get("date")
                    try:
                        tx_date = date.fromisoformat(tx_date_str) if tx_date_str else date.today()
                    except:
                        tx_date = date.today()
                        
                    desc = item.get("desc", "Операция из Vision")
                    cat_id = None

                    # 1. Merchant memory: проверяем историю — тот же мерчант = та же категория
                    cat_id = await _get_merchant_category(desc, db)

                    # 2. Если не знаем — пробуем подсказку от Vision AI
                    if cat_id is None:
                        cat_hint = item.get("category_hint")
                        if cat_hint:
                            cat_res = await db.execute(
                                select(Category).where(
                                    Category.name.ilike(f"%{cat_hint}%"),
                                    Category.type == 'finance',
                                )
                            )
                            cat_obj = cat_res.scalar_one_or_none()
                            if cat_obj:
                                cat_id = cat_obj.id

                    db.add(Transaction(date=tx_date, amount=amount, description=desc, category_id=cat_id, source="vision_screenshot"))
                    count += 1
                await db.commit()
            
            await msg.edit_text(f"✅ Успешно «увидел» операций: {count}\n💰 Все данные внесены в Финансы. Скриншот удален.")
            if file_path and file_path.exists(): os.remove(file_path)
            
        elif verdict == 'calendar':
            events = data.get("events", [])
            if not events:
                await msg.edit_text("📅 Похоже на календарь, но я не разглядел событий. Сохранил в базу.")
                await _save_screenshot_to_db(file_path, "vision_calendar_empty", message)
                return
                
            text_resp = f"📅 Вижу событий: {len(events)}\n\n"
            async with async_session() as db:
                for event in events:
                    due_time = None
                    try:
                        time_str = event.get("time", "")
                        if time_str:
                            time_parts = time_str.split(":")
                            due_time = datetime.strptime(f"{time_parts[0]}:{time_parts[1]}", "%H:%M").time()
                    except Exception:
                        pass

                    # Use event's own date; fall back to today only if missing/unparseable
                    event_date = date.today()
                    try:
                        date_str = event.get("date", "")
                        if date_str:
                            event_date = date.fromisoformat(date_str)
                    except Exception:
                        pass

                    db.add(Task(
                        title=event["title"],
                        due_date=event_date,
                        due_time=due_time,
                        source="vision",
                    ))
                    date_label = event_date.strftime("%d.%m")
                    time_label = event.get("time", "—")
                    text_resp += f"🔸 {date_label} {time_label} — {event['title']}\n"
                await db.commit()
            
            await msg.edit_text(text_resp + "\n✅ Задачи добавлены. Скриншот удален.")
            if file_path and file_path.exists(): os.remove(file_path)
        else:
            await _save_screenshot_to_db(file_path, "vision_unknown", message)
            await msg.edit_text("📥 Не совсем понял, что это. Сохранил скриншот в базу для ручного разбора.")
            
    except Exception as e:
        app_logger.error(f"❌ Ошибка Vision: {e}", exc_info=True)
        if file_path and file_path.exists():
            await _save_screenshot_to_db(file_path, "vision_error", message)
            await msg.edit_text("❌ Ошибка зрения. Сохранил скриншот для ручного разбора.")
        else:
            await msg.edit_text(f"❌ Критическая ошибка: {e}")

async def _get_merchant_category(description: str, db) -> int | None:
    """Merchant memory: ищем категорию по истории транзакций с таким же описанием.
    
    Возвращает category_id который использовался чаще всего для данного мерчанта.
    """
    if not description:
        return None
    result = await db.execute(
        select(Transaction.category_id)
        .where(
            Transaction.description == description,
            Transaction.category_id.is_not(None),
        )
        .order_by(Transaction.created_at.desc())
        .limit(10)
    )
    rows = result.scalars().all()
    if not rows:
        return None
    # Берём наиболее часто встречающуюся категорию
    from collections import Counter
    most_common = Counter(rows).most_common(1)
    return most_common[0][0] if most_common else None


async def _save_screenshot_to_db(file_path: Path, status: str, message: Message):
    """Сохранить информацию о скриншоте в БД для ручного разбора"""
    try:
        from app.models.screenshot import Screenshot
        async with async_session() as db:
            screenshot = Screenshot(
                file_path=str(file_path),
                ocr_status=status,
                created_at=datetime.now()
            )
            db.add(screenshot)
            await db.commit()
    except Exception as e:
        app_logger.error(f"❌ Не удалось сохранить скриншот в БД: {e}")
