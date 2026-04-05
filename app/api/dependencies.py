from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db


def verify_token():
    """Заглушка — аутентификация отключена для локального использования"""
    return True


async def get_db_session(
    session: AsyncSession = Depends(get_db)
) -> AsyncSession:
    """Dependency для получения сессии БД"""
    return session
