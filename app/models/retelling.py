from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from app.models.base import Base


class Retelling(Base):
    """Пересказ «5 мыслей на диктофон» — привычка 11.

    Таблицу 06.10.2026 завёл серверный Hermes: Вера наговаривает голосовое с
    мыслями по прочитанному, он расшифровывает и складывает запись в planner.db
    (не в отдельный файл — чтобы пересказы попадали в штатные бэкапы планера).

    Здесь таблица описана моделью, потому что теперь её читает дашборд: у книги,
    которую Вера читает, должны быть видны её пересказы. Связей две:
    * ``habit_log_id`` — отметка привычки (её проставляют триггеры, что уже есть
      в базе);
    * ``book_item_id`` — книга в списке «Читать» (``shopping_items``). Книга
      находится по названию, а не записывается руками: ``source_title`` —
      свободный текст, в нём может быть и глава, и автор.

    Пересказ может остаться без книги (запись про подкаст, фильм или про книгу,
    которой ещё нет в «Читать») — это честное состояние, а не ошибка.
    """

    __tablename__ = "retellings"

    # Имена индексов совпадают с теми, что уже стоят на боевой базе: иначе
    # create_all и миграция дадут разные имена и схемы разойдутся.
    __table_args__ = (
        Index("idx_retellings_recorded", "recorded_at"),
        Index("idx_retellings_source", "source_title"),
    )

    id = Column(Integer, primary_key=True)
    # МСК, 'YYYY-MM-DD HH:MM' — так пишет серверный Hermes, формат сохраняем.
    recorded_at = Column(String, nullable=False)
    habit_id = Column(Integer, server_default="11")
    source_type = Column(String, server_default="book")  # book | podcast | talk | film
    source_title = Column(String)
    source_author = Column(String)
    chapter = Column(String)
    duration_sec = Column(Integer)
    audio_path = Column(String)
    transcript = Column(Text)
    summary = Column(Text)
    notes = Column(Text)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))
    habit_log_id = Column(Integer, ForeignKey("habit_logs.id"))
    book_item_id = Column(Integer, ForeignKey("shopping_items.id"))

    thoughts = relationship(
        "RetellingThought",
        lazy="selectin",
        order_by="RetellingThought.position",
        cascade="all, delete-orphan",
    )


class RetellingThought(Base):
    """Одна мысль пересказа: до пяти строк, каждая отдельно.

    Отдельными строками, а не текстом через перевод строки — чтобы в аналитике
    можно было считать мысли, а не только пересказы.
    """

    __tablename__ = "retelling_thoughts"
    __table_args__ = (
        UniqueConstraint("retelling_id", "position", name="uq_retelling_thought_position"),
        Index("idx_thoughts_retelling", "retelling_id"),
    )

    id = Column(Integer, primary_key=True)
    retelling_id = Column(
        Integer, ForeignKey("retellings.id", ondelete="CASCADE"), nullable=False
    )
    position = Column(Integer, nullable=False)  # 1..5
    thought = Column(Text, nullable=False)
    quote = Column(Text)
    page = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))
