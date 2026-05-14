import os
import hashlib
from datetime import date, datetime
from pathlib import Path

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

async def send_daily_plan(bot: Bot):
    """Отправка плана на день в 09:00"""
    async with async_session() as db:
        today = date.today()
        admin_id = 163394712
        
        result = await db.execute(select(Task).where(Task.due_date == today, Task.status != "выполнена"))
        tasks = result.scalars().all()
        
        if not tasks:
            await bot.send_message(admin_id, "🌅 Доброе утро! На сегодня планов пока нет. Отличный день!")
            return
            
        text = f"🌅 Доброе утро! Твой план на сегодня ({today.strftime('%d.%m')}):\n\n"
        for t in tasks:
            time_str = f" ⏰ {t.due_time.strftime('%H:%M')}" if getattr(t, 'due_time', None) else ""
            text += f"🔸 {t.title}{time_str}\n"
        
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
    """Обработка текстового сообщения"""
    text = message.text.strip()
    if text.startswith("/"): return
    await _process_and_create_task(text, message, source="telegram")

@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot):
    """Обработка голосового"""
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
        if text.strip(): await _process_and_create_task(text, message, source="voice")
        if file_path.exists(): os.remove(file_path)
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
                    cat_hint = item.get("category_hint")
                    cat_id = None
                    
                    if cat_hint:
                        cat_res = await db.execute(select(Category).where(Category.name.ilike(f"%{cat_hint}%")))
                        cat_obj = cat_res.scalar_one_or_none()
                        if cat_obj: cat_id = cat_obj.id
                        
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
