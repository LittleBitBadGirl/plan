from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base

class Habit(Base):
    __tablename__ = "habits"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(String, default="")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    is_archived = Column(Boolean, default=False)
    start_date = Column(Date, nullable=True)
    target_days = Column(Integer, default=30)
    current_cycle = Column(Integer, default=1) # Номер текущего цикла
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Связи
    logs = relationship("HabitLog", back_populates="habit", cascade="all, delete-orphan")
    category = relationship("Category")
