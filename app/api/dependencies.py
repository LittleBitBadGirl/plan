from fastapi import Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db


def verify_token(x_api_token: str = Header(...)):
    """Проверка API токена"""
    from app.config import settings
    if x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid API token")
    return True


async def get_db_session(
    session: AsyncSession = Depends(get_db)
) -> AsyncSession:
    """Dependency для получения сессии БД"""
    return session
