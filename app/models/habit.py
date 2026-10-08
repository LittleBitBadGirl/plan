from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base

# Режимы цикла трекера.
#   days    — один цикл ровно `target_days` дней от start_date. Кончился —
#             Вера сама решает: «Продлить» (новый цикл на те же N дней) или архив.
#   monthly — непрерывный: цикл это календарный месяц, номер цикла растёт вместе
#             с календарём, ничего нажимать не надо. У месячных трекеров
#             target_days не читается (длина цикла = дней в месяце), поле держим
#             только ради старых трекеров и совместимости.
CYCLE_MODE_DAYS = "days"
CYCLE_MODE_MONTHLY = "monthly"


class Habit(Base):
    __tablename__ = "habits"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(String, default="")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    is_archived = Column(Boolean, default=False)
    start_date = Column(Date, nullable=True)
    target_days = Column(Integer, default=30)
    # У «monthly» start_date — это день заведения трекера, а не начало текущего
    # цикла: окна месячных циклов считаются от месяца старта (см. app/api/habits.py).
    cycle_mode = Column(
        String(16),
        nullable=False,
        default=CYCLE_MODE_DAYS,
        server_default=CYCLE_MODE_DAYS,
    )
    current_cycle = Column(Integer, default=1) # Номер текущего цикла
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Связи
    logs = relationship("HabitLog", back_populates="habit", cascade="all, delete-orphan")
    category = relationship("Category")
