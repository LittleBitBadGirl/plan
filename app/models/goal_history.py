"""История изменений баланса финансовых целей."""
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.models.base import Base

class GoalHistory(Base):
    __tablename__ = "goal_history"

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, ForeignKey("financial_goals.id"), nullable=False, index=True)
    new_amount = Column(Float, nullable=False)      # новый баланс после изменения
    delta = Column(Float, nullable=False)            # изменение (может быть отрицательным)
    note = Column(String(200), nullable=True)        # пополнение / проценты / корректировка
    created_at = Column(DateTime(timezone=True), server_default=func.now())
