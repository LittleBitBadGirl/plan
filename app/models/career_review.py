from sqlalchemy import Column, Integer, Date, DateTime, String, Text
from sqlalchemy.sql import func

from app.models.base import Base


class CareerReview(Base):
    """Разбор периода по карьерным активам (Карьерный капитал).

    Страница рисуется из снимка: `payload` это JSON со всеми активами, пунктами
    и списками задач, поэтому на рендере ничего не считается и числа на экране
    не могут разойтись с тем, что посчитал генератор.
    """

    __tablename__ = "career_reviews"

    id = Column(Integer, primary_key=True, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    period_month = Column(String(7), nullable=False, index=True, unique=True)
    total_tasks = Column(Integer, nullable=False, default=0)
    payload = Column(Text, nullable=False)
    generator = Column(String(120))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
