from sqlalchemy import Column, Integer, Date, Boolean, DateTime
from sqlalchemy.sql import func
from app.models.base import Base


class PeriodEntry(Base):
    __tablename__ = "period_entries"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, unique=True, index=True)
    has_pain = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
