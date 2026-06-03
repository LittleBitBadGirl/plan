from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.models.base import Base


class CalendarIgnoreRule(Base):
    __tablename__ = "calendar_ignore_rules"

    id = Column(Integer, primary_key=True, index=True)
    rule_type = Column(String(30), nullable=False)  # external_uid | recurrence_id | series_title
    value = Column(String(500), nullable=False)
    created_from_event_uid = Column(String(255), nullable=True)
    note = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("rule_type", "value", name="uq_calendar_ignore_rule"),
    )
