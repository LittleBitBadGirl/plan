from sqlalchemy import Column, Integer, Text, String, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base

class CareerImpact(Base):
    """Модель для хранения отфильтрованных и обработанных достижений (Карьерный капитал)"""
    __tablename__ = "career_impacts"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True) # Ссылка на оригинальную задачу (если есть)
    original_title = Column(String(500)) # Как задача называлась изначально
    impact_description = Column(Text, nullable=False) # Переписанный Groq-ом текст в Senior стиле
    category_name = Column(String(100)) # Группа (Работа/Пет-проекты и т.д.)
    period_month = Column(String(7)) # Месяц (например, 2026-05) для удобной группировки
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Связи
    task = relationship("Task")
