import os
import hashlib
from datetime import date, datetime
from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from sqlalchemy import select

from app.utils.logger import app_logger
from app.services.ai_service import ai_service
from app.db.database import async_session
from app.models.missed import MissedMessage
from app.models.task import Task
from app.models.category import Category
from app.models.finance import Transaction
from app.config import settings

from app.bot.task_logic import (
    detect_intent,
    create_task_from_text,
    mark_task_complete,
    plan_task_for_today,
    fetch_today_tasks,
    fetch_backlog_tasks,
    find_tasks_to_complete,
    format_today_stats,
)

import httpx
import re

router = Router()

# Слова-маркеры завершения задачи
_DONE_WORDS = (
    r"сделала?|выполнила?|выполнено|готово|завершила?|закончила?|done|ок|окей"
)
_DONE_SUFFIX = re.compile(rf"\s*[-–—]\s*({_DONE_WORDS})\s*$", re.IGNORECASE)
_DONE_PREFIX = re.compile(rf"^\s*({_DONE_WORDS})\s*[-–—:]\s*", re.IGNORECASE)
_BULLET = re.compile(r"^\s*[-•*]\s+", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)


def _detect_intent(text: str) -> dict:
    return detect_intent(
        text,
        done_suffix=_DONE_SUFFIX,
        done_prefix=_DONE_PREFIX,
        bullet=_BULLET,
        numbered=_NUMBERED,
    )


async def send_daily_plan(bot: Bot):
    """Отправка плана на день в 09:00 — встречи, задачи, регулярные."""
    from app.services.daily_plan_service import build_daily_plan_text, refresh_calendar_for_plan

    chat_id = settings.telegram_admin_chat_id
    if not chat_id:
        app_logger.warning("TELEGRAM_ADMIN_CHAT_ID не задан — утренний план не отправлен")
        return

    await refresh_calendar_for_plan()

    async with async_session() as db:
        text = await build_daily_plan_text(db)

    if not text:
        await bot.send_message(
            chat_id,
            "🌅 Доброе утро! На сегодня планов пока нет. Отличный день!",
        )
        return

    await bot.send_message(chat_id, text)


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
        "👋 Привет! Я планировщик задач.\n\n"
        "📝 Текст или голос → задача на сегодня\n"
        "📥 «бэклог: …» или список строк → в бэклог без даты\n"
        "✅ «задача — сделала» → отметить выполненной\n"
        "🎙 Голосовое и скриншот календаря/финансов — тоже ок\n\n"
        "Команды:\n"
        "/plan — план на сегодня (встречи + задачи)\n"
        "/tasks — задачи на сегодня\n"
        "/backlog — бэклог без даты\n"
        "/stats — прогресс дня\n"
    )


@router.message(Command("plan"))
async def cmd_plan(message: Message):
    """План на сегодня: встречи + задачи + регулярные."""
    from app.services.daily_plan_service import build_daily_plan_text, refresh_calendar_for_plan

    await refresh_calendar_for_plan()

    async with async_session() as db:
        text = await build_daily_plan_text(db)

    if not text:
        await message.answer("🌅 На сегодня планов пока нет. Отличный день!")
        return

    await message.answer(text)


@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    """Список задач на сегодня с кнопками"""
    async with async_session() as db:
        tasks = await fetch_today_tasks(db)

        if not tasks:
            await message.answer("📋 На сегодня активных задач нет!")
            return

        await message.answer("📋 Задачи на сегодня:")
        for task in tasks:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done_{task.id}")
            ]])
            time_str = f" [{task.due_time.strftime('%H:%M')}]" if task.due_time else ""
            await message.answer(f"🔸 {task.title}{time_str}", reply_markup=kb)


@router.message(Command("backlog"))
async def cmd_backlog(message: Message):
    """Бэклог — задачи без даты"""
    async with async_session() as db:
        tasks = await fetch_backlog_tasks(db)

    if not tasks:
        await message.answer("📥 Бэклог пуст. Напишите «бэклог: идея» или список строк.")
        return

    await message.answer(f"📥 Бэклог ({len(tasks)}):")
    for task in tasks[:15]:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⚡ Сегодня", callback_data=f"plan_today_{task.id}"),
            InlineKeyboardButton(text="✅ Готово", callback_data=f"done_{task.id}"),
        ]])
        await message.answer(f"🔸 {task.title}", reply_markup=kb)

    if len(tasks) > 15:
        await message.answer(f"… и ещё {len(tasks) - 15}. Полный список — в вебе /backlog")


@router.callback_query(F.data.startswith("done_"))
async def process_task_done(callback: CallbackQuery):
    """Обработка кнопки выполнения задачи"""
    task_id = int(callback.data.split("_")[1])
    async with async_session() as db:
        task_res = await db.execute(select(Task).where(Task.id == task_id))
        task = task_res.scalar_one_or_none()
        if task and task.status != "выполнена":
            await mark_task_complete(db, task)
            await callback.message.edit_text(f"✅ ~~{task.title}~~ (выполнено)")
        else:
            await callback.message.answer("Задача уже выполнена или не найдена.")
    await callback.answer("Отмечено!")


@router.callback_query(F.data.startswith("plan_today_"))
async def process_plan_today(callback: CallbackQuery):
    """Перенести из бэклога на сегодня"""
    task_id = int(callback.data.split("_")[2])
    async with async_session() as db:
        task = await plan_task_for_today(db, task_id)
        if task:
            await callback.message.edit_text(
                f"⚡ «{task.title}» → сегодня ({date.today().strftime('%d.%m')})"
            )
        else:
            await callback.message.edit_text("❌ Задача не найдена.")
    await callback.answer()


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Прогресс дня — как на дашборде (задачи + подзадачи + регулярные)"""
    async with async_session() as db:
        text = await format_today_stats(db)
    await message.answer(text)


async def _complete_task_by_name(task_name: str, message: Message) -> None:
    """Найти задачу (сегодня или бэклог) и закрыть."""
    async with async_session() as db:
        matches = await find_tasks_to_complete(db, task_name)

    if not matches:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="➕ Добавить как новую", callback_data=f"add_new:{task_name[:60]}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"),
        ]])
        await message.answer(
            f"🔍 «{task_name}» не найдена (сегодня / бэклог).\nДобавить как новую?",
            reply_markup=kb,
        )
        return

    if len(matches) == 1:
        task = matches[0]
        async with async_session() as db:
            res = await db.execute(select(Task).where(Task.id == task.id))
            t = res.scalar_one()
            await mark_task_complete(db, t)
        where = "бэклог" if task.due_date is None else "сегодня"
        await message.answer(f"✅ Выполнено ({where}): {task.title}")
        return

    buttons = [
        [InlineKeyboardButton(
            text=f"✅ {t.title[:36]}{'…' if len(t.title) > 36 else ''}",
            callback_data=f"complete_task:{t.id}",
        )]
        for t in matches
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"🔍 Нашла {len(matches)} похожих. Какую отметить?",
        reply_markup=kb,
    )


async def _process_bulk_tasks(
    tasks: list[str],
    message: Message,
    source: str = "telegram",
    target: str = "backlog",
) -> None:
    """Создать несколько задач (по умолчанию — в бэклог)."""
    created = []
    skipped = []

    for raw in tasks:
        raw = raw.strip()
        if not raw:
            continue
        try:
            async with async_session() as db:
                task, _ = await create_task_from_text(
                    db, raw, source=source, target=target
                )
                created.append(task.title)
        except ValueError as e:
            skipped.append(str(e).strip("«»"))
        except Exception as e:
            app_logger.error(f"❌ Bulk task error ({raw}): {e}", exc_info=True)
            skipped.append(raw)

    label = "в бэклог" if target == "backlog" else "на сегодня"
    lines = [f"📥 Добавлено {len(created)} {label}:"]
    for title in created:
        lines.append(f"  🔸 {title}")
    if skipped:
        lines.append(f"\nℹ️ Пропущено: {', '.join(skipped[:5])}")
        if len(skipped) > 5:
            lines.append(f"   …ещё {len(skipped) - 5}")

    await message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("complete_task:"))
async def cb_complete_task(callback: CallbackQuery):
    """Отметить конкретную задачу выполненной (из fuzzy-поиска)"""
    task_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        task = res.scalar_one_or_none()
        if task:
            await mark_task_complete(db, task)
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


# ─── Ревью категорий (крон-категоризатор B3) ─────────────────────────────────
# Крон 2x/день проставляет категории задачам без категории и шлёт по сообщению
# на задачу с кнопками [✓ ок][✏️ изменить]. Хэндлеры ниже ловят нажатия.

def _cat_review_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Кнопки подтверждения категории под сообщением-ревью."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✓ ок", callback_data=f"catok:{task_id}"),
        InlineKeyboardButton(text="✏️ изменить", callback_data=f"catchg:{task_id}"),
    ]])


async def _category_name(db, cat_id) -> str:
    if not cat_id:
        return "Без категории"
    res = await db.execute(select(Category).where(Category.id == cat_id))
    c = res.scalar_one_or_none()
    return c.name if c else "Без категории"


async def _build_group_picker(db, task_id: int) -> InlineKeyboardMarkup:
    """Уровень 1: глобальные категории задач (Работа / Личное / Бренд-учёба)."""
    res = await db.execute(
        select(Category)
        .where(Category.type == "task", Category.is_global == True)
        .order_by(Category.name)
    )
    groups = res.scalars().all()
    rows = [[InlineKeyboardButton(text=g.name, callback_data=f"catgrp:{task_id}:{g.id}")] for g in groups]
    rows.append([InlineKeyboardButton(text="✖️ отмена", callback_data=f"catcancel:{task_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_leaf_picker(db, task_id: int, group_id: int) -> InlineKeyboardMarkup:
    """Уровень 2: подкатегории выбранной глобальной категории, по 2 в ряд."""
    res = await db.execute(
        select(Category)
        .where(
            Category.type == "task",
            Category.is_global == False,
            Category.parent_id == group_id,
        )
        .order_by(Category.name)
    )
    leaves = res.scalars().all()
    rows, row = [], []
    for c in leaves:
        row.append(InlineKeyboardButton(text=c.name, callback_data=f"catset:{task_id}:{c.id}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="← назад", callback_data=f"catchg:{task_id}"),
        InlineKeyboardButton(text="✖️ отмена", callback_data=f"catcancel:{task_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("catok:"))
async def cb_cat_ok(callback: CallbackQuery):
    """Подтвердить предложенную кроном категорию (она уже проставлена)."""
    task_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        t = res.scalar_one_or_none()
        if not t:
            await callback.message.edit_text("❌ Задача не найдена.")
            await callback.answer()
            return
        cat_name = await _category_name(db, t.category_id)
    await callback.message.edit_text(f"✅ {t.title}\n📂 {cat_name}")
    await callback.answer("Подтверждено")


@router.callback_query(F.data.startswith("catchg:"))
async def cb_cat_change(callback: CallbackQuery):
    """Уровень 1: показать глобальные категории для смены."""
    task_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        t = res.scalar_one_or_none()
        if not t:
            await callback.message.edit_text("❌ Задача не найдена.")
            await callback.answer()
            return
        kb = await _build_group_picker(db, task_id)
    await callback.message.edit_text(f"✏️ Куда отнести:\n«{t.title}»", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("catgrp:"))
async def cb_cat_group(callback: CallbackQuery):
    """Уровень 2: выбрана глобальная категория — показать её подкатегории."""
    parts = callback.data.split(":")
    task_id, group_id = int(parts[1]), int(parts[2])
    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        t = res.scalar_one_or_none()
        if not t:
            await callback.message.edit_text("❌ Задача не найдена.")
            await callback.answer()
            return
        gres = await db.execute(select(Category).where(Category.id == group_id))
        g = gres.scalar_one_or_none()
        kb = await _build_leaf_picker(db, task_id, group_id)
    gname = g.name if g else ""
    await callback.message.edit_text(f"✏️ {gname} →\n«{t.title}»", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("catset:"))
async def cb_cat_set(callback: CallbackQuery):
    """Применить выбранную категорию + залогировать правку (обучение)."""
    parts = callback.data.split(":")
    task_id, cat_id = int(parts[1]), int(parts[2])
    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        t = res.scalar_one_or_none()
        if not t:
            await callback.message.edit_text("❌ Задача не найдена.")
            await callback.answer()
            return
        old_cat = t.category_id
        t.category_id = cat_id
        t.suggested_title = None
        t.suggested_due_date = None
        await db.commit()
        cat_name = await _category_name(db, cat_id)
    # Сигнал обучения: правка попадает в историю задач; крон-категоризатор
    # сверяется с ней и ведёт правила в gbrain (planner/task-category-rules).
    app_logger.info(
        f"CAT_CORRECTION task={task_id} title={t.title!r} {old_cat}->{cat_id} ({cat_name})"
    )
    await callback.message.edit_text(f"✅ {t.title}\n📂 {cat_name}")
    await callback.answer("Категория обновлена")


@router.callback_query(F.data.startswith("catcancel:"))
async def cb_cat_cancel(callback: CallbackQuery):
    """Вернуть сообщение-ревью к виду [✓ ок][✏️ изменить]."""
    task_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        t = res.scalar_one_or_none()
        if not t:
            await callback.message.edit_text("❌ Задача не найдена.")
            await callback.answer()
            return
        cat_name = await _category_name(db, t.category_id)
    await callback.message.edit_text(
        f"🗂 {t.title}\n📂 {cat_name}", reply_markup=_cat_review_keyboard(task_id)
    )
    await callback.answer()


# ─── Применение нормализации (чистый текст + вынесенная дата) ────────────────
# Крон кладёт предложение в suggested_title / suggested_due_date и шлёт ревью
# с кнопками [✓ применить][✏️ категория][✖️ как есть]. Применяем только по кнопке.

@router.callback_query(F.data.startswith("txtok:"))
async def cb_txt_ok(callback: CallbackQuery):
    """Применить предложенные кроном чистый текст и/или вынесенную дату."""
    task_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        t = res.scalar_one_or_none()
        if not t:
            await callback.message.edit_text("❌ Задача не найдена.")
            await callback.answer()
            return
        old_title = t.title
        old_due = t.due_date
        applied = []
        if t.suggested_title:
            t.title = t.suggested_title
            applied.append("текст")
        if t.suggested_due_date:
            t.due_date = t.suggested_due_date
            applied.append("дата")
        t.suggested_title = None
        t.suggested_due_date = None
        await db.commit()
        cat_name = await _category_name(db, t.category_id)
        new_title = t.title
        new_due = t.due_date
    app_logger.info(
        f"TXT_APPLY task={task_id} title={old_title!r}->{new_title!r} due={old_due}->{new_due}"
    )
    msg = f"✅ {new_title}\n📂 {cat_name}"
    if new_due:
        msg += f"\n📅 {new_due.strftime('%d.%m.%Y')}"
    await callback.message.edit_text(msg)
    await callback.answer(("Применено: " + ", ".join(applied)) if applied else "Готово")


@router.callback_query(F.data.startswith("txtskip:"))
async def cb_txt_skip(callback: CallbackQuery):
    """Оставить текст/дату как есть — только сбросить предложение крона."""
    task_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        t = res.scalar_one_or_none()
        if not t:
            await callback.message.edit_text("❌ Задача не найдена.")
            await callback.answer()
            return
        t.suggested_title = None
        t.suggested_due_date = None
        await db.commit()
        cat_name = await _category_name(db, t.category_id)
        title = t.title
    await callback.message.edit_text(f"✅ {title}\n📂 {cat_name}")
    await callback.answer("Оставлено как есть")


async def _process_and_create_task(
    text: str,
    message: Message,
    source: str = "telegram",
    target: str = "today",
):
    """Создание одной задачи из текста."""
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

            _, summary = await create_task_from_text(db, text, source=source, target=target)
            await message.answer(summary)

    except ValueError as e:
        await message.answer(f"ℹ️ {e}")
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
        await _process_bulk_tasks(
            intent["tasks"],
            message,
            source="telegram",
            target=intent.get("target", "backlog"),
        )
    elif intent["intent"] == "backlog_add":
        await _process_and_create_task(
            intent["task_name"], message, source="telegram", target="backlog"
        )
    else:
        await _process_and_create_task(
            text, message, source="telegram", target=intent.get("target", "today")
        )


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
                await _process_bulk_tasks(
                    intent["tasks"], message, source="voice", target="backlog"
                )
            elif intent["intent"] == "backlog_add":
                await _process_and_create_task(
                    intent["task_name"], message, source="voice", target="backlog"
                )
            else:
                await _process_and_create_task(text, message, source="voice")

        if file_path.exists():
            os.remove(file_path)
    except Exception as e:
        app_logger.error(f"❌ Ошибка обработки голосового: {e}", exc_info=True)
        await msg.edit_text("❌ Ошибка при транскрибации.")


@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot):
    """Обработка фото через Vision-модель"""
    msg = await message.answer("👁 Бот внимательно смотрит на картинку...")
    file_path = None
    try:
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        uploads_dir = settings.uploads_dir / "screenshots"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        file_ext = file.file_path.split(".")[-1] if "." in file.file_path else "jpg"
        filename = f"{file_id}.{file_ext}"
        file_path = uploads_dir / filename
        await bot.download_file(file.file_path, destination=file_path)

        vision_result = await ai_service.vision_analyze_screenshot(str(file_path))
        verdict = vision_result.get("type", "other")
        data = vision_result.get("data", {})

        app_logger.info(f"👁 Vision Verdict: {verdict}")

        if verdict == "finance":
            items = data.get("items", [])
            if not items:
                await msg.edit_text("💰 Похоже на финансы, но я не смог разобрать детали. Сохранил для ручного ввода.")
                await _save_screenshot_to_db(file_path, "vision_finance_empty", message)
                return

            async with async_session() as db:
                count = 0
                for item in items:
                    amount = float(item.get("amount", 0))
                    if amount == 0:
                        continue

                    tx_date_str = item.get("date")
                    try:
                        tx_date = date.fromisoformat(tx_date_str) if tx_date_str else date.today()
                    except Exception:
                        tx_date = date.today()

                    desc = item.get("desc", "Операция из Vision")
                    cat_id = await _get_merchant_category(desc, db)

                    if cat_id is None:
                        cat_hint = item.get("category_hint")
                        if cat_hint:
                            cat_res = await db.execute(
                                select(Category).where(
                                    Category.name.ilike(f"%{cat_hint}%"),
                                    Category.type == "finance",
                                )
                            )
                            cat_obj = cat_res.scalar_one_or_none()
                            if cat_obj:
                                cat_id = cat_obj.id

                    # --- Фильтрация и коррекция знака ---
                    INCOME_CATEGORY_IDS = {65, 85, 66, 86, 94, 129}

                    # Пропускаем строки кэшбэка
                    if "кэшбэк" in desc.lower() or "cashback" in desc.lower():
                        app_logger.info(f"⏭ Пропущен кэшбэк: {desc}")
                        continue

                    # Пропускаем микрокэшбэк: маленькие положительные суммы,
                    # которые не являются доходом
                    if amount > 0 and amount < 50 and cat_id not in INCOME_CATEGORY_IDS:
                        app_logger.info(f"⏭ Пропущен кэшбэк: {amount}₽ — {desc}")
                        continue

                    # Расходы должны быть отрицательными.
                    # Если категория известна и это НЕ доход → инвертируем знак.
                    # Если категория неизвестна — предполагаем расход (тоже инвертируем).
                    is_income = cat_id in INCOME_CATEGORY_IDS if cat_id else False
                    if amount > 0 and not is_income:
                        amount = -amount

                    # Проверка на дубликат: НЕ вставляем, если такая транзакция уже есть
                    dup_check = await db.execute(
                        select(Transaction).where(
                            Transaction.date == tx_date,
                            Transaction.description == desc,
                            Transaction.amount == amount,
                            Transaction.source == "vision_screenshot",
                        ).limit(1)
                    )
                    if dup_check.scalar_one_or_none() is not None:
                        app_logger.info(f"⏭ Дубликат пропущен: {tx_date} {desc} {amount}₽")
                        continue

                    db.add(Transaction(
                        date=tx_date,
                        amount=amount,
                        description=desc,
                        category_id=cat_id,
                        source="vision_screenshot",
                    ))
                    count += 1
                await db.commit()

            await msg.edit_text(f"✅ Успешно «увидел» операций: {count}\n💰 Все данные внесены в Финансы. Скриншот удален.")
            if file_path and file_path.exists():
                os.remove(file_path)

        elif verdict == "calendar":
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
            if file_path and file_path.exists():
                os.remove(file_path)
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
    """Merchant memory + keyword rules: категория по описанию транзакции."""
    if not description:
        return None
    
    # Keyword rules — точные совпадения по ключевым словам
    desc_lower = description.lower()
    
    KEYWORD_RULES = [
        # Транспорт
        (["самокат", "whoosh", "вуш", "lime", "bolt", "yandex go самокат"], 138),  # Самокаты (перед Такси!)
        (["такси", "яндекс go", "yandex go", "uber", "ситимобил"], 48),  # Такси
        (["парковк", "parking"], 56),           # Парковка
        (["азс", "газпромнефть", "татнефть", "лукойл", "рнефть", "shell", "bp "], 52),  # Бензин
        (["метро", "метрополитен"], 71),         # Метро (транспорт)
        (["мотоцикл", "мотавр", "motavr"], 136),  # Мотоцикл
        
        # Еда
        (["магнит", "лента", "вкусвилл", "пятёрочк", "дикси", "перекрёст", "продукт", "супермаркет"], 39),  # Продукты
        (["ресторан", "кафе", "кофейня", "бургер", "суши", "пицц", "вкусно", "kfc", "mcdonald", "тандер", "копчён", "копчен", "кост", "норман", "пив", "бар ", "паб"], 42),  # Фастфуд/Рестораны
        
        # Развлечения (Даня)
        (["зоопарк", "кино", "театр", "sandylan", "yamaguchi", "evo", "экстрим", "батут", "квест", "детск"], 149),  # Развлечения
        
        # Дом
        (["ипотека", "ipoteka"], 36),
        (["коммуналк", "жкх", "тсж", "ук ", "энергосбыт", "водоканал"], 62),  # Коммуналка
        (["ремонт", "хозтовар", "строй", "obi", "леруа", "петрович", "домов"], 91),  # Хозтовары
        
        # Здоровье
        (["аптек", "aptek", "ригла"], 96),  # Аптеки
        (["клиник", "стоматолог", "врач", "медицин"], 128),  # Клиники
        
        # Связь
        (["теле2", "мтс", "билайн", "мегафон", "yota", "связь", "интернет", "подписк"], 55),  # Подписки
        
        # Красота
        (["салон", "парикмахер", "barber", "ногт", "бров", "косметолог", "маникюр"], 97),  # Салоны
        (["косметолог"], 101),  # Косметолог
        
        # Прочее
        (["ozon", "wildberries", "вб ", "wb ", "маркетплейс", "алиэкспресс", "aliexpress"], 131),  # Озон/ВБ
        (["благотворитель", "помощь рядом"], 40),  # Благотворительность
        (["табак", "сигарет", "кальян"], 34),  # Табак
    ]
    
    for keywords, cat_id in KEYWORD_RULES:
        for kw in keywords:
            if kw in desc_lower:
                return cat_id
    
    # Fallback: merchant memory (exact match)
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
                created_at=datetime.now(),
            )
            db.add(screenshot)
            await db.commit()
    except Exception as e:
        app_logger.error(f"❌ Не удалось сохранить скриншот в БД: {e}")


@router.message(F.document)
async def handle_document(message: Message, bot: Bot):
    """Обработка документов — скачивание и сохранение в uploads."""
    doc = message.document
    file_id = doc.file_id
    file_name = doc.file_name or f"{file_id}.bin"
    file_size_kb = (doc.file_size or 0) / 1024

    msg = await message.answer(f"📄 Получаю файл «{file_name}» ({file_size_kb:.0f} КБ)...")

    try:
        file = await bot.get_file(file_id)
        uploads_dir = settings.uploads_dir / "documents"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        dest_path = uploads_dir / file_name
        await bot.download_file(file.file_path, destination=dest_path)

        await msg.edit_text(
            f"📄 Файл «{file_name}» ({file_size_kb:.0f} КБ) сохранён в uploads/documents/"
        )
        app_logger.info(f"📄 Документ сохранён: {dest_path}")

    except Exception as e:
        app_logger.error(f"❌ Ошибка при скачивании документа: {e}", exc_info=True)
        await msg.edit_text(f"❌ Не удалось сохранить файл: {e}")
