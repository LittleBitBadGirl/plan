import asyncio
import logging
from aiogram import Bot, Dispatcher
from app.config import settings
from app.bot.handlers import router, send_daily_plan
from app.db.database import init_db
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # Инициализация базы данных, если нужно
    await init_db()
    
    # Инициализация бота
    if not settings.telegram_bot_token:
        logging.error("Токен бота не найден в настройках!")
        return
        
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    
    # Подключение роутера с обработчиками
    dp.include_router(router)
    
    # Настройка планировщика для утреннего пуша
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_daily_plan,
        CronTrigger(hour=9, minute=0),
        args=[bot],
        id="daily_plan_push",
        name="Утренний пуш плана на день"
    )
    scheduler.start()
    logging.info("⏰ Планировщик утренних пушей запущен (09:00)")
    
    logging.info("Бот запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
