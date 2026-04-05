from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import async_session
from app.models.missed import MissedMessage


async def sync_missed_messages(bot: Bot):
    """Обработать пропущенные сообщения при запуске"""
    async with async_session() as db:
        result = await db.execute(
            select(MissedMessage).where(MissedMessage.processed == False)
        )
        missed = result.scalars().all()

        if not missed:
            return

        for msg in missed:
            # Отправить обработку (текст → задача)
            chat_id = msg.telegram_chat_id
            await bot.send_message(
                chat_id,
                f"🔄 Обработано: {msg.message_text[:50]}..."
            )
            msg.processed = True
            await db.flush()

        # Сводка
        chat_ids = set(m.telegram_chat_id for m in missed)
        for chat_id in chat_ids:
            await bot.send_message(
                chat_id,
                f"✅ Обработано {len(missed)} пропущенных сообщений"
            )
