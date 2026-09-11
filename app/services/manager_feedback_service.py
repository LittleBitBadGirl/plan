"""Бизнес-логика блока «Менеджеры»: контекст виджета, попапа, загрузка пруфов."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy import desc, func, select

from app.models.manager import Manager, ManagerFeedback

RU_MONTHS_FULL = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

KIND_LABELS = {"minus": "минус", "note": "наблюдение", "plus": "плюс"}
KIND_ORDER = ("minus", "note", "plus")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic"}
DOC_SUFFIXES = {".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx"}
ALLOWED_SUFFIXES = IMAGE_SUFFIXES | DOC_SUFFIXES

MAX_FILE_BYTES = 12 * 1024 * 1024
UPLOAD_SUBDIR = "feedback"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _project_root() -> Path:
    # app/services/manager_feedback_service.py -> services -> app -> plan/
    return Path(__file__).resolve().parents[2]


def uploads_dir() -> Path:
    directory = _project_root() / "uploads" / UPLOAD_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_upload_path(relative: str) -> Optional[Path]:
    """Путь внутри uploads/ — защита от '..' из БД."""
    uploads_root = (_project_root() / "uploads").resolve()
    candidate = (uploads_root / (relative or "")).resolve()
    if uploads_root not in candidate.parents:
        return None
    return candidate


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def records_word(n: int) -> str:
    return plural(n, "запись", "записи", "записей")


def month_label(period: str) -> str:
    try:
        year, month = period.split("-")
        return f"{RU_MONTHS_FULL[int(month)]} {year}"
    except (ValueError, KeyError, AttributeError):
        return period or ""


def parse_links(raw: str) -> List[str]:
    """Оставляем только http(s) — чтобы в href не попало javascript:."""
    links: List[str] = []
    for part in re.split(r"[\s,;]+", raw or ""):
        part = part.strip()
        if part.startswith(("http://", "https://")) and len(part) > 8:
            links.append(part)
    return links[:20]


def parse_record_date(raw: str) -> date:
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


def dump_list(values: List[str]) -> Optional[str]:
    return json.dumps(values, ensure_ascii=False) if values else None


def load_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def normalize_kind(kind: str) -> str:
    return kind if kind in KIND_LABELS else "note"


async def save_uploads(files: List[UploadFile]) -> List[str]:
    """Сохранить пруфы в uploads/feedback, вернуть относительные пути."""
    saved: List[str] = []
    target_dir = uploads_dir()
    for upload in files or []:
        filename = getattr(upload, "filename", "") or ""
        if not filename:
            continue
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            continue
        try:
            content = await upload.read()
        except Exception:  # noqa: BLE001 — битый аплоад не должен ронять запись
            continue
        if not content or len(content) > MAX_FILE_BYTES:
            continue
        name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{suffix}"
        (target_dir / name).write_bytes(content)
        saved.append(f"{UPLOAD_SUBDIR}/{name}")
    return saved


# --------------------------------------------------------------------------
# context builders
# --------------------------------------------------------------------------

async def build_widget_context(db) -> dict:
    """Данные компактного блока на дашборде."""
    period = date.today().strftime("%Y-%m")
    manager_res = await db.execute(
        select(Manager)
        .where(Manager.is_active.is_(True))
        .order_by(Manager.sort_order, Manager.id)
    )
    managers = manager_res.scalars().all()

    month_res = await db.execute(
        select(ManagerFeedback.manager_id, func.count(ManagerFeedback.id))
        .where(ManagerFeedback.period_month == period)
        .group_by(ManagerFeedback.manager_id)
    )
    month_counts = dict(month_res.all())

    total_res = await db.execute(
        select(ManagerFeedback.manager_id, func.count(ManagerFeedback.id))
        .group_by(ManagerFeedback.manager_id)
    )
    total_counts = dict(total_res.all())

    last_res = await db.execute(
        select(ManagerFeedback.manager_id, func.max(ManagerFeedback.date))
        .group_by(ManagerFeedback.manager_id)
    )
    last_dates = dict(last_res.all())

    items = []
    for manager in managers:
        count = month_counts.get(manager.id, 0)
        last = last_dates.get(manager.id)
        items.append(
            {
                "id": manager.id,
                "name": manager.name,
                "projects": manager.projects or "",
                "month_count": count,
                "month_word": records_word(count),
                "total_count": total_counts.get(manager.id, 0),
                "last_date": last.strftime("%d.%m.%Y") if last else "",
            }
        )

    return {"managers": items, "period_label": month_label(period)}


async def build_modal_context(db, manager_id: int, error: str = "") -> dict:
    """Данные попапа: записи, сгруппированные по месяцам (свежие сверху)."""
    manager_res = await db.execute(select(Manager).where(Manager.id == manager_id))
    manager = manager_res.scalar_one_or_none()
    if not manager:
        raise HTTPException(status_code=404, detail="Менеджер не найден")

    rows_res = await db.execute(
        select(ManagerFeedback)
        .where(ManagerFeedback.manager_id == manager_id)
        .order_by(desc(ManagerFeedback.date), desc(ManagerFeedback.id))
    )
    rows = rows_res.scalars().all()

    months: List[dict] = []
    by_period: dict = {}
    for row in rows:
        period = row.period_month or row.date.strftime("%Y-%m")
        bucket = by_period.get(period)
        if bucket is None:
            bucket = {"period": period, "label": month_label(period), "entries": []}
            by_period[period] = bucket
            months.append(bucket)

        files = []
        for path in load_list(row.files):
            files.append(
                {
                    "url": f"/uploads/{path}",
                    "name": Path(path).name,
                    "is_image": Path(path).suffix.lower() in IMAGE_SUFFIXES,
                }
            )

        bucket["entries"].append(
            {
                "id": row.id,
                "date_str": row.date.strftime("%d.%m.%Y"),
                "kind": row.kind,
                "kind_label": KIND_LABELS.get(row.kind, row.kind),
                "project": row.project or "",
                "text": row.text,
                "links": load_list(row.links),
                "files": files,
            }
        )

    for bucket in months:
        bucket["count"] = len(bucket["entries"])
        bucket["word"] = records_word(bucket["count"])

    total = len(rows)
    return {
        "manager": {
            "id": manager.id,
            "name": manager.name,
            "projects": manager.projects or "",
        },
        "months": months,
        "total": total,
        "total_word": records_word(total),
        "today": date.today().isoformat(),
        "kinds": [
            {"value": kind, "label": KIND_LABELS[kind], "cls": f"mf-kind--{kind}"}
            for kind in KIND_ORDER
        ],
        "error": error,
    }
