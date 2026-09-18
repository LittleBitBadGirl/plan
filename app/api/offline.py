"""API офлайн-синхронизации PWA.

Три точки входа:

* ``GET /api/offline/state`` — срез задач: база, с которой телефон сравнивает
  офлайн-правки, и данные для отрисовки без сети.
* ``POST /api/offline/sync`` — пачка действий из очереди телефона.
* ``GET /api/offline/conflicts`` + ``POST /api/offline/conflicts/{id}/resolve`` —
  спорные поля: показываются Вере, решение принимает она.

Авторизация — та же, что у остального API: cookie ``api_token`` (её ставит
``/login``), поэтому отдельной схемы для телефона не нужно.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body
from app.db.database import async_session
from app.services.offline_sync_service import (
    apply_actions,
    list_conflicts,
    resolve_conflict,
    tasks_state,
)

router = APIRouter(prefix="/api/offline", tags=["offline"])


@router.get("/state")
async def offline_state():
    """Срез задач для офлайн-базы на телефоне."""
    async with async_session() as db:
        tasks = await tasks_state(db)
    return {
        "tasks": tasks,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/sync")
async def offline_sync(payload: dict = Body(...)):
    """Применить действия, накопленные без сети.

    Тело: ``{"actions": [{"client_uuid": "...", "kind": "...", "task_id": 1,
    "payload": {...}}, ...]}``.
    """
    actions = payload.get("actions") or []
    if not isinstance(actions, list):
        return {"results": [], "tasks": [], "conflicts_total": 0, "error": "actions должен быть списком"}

    async with async_session() as db:
        return await apply_actions(db, actions)


@router.get("/conflicts")
async def offline_conflicts():
    """Конфликты, ждущие решения Веры."""
    async with async_session() as db:
        conflicts = await list_conflicts(db)
    return {"conflicts": conflicts}


@router.post("/conflicts/{conflict_id}/resolve")
async def offline_conflict_resolve(conflict_id: int, payload: dict = Body(...)):
    """Разрешить конфликт: ``keep_server`` или ``keep_local``."""
    resolution = str(payload.get("resolution") or "")
    async with async_session() as db:
        return await resolve_conflict(db, conflict_id, resolution)
