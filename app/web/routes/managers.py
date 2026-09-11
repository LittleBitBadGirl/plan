"""Обратная связь по менеджерам: блок на дашборде + попап с историей по месяцам.

Виджет:   GET  /managers/widget                (обновление блока после добавления)
Попап:    GET  /managers/{manager_id}/modal
Записи:   POST /api/managers/{manager_id}/feedback
          POST /api/managers/feedback/{feedback_id}/delete
Люди:     POST /api/managers/create
"""

from __future__ import annotations

from datetime import date
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.db.database import async_session
from app.models.manager import Manager, ManagerFeedback
from app.services.manager_feedback_service import (
    build_modal_context,
    build_widget_context,
    dump_list,
    load_list,
    normalize_kind,
    parse_links,
    parse_record_date,
    safe_upload_path,
    save_uploads,
)
from app.web.deps import templates

router = APIRouter()


@router.get("/managers/widget", response_class=HTMLResponse)
async def managers_widget(request: Request):
    """Компактный блок менеджеров (дашборд + обновление после записи)."""
    async with async_session() as db:
        widget = await build_widget_context(db)
    response = templates.TemplateResponse(
        request,
        "partials/managers_widget.html",
        {"request": request, "managers_widget": widget},
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@router.get("/managers/{manager_id}/modal", response_class=HTMLResponse)
async def manager_feedback_modal(request: Request, manager_id: int):
    """Попап: вся история по менеджеру, по месяцам."""
    async with async_session() as db:
        context = await build_modal_context(db, manager_id)
    context["request"] = request
    return templates.TemplateResponse(
        request, "partials/manager_feedback_modal.html", context
    )


@router.post("/api/managers/create", response_class=HTMLResponse)
async def create_manager(
    request: Request,
    name: str = Form(""),
    projects: str = Form(""),
):
    """Добавить менеджера в блок."""
    clean_name = (name or "").strip()
    async with async_session() as db:
        if clean_name:
            order_res = await db.execute(select(func.max(Manager.sort_order)))
            sort_order = (order_res.scalar() or 0) + 1
            db.add(
                Manager(
                    name=clean_name[:200],
                    projects=(projects or "").strip()[:500] or None,
                    is_active=True,
                    sort_order=sort_order,
                )
            )
            await db.commit()
        widget = await build_widget_context(db)
    return templates.TemplateResponse(
        request,
        "partials/managers_widget.html",
        {"request": request, "managers_widget": widget},
    )


@router.post("/api/managers/{manager_id}/feedback", response_class=HTMLResponse)
async def create_manager_feedback(
    request: Request,
    manager_id: int,
    text: str = Form(""),
    kind: str = Form("minus"),
    project: str = Form(""),
    links: str = Form(""),
    fb_date: str = Form(""),
    files: List[UploadFile] = File(default=[]),
):
    """Добавить запись обратной связи (дата по умолчанию — сегодня)."""
    async with async_session() as db:
        manager_res = await db.execute(select(Manager).where(Manager.id == manager_id))
        if not manager_res.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Менеджер не найден")

        clean_text = (text or "").strip()
        saved_files = await save_uploads(files)
        if not clean_text and not saved_files:
            context = await build_modal_context(
                db, manager_id, error="Нужен текст или хотя бы один скрин."
            )
            context["request"] = request
            return templates.TemplateResponse(
                request, "partials/manager_feedback_modal.html", context
            )

        record_date = parse_record_date(fb_date)
        db.add(
            ManagerFeedback(
                manager_id=manager_id,
                date=record_date,
                period_month=record_date.strftime("%Y-%m"),
                kind=normalize_kind(kind),
                project=(project or "").strip()[:200] or None,
                text=clean_text,
                links=dump_list(parse_links(links)),
                files=dump_list(saved_files),
                source="web",
            )
        )
        await db.commit()
        context = await build_modal_context(db, manager_id)

    context["request"] = request
    return templates.TemplateResponse(
        request, "partials/manager_feedback_modal.html", context
    )


@router.post("/api/managers/feedback/{feedback_id}/delete", response_class=HTMLResponse)
async def delete_manager_feedback(request: Request, feedback_id: int):
    """Удалить запись вместе с загруженными пруфами."""
    async with async_session() as db:
        record_res = await db.execute(
            select(ManagerFeedback).where(ManagerFeedback.id == feedback_id)
        )
        record = record_res.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Запись не найдена")

        manager_id = record.manager_id
        for relative in load_list(record.files):
            path = safe_upload_path(relative)
            if path and path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass

        await db.delete(record)
        await db.commit()
        context = await build_modal_context(db, manager_id)

    context["request"] = request
    return templates.TemplateResponse(
        request, "partials/manager_feedback_modal.html", context
    )


@router.post("/api/managers/feedback/{feedback_id}/edit", response_class=HTMLResponse)
async def update_manager_feedback(
    request: Request,
    feedback_id: int,
    text: str = Form(""),
    kind: str = Form("minus"),
    project: str = Form(""),
    links: str = Form(""),
    fb_date: str = Form(""),
    remove_files: List[str] = Form(default=[]),
    files: List[UploadFile] = File(default=[]),
):
    """Изменить существующую запись: текст, тип, проект, дата, ссылки, пруфы.

    remove_files — пути пруфов, которые надо убрать (чекбоксы в форме правки).
    Новые файлы добавляются к оставшимся.
    """
    async with async_session() as db:
        record_res = await db.execute(
            select(ManagerFeedback).where(ManagerFeedback.id == feedback_id)
        )
        record = record_res.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Запись не найдена")

        manager_id = record.manager_id
        existing_files = load_list(record.files)
        to_remove = [path for path in (remove_files or []) if path in existing_files]
        kept_files = [path for path in existing_files if path not in to_remove]

        # Сначала проверяем, что запись не станет пустой, и только потом трогаем диск:
        # иначе можно удалить пруфы и вернуть ошибку, оставив в записи битые ссылки.
        clean_text = (text or "").strip()
        kept_files.extend(await save_uploads(files))
        if not clean_text and not kept_files:
            context = await build_modal_context(
                db, manager_id, error="Нужен текст или хотя бы один скрин."
            )
            context["request"] = request
            return templates.TemplateResponse(
                request, "partials/manager_feedback_modal.html", context
            )

        for relative in to_remove:
            target = safe_upload_path(relative)
            if target and target.is_file():
                try:
                    target.unlink()
                except OSError:
                    pass

        record_date = parse_record_date(fb_date)
        record.text = clean_text
        record.kind = normalize_kind(kind)
        record.project = (project or "").strip()[:200] or None
        record.links = dump_list(parse_links(links))
        record.files = dump_list(kept_files)
        record.date = record_date
        record.period_month = record_date.strftime("%Y-%m")
        await db.commit()
        context = await build_modal_context(db, manager_id)

    context["request"] = request
    return templates.TemplateResponse(
        request, "partials/manager_feedback_modal.html", context
    )
