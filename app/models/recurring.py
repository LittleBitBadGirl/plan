from sqlalchemy import Column, Integer, String, Boolean, Date, Time, DateTime, JSON
from sqlalchemy.sql import func
from app.models.base import Base


class RecurringTask(Base):
    __tablename__ = "recurring_tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(String, default="")
    category_id = Column(Integer, nullable=True)
    priority = Column(String(20), default="средний")
    recurrence_type = Column(String(20), nullable=False)  # daily/weekly/monthly/custom
    recurrence_days = Column(JSON, nullable=True)  # ["mon", "wed", "fri"]
    recurrence_interval = Column(Integer, default=1)  # для custom
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    time_of_day = Column(Time, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    completed_count = Column(Integer, default=0)  # сколько раз выполнена
    created_at = Column(DateTime(timezone=True), server_default=func.now())
