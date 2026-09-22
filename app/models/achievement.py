"""Достижения: ручные записи Веры, две полки — рабочее и личное."""
from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.models.base import Base

# Полки раздела. Ключ хранится в базе, подпись показывается в интерфейсе.
SPHERE_LABELS = {"work": "Рабочее", "personal": "Личное"}
SPHERE_KEYS = tuple(SPHERE_LABELS)


class Achievement(Base):
    """Одно достижение, записанное руками.

    Автоматику из задач (Карьерный капитал) не смешиваем: это отдельный,
    осознанно заполняемый список.
    """

    __tablename__ = "achievements"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    # work / personal — раскладывает запись на полку
    sphere = Column(String(20), nullable=False, default="personal", index=True)
    # свободная метка темы: Тело, Менталка, Проекты, Работа...
    tag = Column(String(100), nullable=True)
    happened_on = Column(Date, nullable=True)
    # откуда запись: сама, Полина, ОС от Миши...
    source = Column(String(100), nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
