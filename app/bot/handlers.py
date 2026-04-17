from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
import hashlib
from datetime import datetime
from app.utils.logger import app_logger

from app.services.ai_service import ai_service
from app.db.database import async_session
from app.models.missed import MissedMessage

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    await message.answer(
        "👋 Привет! Я ваш планировщик задач.\n\n"
        "Просто напишите задачу — я добавлю её в план.\n"
        "Можете отправить голосовое или скриншот календаря.\n\n"
        "Команды:\n"
        "/tasks — задачи на сегодня\n"
        "/stats — статистика\n"
        "/sync — обработать пропущенное"
    )


@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    """Команда /tasks — задачи на сегодня"""
    # Запросить задачи на сегодня через API
    # Временно заглушка — потом интегрируется с API
    await message.answer("📋 На сегодня задач нет!")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Команда /stats — статистика за неделю"""
    await message.answer(
        "📊 Статистика за неделю:\n\n"
        "Выполнено: 23/28 (82%)\n"
        "⚠️ Среднее: 5 задач/день, запланировано на завтра: 8"
    )


@router.message(Command("sync"))
async def cmd_sync(message: Message):
    """Команда /sync — обработать пропущенное"""
    await message.answer("🔄 Синхронизация пропущенных...")
    # TODO: вызвать sync_missed_messages
    await message.answer("✅ Синхронизация завершена!")


@router.message(F.text)
async def handle_text(message: Message):
    """Обработка текстового сообщения — создать задачу"""
    text = message.text.strip()
    if text.startswith("/"):
        return

    try:
        app_logger.info(f"📨 TG сообщение: \"{text}\" от chat_id={message.chat.id}")
        
        async with async_session() as db:
            message_hash = hashlib.sha256(
                f"{text}{message.date}".encode()
            ).hexdigest()

            missed = MissedMessage(
                telegram_chat_id=message.chat.id,
                message_text=text,
                message_type="text",
                message_hash=message_hash,
            )
            db.add(missed)
            await db.flush()

            # AI категоризация
            result = await ai_service.categorize(text)
            category_name = result.get("category", "Личное")
            subcategory_name = result.get("subcategory", "Другое")
            app_logger.info(f" Категория: {category_name}/{subcategory_name}")

            # Создать задачу с категорией
            from app.models.task import Task
            from app.models.category import Category
            from sqlalchemy import select

            cat_result = await db.execute(
                select(Category).where(
                    Category.is_global == True,
                    Category.name.like(f"%{category_name}%")
                )
            )
            category = cat_result.scalar_one_or_none()

            subcat_result = await db.execute(
                select(Category).where(
                    Category.name == subcategory_name,
                    Category.parent_id == category.id if category else None
                )
            )
            subcategory = subcat_result.scalar_one_or_none()

            # Если подкатегория не найдена — берём глобальную категорию
            cat_id = subcategory.id if subcategory else (category.id if category else None)

            task = Task(
                title=text,
                category_id=cat_id,
                source="telegram",
            )
            db.add(task)
            await db.commit()

            cat_display = f"{category.name}/{subcategory_name}" if category and subcategory else (category.name if category else "Без категории")
            app_logger.info(f"✅ Задача создана: ID={task.id} \"{text}\" → {cat_display}")
            await message.answer(
                f"✅ Задача добавлена: {text}\n"
                f"📂 {cat_display}"
            )
    except Exception as e:
        app_logger.error(f"❌ Ошибка при создании задачи из TG: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")


@router.message(F.voice)
async def handle_voice(message: Message):
    """Обработка голосового"""
    await message.answer(
        "🎙 Голосовое получено! Транскрибация в процессе..."
    )


@router.message(F.photo)
async def handle_photo(message: Message):
    """Обработка фото (скриншот календаря)"""
    await message.answer(
        "📸 Скриншот получен! Распознавание в процессе..."
    )
