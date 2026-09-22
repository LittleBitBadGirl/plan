"""Офлайн-синхронизация задач: применение действий телефона и слияние изменений.

Зачем отдельный сервис, а не повторное использование HTMX-роутов: роуты отвечают
HTML-фрагментами и не умеют того, что нужно офлайну — идемпотентности и слияния.
Здесь одна точка входа: клиент присылает пачку действий, получает по каждому
результат (применено / дубль / конфликт / отклонено) и актуальное состояние
затронутых задач.

Главное правило слияния — «не перетирать». В каждом изменении поля хранится не
только новое значение, но и то, каким клиент его видел (``from``). При досылке:

* поле на сервере всё ещё равно ``from`` → правка применяется;
* поле уже другое → сервер не угадывает и не затирает: создаётся запись в
  ``offline_conflicts``, поле остаётся серверным, Вера выбирает, что оставить.

Так два разных поля, изменённые в разных местах, съезжаются оба, а спор по одному
полю не теряет ни одну из сторон.

Границы: только задачи (дашборд, бэклог, архив, подзадачи). Финансы, покупки,
чтение, календарь офлайн не правятся — см. pwa/ARCHITECTURE.md.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.offline import OfflineAction, OfflineConflict
from app.models.task import Task, task_is_active
from app.models.category import Category

# Поля задачи, которые разрешено менять из офлайн-очереди.
EDITABLE_FIELDS = (
    "title",
    "description",
    "category_id",
    "priority",
    "due_date",
    "deadline",
    "status",
    "size",
    "impact_notes",
)

DATE_FIELDS = ("due_date", "deadline")

FIELD_LABELS = {
    "title": "Название",
    "description": "Описание",
    "category_id": "Категория",
    "priority": "Приоритет",
    "due_date": "Дата",
    "deadline": "Дедлайн",
    "status": "Статус",
    "size": "Размер",
    "impact_notes": "Заметки",
    "is_archived": "В архиве",
}

_TIME_PREFIX_RE = re.compile(r"^(\d{1,2}:\d{2})\s+(.*)$")


def _norm(value: Any) -> str:
    """Привести значение поля к сравнимой строке.

    Сравнение идёт по строкам, потому что клиент присылает JSON: дата приезжает
    как ``2026-09-25``, пустое значение — как ``""``, а в базе лежит ``date``.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _parse_date(value: Any) -> date | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # ДД.ММ без года — как в вебе, берём текущий год.
    match = re.match(r"^(\d{1,2})\.(\d{1,2})$", text)
    if match:
        today = date.today()
        return date(today.year, int(match.group(2)), int(match.group(1)))
    raise ValueError(f"Не понимаю дату: {text!r}")


def _split_time(title: str) -> tuple[str, time | None]:
    """«12:00 Задача» → («Задача», 12:00). Повторяет разбор из /tasks/create."""
    match = _TIME_PREFIX_RE.match(title.strip())
    if not match:
        return title, None
    try:
        return match.group(2), time.fromisoformat(match.group(1).zfill(5))
    except ValueError:
        return title, None


def _task_state(task: Task) -> dict[str, Any]:
    """Состояние задачи для клиента: база для сравнения и для отрисовки."""
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "category_id": task.category_id,
        "priority": task.priority,
        "due_date": _norm(task.due_date),
        "due_time": task.due_time.strftime("%H:%M") if task.due_time else "",
        "deadline": _norm(task.deadline),
        "status": task.status,
        "size": task.size,
        "impact_notes": task.impact_notes,
        "is_archived": bool(task.is_archived),
        "parent_task_id": task.parent_task_id,
        "postpones": task.postpones or 0,
    }


async def _load_task(db: AsyncSession, task_id: int | None) -> Task | None:
    if not task_id:
        return None
    result = await db.execute(select(Task).where(Task.id == int(task_id)))
    return result.scalar_one_or_none()


async def _known_action(db: AsyncSession, client_uuid: str) -> OfflineAction | None:
    result = await db.execute(
        select(OfflineAction).where(OfflineAction.client_uuid == client_uuid)
    )
    return result.scalar_one_or_none()


async def _remember(
    db: AsyncSession,
    client_uuid: str,
    kind: str,
    task_id: int | None,
    payload: dict,
    status: str,
    detail: str = "",
) -> OfflineAction:
    record = OfflineAction(
        client_uuid=client_uuid,
        kind=kind,
        task_id=task_id,
        payload=json.dumps(payload, ensure_ascii=False),
        status=status,
        detail=detail,
        applied_at=datetime.now(timezone.utc),
    )
    db.add(record)
    return record


def _rejected(client_uuid: str, message: str, task_id: int | None = None) -> dict[str, Any]:
    """Отказ без записи в журнал.

    Журнал — это «действие уже применено». Отклонённое действие применено не
    было, поэтому его нельзя туда писать: иначе после устранения причины (Вера
    вошла заново, поправила данные) повторная досылка получила бы ответ
    «duplicate» и правка исчезла бы молча.
    """
    return {
        "client_uuid": client_uuid,
        "status": "rejected",
        "task_id": task_id,
        "message": message,
    }


async def _add_conflict(
    db: AsyncSession,
    client_uuid: str,
    task: Task,
    field: str,
    base_value: Any,
    server_value: Any,
    local_value: Any,
) -> OfflineConflict:
    conflict = OfflineConflict(
        client_uuid=client_uuid,
        task_id=task.id,
        task_title=(task.title or "")[:500],
        field=field,
        base_value=_norm(base_value),
        server_value=_norm(server_value),
        local_value=_norm(local_value),
    )
    db.add(conflict)
    return conflict


async def _sync_parent(db: AsyncSession, parent_id: int) -> None:
    """Синхронизировать ДЛ родителя с подзадачами.

    Та же логика, что в ``app/web/routes/tasks.py``: импорт ленивый, чтобы не
    держать сервис зависимым от модуля роутов на этапе загрузки.
    """
    from app.web.routes.tasks import _sync_parent as sync_parent_impl

    await sync_parent_impl(db, parent_id)


async def _apply_update_fields(
    db: AsyncSession, client_uuid: str, task: Task, payload: dict
) -> dict[str, Any]:
    changes = payload.get("changes") or []
    applied: list[str] = []
    conflicts: list[dict] = []

    for change in changes:
        field = change.get("field")
        if field not in EDITABLE_FIELDS:
            conflicts.append({"field": str(field), "reason": "поле не редактируется офлайн"})
            continue

        current = getattr(task, field, None)
        base = change.get("from", "")
        new_value = change.get("to", "")

        if _norm(current) == _norm(new_value):
            # Уже такое же — не конфликт, просто ничего не делаем.
            applied.append(field)
            continue

        if _norm(current) != _norm(base):
            await _add_conflict(
                db,
                client_uuid,
                task,
                field,
                base_value=base,
                server_value=current,
                local_value=new_value,
            )
            conflicts.append(
                {
                    "field": field,
                    "label": FIELD_LABELS.get(field, field),
                    "server_value": _norm(current),
                    "local_value": _norm(new_value),
                }
            )
            continue

        if field in DATE_FIELDS:
            setattr(task, field, _parse_date(new_value))
        elif field == "category_id":
            text = str(new_value or "").strip()
            setattr(task, field, int(text) if text.isdigit() else None)
        else:
            setattr(task, field, new_value)
        applied.append(field)

    return {
        "status": "conflict" if conflicts else "applied",
        "applied_fields": applied,
        "conflicts": conflicts,
        "task_id": task.id,
    }


async def _apply_one(
    db: AsyncSession, action: dict
) -> dict[str, Any]:
    client_uuid = str(action.get("client_uuid") or "").strip()
    kind = str(action.get("kind") or "").strip()
    task_id = action.get("task_id")
    payload = action.get("payload") or {}

    if not client_uuid:
        return {"client_uuid": client_uuid, "status": "rejected", "message": "нет client_uuid"}

    known = await _known_action(db, client_uuid)
    if known:
        return {
            "client_uuid": client_uuid,
            "status": "duplicate",
            "task_id": known.task_id,
            "message": "уже применено",
        }

    task = await _load_task(db, task_id)

    if kind == "create_task":
        title = str(payload.get("title") or "").strip()
        if not title:
            return _rejected(client_uuid, "пустое название")

        clean_title, due_time = _split_time(title)
        category = str(payload.get("category_id") or "").strip()
        new_task = Task(
            title=clean_title,
            description=str(payload.get("description") or "").strip() or None,
            due_date=_parse_date(payload.get("due_date")) or date.today(),
            due_time=due_time,
            category_id=int(category) if category.isdigit() else None,
            deadline=_parse_date(payload.get("deadline")),
            size=str(payload.get("size") or "").strip() or None,
            source="offline",
            status="новая",
            item_kind="task",
        )
        db.add(new_task)
        await db.flush()
        await _remember(db, client_uuid, kind, new_task.id, payload, "applied")
        return {
            "client_uuid": client_uuid,
            "status": "applied",
            "task_id": new_task.id,
            "task": _task_state(new_task),
        }

    if kind == "create_subtask":
        parent = await _load_task(db, task_id)
        if not parent:
            return _rejected(client_uuid, "задача не найдена", task_id)
        title = str(payload.get("title") or "").strip()
        if not title:
            return _rejected(client_uuid, "пустое название", task_id)
        subtask = Task(
            title=title,
            parent_task_id=parent.id,
            deadline=_parse_date(payload.get("deadline")),
            source="offline",
            status="новая",
            item_kind="task",
        )
        # due_date подзадаче не ставим: в вебе подзадача создаётся без даты, иначе
        # она всплывает отдельной строкой в списке задач.
        db.add(subtask)
        await db.flush()
        await _sync_parent(db, parent.id)
        await _remember(db, client_uuid, kind, subtask.id, payload, "applied")
        return {
            "client_uuid": client_uuid,
            "status": "applied",
            "task_id": subtask.id,
            "task": _task_state(subtask),
        }

    if not task:
        return _rejected(client_uuid, "задача не найдена", task_id)

    if kind == "update_fields":
        changes = payload.get("changes") or []
        bad = [str(change.get("field")) for change in changes if change.get("field") not in EDITABLE_FIELDS]
        if bad:
            return _rejected(client_uuid, f"эти поля офлайн не правятся: {', '.join(bad)}", task.id)
        result = await _apply_update_fields(db, client_uuid, task, payload)
        await _remember(
            db,
            client_uuid,
            kind,
            task.id,
            payload,
            result["status"],
            json.dumps(result.get("conflicts", []), ensure_ascii=False),
        )
        result["client_uuid"] = client_uuid
        result["task"] = _task_state(task)
        return result

    if kind == "complete":
        if task.status == "выполнена" and task.is_archived:
            return {
                "client_uuid": client_uuid,
                "status": "duplicate",
                "task_id": task.id,
                "task": _task_state(task),
            }
        if task.is_archived:
            # Задача уже в архиве, но не как выполненная (её отправили в архив
            # или удалили в вебе). Конфликта здесь нет: у «отметить выполненной»
            # и «вернуть из архива» разный смысл, выбирать нечего.
            return _rejected(
                client_uuid,
                "задача уже в архиве — открой архив, если её надо вернуть",
                task.id,
            )

        if task.parent_task_id:
            from app.web.routes.tasks import _complete_subtask_impl

            await _complete_subtask_impl(db, task)
        else:
            task.status = "выполнена"
            task.completed_at = datetime.now(timezone.utc)
            task.is_archived = True
            task.item_kind = "task"
            task.overdue_since = None  # закрытая задача больше не «тянется»
            children = await db.execute(
                select(Task).where(
                    Task.parent_task_id == task.id,
                    Task.status != "выполнена",
                )
            )
            for child in children.scalars().all():
                child.status = "выполнена"
                child.completed_at = datetime.now(timezone.utc)
                child.is_archived = False

        await _remember(db, client_uuid, kind, task.id, payload, "applied")
        return {
            "client_uuid": client_uuid,
            "status": "applied",
            "task_id": task.id,
            "task": _task_state(task),
        }

    if kind == "archive":
        task.is_archived = True
        task.item_kind = "task"
        await _remember(db, client_uuid, kind, task.id, payload, "applied")
        return {
            "client_uuid": client_uuid,
            "status": "applied",
            "task_id": task.id,
            "task": _task_state(task),
        }

    if kind == "unarchive":
        # Как веб-роут /archive/{id}/restore: задача возвращается новой, а не
        # «выполненной».
        task.is_archived = False
        task.status = "новая"
        task.completed_at = None
        task.overdue_since = None  # вернули из архива — отсчёт просрочки с нуля
        await _remember(db, client_uuid, kind, task.id, payload, "applied")
        return {
            "client_uuid": client_uuid,
            "status": "applied",
            "task_id": task.id,
            "task": _task_state(task),
        }

    if kind == "plan":
        # Перенос на дату: семантика веб-роута /tasks/{id}/plan. Без
        # apply_manual_plan с телефона терялся счётчик переносов.
        from app.services.postpones_service import apply_manual_plan

        new_due = _parse_date(payload.get("due_date")) or date.today()
        base = payload.get("from")
        if base is not None and _norm(task.due_date) != _norm(base):
            await _add_conflict(
                db,
                client_uuid,
                task,
                "due_date",
                base_value=base,
                server_value=task.due_date,
                local_value=new_due,
            )
            await _remember(db, client_uuid, kind, task.id, payload, "conflict", "дата менялась")
            return {
                "client_uuid": client_uuid,
                "status": "conflict",
                "task_id": task.id,
                "conflicts": [
                    {
                        "field": "due_date",
                        "label": FIELD_LABELS["due_date"],
                        "server_value": _norm(task.due_date),
                        "local_value": new_due.isoformat(),
                    }
                ],
                "task": _task_state(task),
            }

        apply_manual_plan(task, task.due_date, new_due)
        task.due_date = new_due
        task.status = "новая"
        await _remember(db, client_uuid, kind, task.id, payload, "applied")
        return {
            "client_uuid": client_uuid,
            "status": "applied",
            "task_id": task.id,
            "task": _task_state(task),
        }

    if kind == "to_backlog":
        task.due_date = None
        task.postpones = 0
        task.status = "новая"
        await _remember(db, client_uuid, kind, task.id, payload, "applied")
        return {
            "client_uuid": client_uuid,
            "status": "applied",
            "task_id": task.id,
            "task": _task_state(task),
        }

    if kind == "delete_subtask":
        # В вебе удаление подзадачи — физическое (db.delete), повторяем как есть.
        if task.parent_task_id is None:
            return _rejected(client_uuid, "это не подзадача", task.id)
        parent_id = task.parent_task_id
        await db.delete(task)
        await db.flush()
        # Родителя не трогаем: веб-роут DELETE /tasks/{id}/subtask его тоже не
        # синхронизирует, а расходиться с вебом поведение не должно.
        await _remember(db, client_uuid, kind, parent_id, payload, "applied")
        return {"client_uuid": client_uuid, "status": "applied", "task_id": parent_id}

    return _rejected(client_uuid, f"неизвестное действие: {kind}", task_id)


async def apply_actions(db: AsyncSession, actions: Iterable[dict]) -> dict[str, Any]:
    """Применить пачку действий офлайн-очереди.

    Каждое действие коммитится отдельно: одно битое не должно откатывать
    остальные (и не должно повторно применяться на следующей досылке).
    """
    results: list[dict] = []
    touched: list[int] = []

    for action in actions:
        try:
            result = await _apply_one(db, action)
            await db.commit()
        except Exception as exc:  # noqa: BLE001 — наверх нужен отчёт, а не 500
            await db.rollback()
            results.append(
                {
                    "client_uuid": action.get("client_uuid"),
                    "status": "error",
                    "message": str(exc)[:300],
                }
            )
            continue

        results.append(result)
        if result.get("task_id"):
            touched.append(int(result["task_id"]))
        if result.get("task"):
            touched.append(int(result["task"]["id"]))

    tasks = await tasks_state(db, sorted(set(touched)))
    return {
        "results": results,
        "tasks": tasks,
        "conflicts_total": await conflicts_count(db),
    }


async def tasks_state(db: AsyncSession, task_ids: list[int] | None = None) -> list[dict]:
    """Состояние задач для клиента.

    Без ``task_ids`` — полный срез того, что реально правится с телефона:
    все неархивные задачи. Архивные в срез не входят: они не правятся офлайн,
    а восстановление из архива в правке не нуждается.
    """
    query = select(Task)
    if task_ids:
        query = query.where(Task.id.in_(task_ids))
    else:
        query = query.where(task_is_active())  # noqa: E712

    result = await db.execute(query)
    tasks = result.scalars().all()
    return [_task_state(task) for task in tasks]


async def conflicts_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(OfflineConflict).where(OfflineConflict.resolved_at.is_(None))
    )
    return len(result.scalars().all())


async def list_conflicts(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(OfflineConflict)
        .where(OfflineConflict.resolved_at.is_(None))
        .order_by(OfflineConflict.created_at.desc())
    )
    conflicts = result.scalars().all()
    listed = []
    for c in conflicts:
        listed.append(
            {
                "id": c.id,
                "task_id": c.task_id,
                "task_title": c.task_title,
                "field": c.field,
                "label": FIELD_LABELS.get(c.field, c.field),
                "base_value": c.base_value,
                "server_value": c.server_value,
                "local_value": c.local_value,
                # Человекочитаемый вид: номер категории Вере ничего не говорит.
                "server_display": await _display_value(db, c.field, c.server_value),
                "local_display": await _display_value(db, c.field, c.local_value),
                "created_at": c.created_at.isoformat() if c.created_at else "",
            }
        )
    return listed


def _human_date(value: Any) -> str:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{match.group(3)}.{match.group(2)}.{match.group(1)}" if match else str(value or "")


async def _display_value(db: AsyncSession, field: str, value: Any) -> str:
    """Значение поля в том виде, в каком его понимает Вера."""
    text = _norm(value)
    if not text:
        return "(пусто)"
    if field == "category_id":
        if not text.isdigit():
            return "(без категории)"
        result = await db.execute(select(Category).where(Category.id == int(text)))
        category = result.scalar_one_or_none()
        return category.name if category else f"категория {text}"
    if field in DATE_FIELDS:
        return _human_date(text)
    return text


async def resolve_conflict(db: AsyncSession, conflict_id: int, resolution: str) -> dict[str, Any]:
    """Разрешить конфликт: оставить серверное значение или поставить своё."""
    if resolution not in ("keep_server", "keep_local"):
        return {"ok": False, "message": "resolution: keep_server или keep_local"}

    result = await db.execute(
        select(OfflineConflict).where(OfflineConflict.id == conflict_id)
    )
    conflict = result.scalar_one_or_none()
    if not conflict:
        return {"ok": False, "message": "конфликт не найден"}
    if conflict.resolved_at is not None:
        return {"ok": False, "message": "уже разрешён"}

    if resolution == "keep_local":
        task = await _load_task(db, conflict.task_id)
        if not task:
            return {"ok": False, "message": "задача не найдена"}
        field = conflict.field
        if field not in EDITABLE_FIELDS:
            return {"ok": False, "message": f"поле «{field}» офлайн не правится"}

        # Пока конфликт ждал решения, поле могли изменить в планере ещё раз.
        # Затирать в этом случае нельзя — показываем новое значение и просим
        # выбрать заново.
        if _norm(getattr(task, field, None)) != _norm(conflict.server_value):
            conflict.server_value = _norm(getattr(task, field, None))
            await db.commit()
            return {
                "ok": False,
                "message": "поле снова изменилось в планере — посмотри и выбери заново",
                "conflicts_total": await conflicts_count(db),
            }

        if field in DATE_FIELDS:
            setattr(task, field, _parse_date(conflict.local_value))
        elif field == "category_id":
            text = str(conflict.local_value or "").strip()
            setattr(task, field, int(text) if text.isdigit() else None)
        else:
            setattr(task, field, conflict.local_value or "")

    conflict.resolution = resolution
    conflict.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    tasks = await tasks_state(db, [conflict.task_id]) if conflict.task_id else []
    return {
        "ok": True,
        "resolution": resolution,
        "tasks": tasks,
        "conflicts_total": await conflicts_count(db),
    }
