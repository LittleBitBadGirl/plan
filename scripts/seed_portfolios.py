"""Seed portfolio accounts. Run: python scripts/seed_portfolios.py"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import async_session, init_db
from app.db.seed_portfolios import seed_portfolios


async def main() -> None:
    await init_db()
    async with async_session() as db:
        await seed_portfolios(db)
        await db.commit()
    print("portfolios seeded")


if __name__ == "__main__":
    asyncio.run(main())
