from sqlalchemy import Column, Integer, Text, Date, DateTime
from sqlalchemy.sql import func
from app.models.base import Base


class AIReport(Base):
    __tablename__ = "ai_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(Date, unique=True, index=True) # Дата, за которую сделан анализ
    content = Column(Text, nullable=False) # Текст моего анализа
    created_at = Column(DateTime(timezone=True), server_default=func.now())
