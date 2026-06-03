from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base


class RecurringCompletion(Base):
    """Журнал выполнений/пропусков регулярных задач (одна запись на день вхождения)."""

    __tablename__ = "recurring_completions"
    __table_args__ = (
        UniqueConstraint("recurring_task_id", "occurrence_date", name="uq_recurring_occurrence"),
    )

    id = Column(Integer, primary_key=True, index=True)
    recurring_task_id = Column(Integer, ForeignKey("recurring_tasks.id"), nullable=False, index=True)
    occurrence_date = Column(Date, nullable=False, index=True)
    status = Column(String(20), nullable=False)  # completed | missed
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    recurring_task = relationship("RecurringTask", back_populates="completions")
