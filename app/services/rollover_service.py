"""Ночная зачистка дня: незакрытое возвращается в бэклог.

До 07.10.2026 сервис работал наоборот — переносил ВСЕ незакрытые задачи на
сегодняшнюю дату (`due_date = today`). За месяцы в «сегодня» накапливались
десятки задач (в один день 54, из них 28 старше месяца), и утро начиналось
с простыни вместо плана.

Решение Веры: день собирается руками (минимум 5 задач из бэклога), а вечером
всё несделанное уходит обратно в бэклог. Здесь это и происходит: задача теряет
`planned_for`, счётчик переносов растёт — по нему видно, что брали и не сделали.

Имя функции и ключи ответа оставлены прежними: их дёргает APScheduler в main.py
и тесты производительности дашборда.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session
from app.services.day_pool_service import return_unfinished_to_backlog


async def _rollover_impl(db: AsyncSession):
    # Последний шанс запомнить вчерашний день: у его задач planned_for ещё на
    # месте, а return_unfinished_to_backlog ниже его сотрёт — после этого любой
    # прошлый день выглядел бы закрытым, даже если хвост оставался.
    # Сбой памяти о дне не должен ломать саму зачистку дня.
    from datetime import date, timedelta

    from app.services.day_win_service import sync_day_win
    from app.utils.logger import app_logger

    try:
        await sync_day_win(db, date.today() - timedelta(days=1))
    except Exception as exc:  # noqa: BLE001
        # Сессию после сбойного запроса вернуть в рабочее состояние: иначе на
        # этом же коммите упадёт и сама зачистка дня — а она важнее памяти.
        await db.rollback()
        app_logger.warning(f"Day win sync skipped: {exc}")

    return await return_unfinished_to_backlog(db)


async def rollover_overdue_tasks(db: AsyncSession = None):
    """Вернуть незакрытые задачи дня в бэклог (для APScheduler, 00:10)."""
    if db is None:
        async with async_session() as db:
            result = await _rollover_impl(db)
            await db.commit()
            return result
    return await _rollover_impl(db)
