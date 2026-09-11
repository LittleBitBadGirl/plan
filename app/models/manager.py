from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base


class Manager(Base):
    """Менеджер группы Веры — карточка для сбора обратной связи."""

    __tablename__ = "managers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    projects = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    feedback = relationship(
        "ManagerFeedback",
        back_populates="manager",
        cascade="all, delete-orphan",
    )


class ManagerFeedback(Base):
    """Одна запись обратной связи по менеджеру.

    kind: minus (что не так) / note (наблюдение) / plus (что хорошо)
    period_month: YYYY-MM — по нему записи группируются в попапе.
    links / files: JSON-списки (ссылки на пруфы и пути в /uploads).
    """

    __tablename__ = "manager_feedback"
    __table_args__ = (
        Index("ix_manager_feedback_manager", "manager_id", "period_month"),
    )

    id = Column(Integer, primary_key=True, index=True)
    manager_id = Column(Integer, ForeignKey("managers.id"), nullable=False)
    date = Column(Date, nullable=False)
    period_month = Column(String(7), nullable=False, index=True)
    kind = Column(String(20), nullable=False, default="minus")
    project = Column(String(200), nullable=True)
    text = Column(Text, nullable=False)
    links = Column(Text, nullable=True)
    files = Column(Text, nullable=True)
    source = Column(String(20), nullable=False, default="web")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    manager = relationship("Manager", back_populates="feedback")
