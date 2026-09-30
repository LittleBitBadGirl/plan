"""Мероприятия Веры: конференции, выставки, лекции — со ссылками и картинками.

Отдельная сущность от задач и от встреч (calendar_events): встреча приходит из
календаря и занимает час, мероприятие Вера находит сама в интернете и решает,
идёт она или нет. У мероприятия может быть конкретная дата или период (выставка
идёт неделю), поэтому дата окончания хранится отдельной колонкой.
"""

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    or_,
)
from sqlalchemy.sql import func

from app.models.base import Base

STATUS_NONE = "none"
STATUS_GOING = "going"
STATUS_NOT_GOING = "not_going"

# Решение Веры по мероприятию: иду / не иду. «Без отметки» — когда ещё не решила,
# это состояние по умолчанию и его нельзя терять.
EVENT_STATUSES = (STATUS_NONE, STATUS_GOING, STATUS_NOT_GOING)


def event_is_active():
    """Мероприятие не в архиве — включая строки, где is_archived = NULL.

    Как и у задач: сравнение `is_archived == 0` в SQL отбрасывает NULL, и запись
    молча пропадает из всех выборок.
    """
    return or_(Event.is_archived.is_(None), Event.is_archived == 0)


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    url = Column(String(1000), nullable=True)
    # Картинка либо приходит со страницы (ссылка), либо Вера вставляет свою —
    # тогда файл лежит в uploads/events/, а в колонке относительный путь.
    image_url = Column(String(1000), nullable=True)
    image_file = Column(String(300), nullable=True)
    location = Column(String(500), nullable=True)
    start_date = Column(Date, nullable=False, index=True)
    end_date = Column(Date, nullable=True)  # NULL = одна дата, не период
    start_time = Column(String(5), nullable=True)  # «19:00», необязательно
    status = Column(String(20), nullable=False, server_default=STATUS_NONE)
    source = Column(String(20), nullable=False, server_default="web")
    is_archived = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Выборки всегда «пересекается ли период с окном недели/месяца».
        Index("ix_events_range", "start_date", "end_date"),
        Index("ix_events_status", "status"),
    )

    @property
    def last_day(self):
        """Последний день мероприятия: для периода — дата окончания."""
        return self.end_date or self.start_date

    @property
    def is_range(self) -> bool:
        """Идёт несколько дней (выставка, фестиваль)."""
        return bool(self.end_date and self.end_date != self.start_date)
