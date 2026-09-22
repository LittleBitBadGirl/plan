from datetime import date as date_type

from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Time, Text, ForeignKey, or_, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base


def task_is_active():
    """Задача не в архиве — включая строки, где is_archived = NULL.

    Колонка заводилась позже данных: часть задач (их пишет интеграция с source
    'hermes' в обход ORM) лежит с пустым is_archived. Сравнение
    `Task.is_archived == False` в SQL отбрасывает NULL, и такие задачи исчезали
    из бэклога, дашборда и счётчиков категорий. Считаем пустое значение
    «не в архиве» — так задача не пропадает, а архив по-прежнему виден по
    явной единице.
    """
    return or_(Task.is_archived.is_(None), Task.is_archived == False)  # noqa: E712


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(String, default="")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    status = Column(String(20), default="новая", index=True)  # новая/в_работе/выполнена/отложена
    priority = Column(String(20), default="средний")  # низкий/средний/высокий
    due_date = Column(Date, nullable=True, index=True)
    deadline = Column(Date, nullable=True, index=True)  # DL — крайний срок (отдельно от фокуса дня)
    due_time = Column(Time, nullable=True) # Время встречи
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String(20), default="web")  # telegram/web/screenshot
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)
    is_archived = Column(Boolean, default=False, server_default=text("0"), index=True)
    item_kind = Column(String(20), default="task", nullable=False, index=True)  # task
    sort_order = Column(Integer, default=0)
    needs_review = Column(Boolean, default=False)
    message_hash = Column(String(64), nullable=True)
    postpones = Column(Integer, default=0)
    # День, когда задачу должны были сделать, а она осталась висеть. Отсюда
    # считается «сколько тянется» — в отличие от postpones, значение не зависит
    # от того, отработал ли ночной перенос и сколько раз задачу переносили руками.
    overdue_since = Column(Date, nullable=True)
    chronic_task = Column(Boolean, default=False, index=True)
    chronic_reviewed = Column(Boolean, default=False)
    tags = Column(String(500), nullable=True) # Теги проекта или контекста (#Антон, #Сбер)
    size = Column(String(4), nullable=True)   # L / XL — крупные задачи (NULL = обычная)
    
    # Карьерный капитал и аналитика
    impact_notes = Column(Text, nullable=True) # Заметки о результате/влиянии
    is_milestone = Column(Boolean, default=False, index=True) # Флаг важного достижения
    estimated_minutes = Column(Integer, nullable=True)
    actual_minutes = Column(Integer, nullable=True)

    # Нормализация кроном: предложение «чистого» текста и вынесенной из текста
    # даты. Применяются ТОЛЬКО по кнопке подтверждения в Telegram (не молча).
    suggested_title = Column(String(500), nullable=True)
    suggested_due_date = Column(Date, nullable=True)

    # Связи
    category = relationship("Category", back_populates="tasks")
    subtasks = relationship("Task", backref="parent_task", remote_side=[id], lazy="select")

    @property
    def overdue_days(self) -> int:
        """Сколько дней задача тянется. 0 — не тянется.

        Считается в календарных днях от дня, когда её должны были сделать: если
        задача висит с 10-го, 18-го она честно показывает 8, а не то, что успел
        насчитать счётчик переносов.
        """
        anchor = self.overdue_since
        if anchor is None and self.due_date and self.due_date < date_type.today():
            anchor = self.due_date
        if anchor is None:
            return 0
        return max((date_type.today() - anchor).days, 1)
