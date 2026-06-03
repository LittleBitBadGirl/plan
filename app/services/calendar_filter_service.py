"""Правила фильтрации событий Яндекс.Календаря."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.config import settings

_CONFIG_CACHE: dict | None = None


def load_calendar_sync_config() -> dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    path = settings.config_dir / "calendar_sync.yaml"
    with open(path, encoding="utf-8") as f:
        _CONFIG_CACHE = yaml.safe_load(f) or {}
    return _CONFIG_CACHE


def reload_calendar_sync_config() -> dict[str, Any]:
    global _CONFIG_CACHE
    _CONFIG_CACHE = None
    return load_calendar_sync_config()


def name_matches(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(p.lower() in low for p in patterns)


def calendar_included(name: str, cfg: dict | None = None) -> bool:
    cfg = cfg or load_calendar_sync_config()
    cal = cfg.get("calendars", {})
    if name_matches(name, cal.get("exclude_name_patterns", [])):
        return False
    includes = cal.get("include_name_patterns", [])
    if includes:
        return name_matches(name, includes)
    return True


def is_work_calendar(name: str, cfg: dict | None = None) -> bool:
    cfg = cfg or load_calendar_sync_config()
    return name_matches(name, cfg.get("work_calendar_name_patterns", []))


def title_excluded(title: str, cfg: dict | None = None) -> str | None:
    cfg = cfg or load_calendar_sync_config()
    for exact in cfg.get("title_exclude_exact", []):
        if title.strip() == exact.strip():
            return f"exact:{exact}"
    for pattern in cfg.get("title_exclude_patterns", []):
        if re.search(pattern, title):
            return f"regex:{pattern}"
    return None


def event_planner_visible(
    title: str,
    start: datetime,
    calendar_name: str,
    cfg: dict | None = None,
    *,
    force_ignore: bool = False,
) -> tuple[bool, str | None]:
    if force_ignore:
        return False, "user_ignore"

    cfg = cfg or load_calendar_sync_config()
    reason = title_excluded(title, cfg)
    if reason:
        return False, reason

    if cfg.get("weekend_skip_for_work_calendars") and is_work_calendar(calendar_name, cfg):
        if start.weekday() >= 5:
            return False, "weekend_work_calendar"

    return True, None
