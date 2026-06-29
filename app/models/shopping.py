from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func
from app.models.base import Base


class ShoppingItem(Base):
    __tablename__ = "shopping_items"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    quantity = Column(String(100), default="")  # количество (например, "2 шт", "500 г")
    is_purchased = Column(Boolean, default=False, index=True)
    is_archived = Column(Boolean, default=False, index=True)
    item_kind = Column(String(20), default="purchase", nullable=False)  # purchase / reading
    content = Column(Text, nullable=True)  # заметки, полный текст поста
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    purchased_at = Column(DateTime(timezone=True), nullable=True)
