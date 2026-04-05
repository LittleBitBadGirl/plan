from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(String, default="")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    status = Column(String(20), default="новая", index=True)  # новая/в_работе/выполнена/отложена
    priority = Column(String(20), default="средний")  # низкий/средний/высокий
    due_date = Column(Date, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String(20), default="web")  # telegram/web/screenshot
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)
    is_archived = Column(Boolean, default=False, index=True)
    sort_order = Column(Integer, default=0)
    needs_review = Column(Boolean, default=False)
    message_hash = Column(String(64), nullable=True)
    postpones = Column(Integer, default=0)
    chronic_task = Column(Boolean, default=False, index=True)
    chronic_reviewed = Column(Boolean, default=False)

    # Связи
    category = relationship("Category", back_populates="tasks")
    subtasks = relationship("Task", backref="parent_task", remote_side=[id], lazy="select")
