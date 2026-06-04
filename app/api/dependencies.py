from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import extract_token, validate_token
from app.db.database import get_db


def verify_token(request: Request) -> bool:
    """Проверка API-токена (дублирует middleware для явных Depends на роутерах)."""
    validate_token(extract_token(request))
    return True


async def get_db_session(
    session: AsyncSession = Depends(get_db)
) -> AsyncSession:
    """Dependency для получения сессии БД"""
    return session
