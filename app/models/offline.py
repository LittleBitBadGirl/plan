"""Офлайн-очередь PWA: применённые действия и конфликты слияния.

Две таблицы:

* ``offline_actions`` — журнал действий, досланных с телефона. Ключ идемпотентности
  ``client_uuid``: повторная досылка того же действия не создаёт дубль.
* ``offline_conflicts`` — расхождения, которые сервер не стал разрешать сам.
  Возникают, когда одно и то же поле изменили и офлайн, и на сервере за время
  отсутствия. Пользователь выбирает, что оставить.

Архитектура офлайн-режима: ``pwa/ARCHITECTURE.md``.
"""
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from .base import Base


class OfflineAction(Base):
    """Одно действие, досланное из офлайн-очереди телефона."""

    __tablename__ = "offline_actions"

    id = Column(Integer, primary_key=True)
    # Идентификатор действия на клиенте. Уникальный индекс — защита от дублей
    # при повторной досылке (потерянный ответ, повторное «Повторить»).
    client_uuid = Column(String(64), nullable=False, unique=True, index=True)
    kind = Column(String(32), nullable=False)
    task_id = Column(Integer, nullable=True, index=True)
    payload = Column(Text, nullable=True)
    # applied / duplicate / conflict / rejected / error
    status = Column(String(16), nullable=False, default="applied")
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    applied_at = Column(DateTime(timezone=True), nullable=True)


class OfflineConflict(Base):
    """Поле, изменённое и офлайн, и на сервере — требует решения человека."""

    __tablename__ = "offline_conflicts"

    id = Column(Integer, primary_key=True)
    client_uuid = Column(String(64), nullable=True)
    task_id = Column(Integer, nullable=False, index=True)
    task_title = Column(String(500), nullable=True)
    field = Column(String(32), nullable=False)
    base_value = Column(Text, nullable=True)
    server_value = Column(Text, nullable=True)
    local_value = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    # keep_server / keep_local
    resolution = Column(String(16), nullable=True)
