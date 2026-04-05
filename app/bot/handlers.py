from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

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
    # Сохранить в missed_messages (через БД)
    # TODO: AI категоризация через очередь
    await message.answer(
        f"✅ Задача добавлена: {text}\n"
        "🤖 Категоризация в процессе..."
    )


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
