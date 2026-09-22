from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base
from app.models.tag import shopping_item_tags


class ShoppingItem(Base):
    __tablename__ = "shopping_items"

    # Ключ импорта архивов уникален: одна пара (откуда, номер сообщения) —
    # одна запись. Тот же индекс создаёт миграция 017 на боевой базе.
    __table_args__ = (
        Index(
            "uq_shopping_items_import_key",
            "imported_from",
            "external_id",
            unique=True,
            sqlite_where=text("imported_from IS NOT NULL AND external_id IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    quantity = Column(String(100), default="")  # количество (например, "2 шт", "500 г")
    is_purchased = Column(Boolean, default=False, index=True)
    is_archived = Column(Boolean, default=False, index=True)
    item_kind = Column(String(20), default="purchase", nullable=False)  # purchase / reading
    content = Column(Text, nullable=True)  # заметки, полный текст поста
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    purchased_at = Column(DateTime(timezone=True), nullable=True)

    # --- Reading tracker fields ---
    reading_status = Column(String(20), default="want_to_read", nullable=False)
    # want_to_read / reading / done
    pages_total = Column(Integer, nullable=True)
    pages_read = Column(Integer, default=0)

    # --- Таксономия чтения (см. reading_service) ---------------------------
    # Категория берётся из общей таблицы categories с type='reading':
    # отдельная сущность не нужна, механизм категорий в проекте уже есть.
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    # Формат: книга, статья, видео, подкаст, плейлист, разбор PDF, конспект, тест.
    reading_format = Column(String(30), nullable=True, index=True)

    # --- Провенанс импорта архивов ----------------------------------------
    # imported_from: favorites / read / instagram_saved и подобное.
    # Пара (imported_from, external_id) — ключ идемпотентности: повторный
    # прогон импорта не создаёт дублей и любую запись можно отследить
    # до исходного сообщения.
    imported_from = Column(String(30), nullable=True, index=True)
    external_id = Column(String(60), nullable=True, index=True)

    category = relationship("Category", lazy="selectin")
    tags = relationship(
        "Tag",
        secondary=shopping_item_tags,
        lazy="selectin",
        order_by="Tag.name",
    )
