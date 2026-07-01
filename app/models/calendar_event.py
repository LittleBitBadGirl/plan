from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, Index
from sqlalchemy.sql import func

from app.models.base import Base


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, index=True)
    external_uid = Column(String(255), nullable=False, unique=True, index=True)
    recurrence_id = Column(String(255), nullable=True, index=True)
    calendar_name = Column(String(200), nullable=False)
    calendar_url = Column(String(500), nullable=False)
    title = Column(String(500), nullable=False)
    start_at = Column(DateTime, nullable=False, index=True)
    end_at = Column(DateTime, nullable=True)
    location = Column(String(500), nullable=True)
    is_recurring = Column(Boolean, default=False)
    is_all_day = Column(Boolean, default=False)
    calendar_source = Column(String(20), default="yandex", nullable=False, index=True)
    calendar_kind = Column(String(20), default="work", nullable=False, index=True)
    planner_visible = Column(Boolean, default=True, index=True)
    filter_reason = Column(String(100), nullable=True)
    ignored_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_calendar_events_day_visible", "start_at", "planner_visible"),
    )
