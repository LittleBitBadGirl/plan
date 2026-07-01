import os
import sqlite3
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.migrate import run_migrations
from app.models import calendar_event as _ce  # noqa: F401
from app.models import calendar_ignore_rule as _cir  # noqa: F401
from app.models import period_entry as _pe  # noqa: F401
from app.models import recurring_completion as _rc  # noqa: F401
from app.models.base import Base


@pytest.mark.asyncio
async def test_run_migrations_on_fresh_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "migrate_test.db")
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url, connect_args={"check_same_thread": False})
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from app.config import settings

        prev_url = settings.database_url
        settings.database_url = url
        try:
            run_migrations()
        finally:
            settings.database_url = prev_url

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = result.scalar_one()
        assert version == "002_legacy_schema"

        sync = sqlite3.connect(db_path)
        cols = {row[1] for row in sync.execute("PRAGMA table_info(tasks)")}
        sync.close()
        assert "estimated_minutes" in cols

        await engine.dispose()
