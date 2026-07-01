from fastapi import APIRouter
from sqlalchemy import select
from app.db.database import async_session
from app.models.period_entry import PeriodEntry
from pydantic import BaseModel
from datetime import date

router = APIRouter(prefix="/api/period", tags=["period"])


class PeriodToggleRequest(BaseModel):
    date: date


@router.post("/toggle")
async def toggle_period_day(data: PeriodToggleRequest):
    """
    4-state cycle per day:
      absent    → spotting (no pain)
      spotting  → period (no pain)
      period    → period + pain
      pain      → absent (delete)
    """
    async with async_session() as db:
        result = await db.execute(
            select(PeriodEntry).where(PeriodEntry.date == data.date)
        )
        entry = result.scalar_one_or_none()

        if entry is None:
            # absent → spotting
            db.add(PeriodEntry(date=data.date, has_pain=False, is_spotting=True))
            await db.commit()
            return {"state": "spotting", "date": str(data.date)}

        if entry.is_spotting and not entry.has_pain:
            # spotting → period
            entry.is_spotting = False
            await db.commit()
            return {"state": "period", "date": str(data.date)}

        if not entry.is_spotting and not entry.has_pain:
            # period → pain
            entry.has_pain = True
            await db.commit()
            return {"state": "pain", "date": str(data.date)}

        # pain → delete
        await db.delete(entry)
        await db.commit()
        return {"state": "none", "date": str(data.date)}
