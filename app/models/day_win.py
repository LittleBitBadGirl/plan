from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String
from sqlalchemy.sql import func

from app.models.base import Base


class DayWin(Base):
    """День без хвоста: всё, что взято на день, закрыто.

    Вера: «если там уже были задачи и они все ушли — писать, что ты молодец,
    мы запомним этот день». Вот эта память.

    Почему отдельная таблица, а не пересчёт по ``tasks``: ночная зачистка
    стирает ``planned_for`` у незакрытых задач, поэтому у любого прошлого дня
    «на дне ничего не осталось» выполняется само собой — задним числом
    идеальными выглядели бы все дни, где была хотя бы одна закрытая задача.
    Строка пишется один раз, в момент, когда день закрыт (или в момент ночной
    зачистки, пока данные ещё честные), и убирается, если день снова получил
    хвост.

    ``level``: ``tasks`` — закрыты все задачи дня; ``full`` — ещё и все
    регулярные, которые были на этот день.
    """

    __tablename__ = "day_wins"

    id = Column(Integer, primary_key=True, index=True)
    day = Column(Date, nullable=False, unique=True, index=True)
    level = Column(String(16), nullable=False, server_default="tasks")
    tasks_done = Column(Integer, nullable=False, server_default="0")
    recurring_done = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
