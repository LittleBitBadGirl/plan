from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, UniqueConstraint
from sqlalchemy.sql import func

from app.models.base import Base


class Tag(Base):
    """Свободный тег. Один на все сущности: сейчас используется в чтении.

    Список тегов растёт сам: при вводе подсказываются уже использованные
    (см. reading_service). Отдельная таблица нужна, чтобы фильтр по тегам
    был индексным поиском, а не LIKE по строке.
    """

    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(60), nullable=False, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# Связь «запись чтения — тег». Составной первичный ключ не даёт поставить
# один и тот же тег дважды; ondelete CASCADE чистит связи при удалении записи.
shopping_item_tags = Table(
    "shopping_item_tags",
    Base.metadata,
    Column("item_id", Integer, ForeignKey("shopping_items.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    UniqueConstraint("item_id", "tag_id", name="uq_shopping_item_tag"),
)
