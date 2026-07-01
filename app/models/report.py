from sqlalchemy import Column, Integer, Text, Date, DateTime, String
from sqlalchemy.sql import func
from app.models.base import Base


class AIReport(Base):
    __tablename__ = "ai_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(Date, unique=True, index=True) # Дата, за которую сделан анализ
    content = Column(Text, nullable=False) # Текст анализа
    source = Column(String(20), default="deepseek")  # deepseek / hermes
    status = Column(String(20), default="done")  # pending / done
    created_at = Column(DateTime(timezone=True), server_default=func.now())
