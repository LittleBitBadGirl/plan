from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from app.models.base import Base

class FinancialGoal(Base):
    __tablename__ = "financial_goals"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False) # Инвестиции (ИИС), Автомобиль, Подушка
    target_amount = Column(Float, nullable=False)
    current_amount = Column(Float, default=0.0)
    category_id = Column(Integer, nullable=True) # Привязка к категории для авто-обновления (напр. 'Подушка')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
