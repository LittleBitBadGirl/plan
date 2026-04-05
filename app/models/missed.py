from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger
from sqlalchemy.sql import func
from app.models.base import Base


class MissedMessage(Base):
    __tablename__ = "missed_messages"

    id = Column(Integer, primary_key=True, index=True)
    telegram_chat_id = Column(BigInteger, nullable=False)
    message_text = Column(String, nullable=True)
    message_type = Column(String(20), nullable=False)  # text/voice/photo
    processed = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    message_hash = Column(String(64), unique=True, nullable=True)
