#!/usr/bin/env python3
"""
Phase 0: проверка CalDAV Яндекса — список календарей и preview событий с фильтрами.

Usage (from project root):
  python scripts/caldav_probe.py
  python scripts/caldav_probe.py --days 7
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from app.config import settings  # noqa: E402


def _load_sync_config() -> dict:
    path = PROJECT_ROOT / "config" / "calendar_sync.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _name_matches(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(p.lower() in low for p in patterns)


def _calendar_included(name: str, cfg: dict) -> bool:
    cal = cfg.get("calendars", {})
    if _name_matches(name, cal.get("exclude_name_patterns", [])):
        return False
    includes = cal.get("include_name_patterns", [])
    if includes:
        return _name_matches(name, includes)
    return True


def _is_work_calendar(name: str, cfg: dict) -> bool:
    return _name_matches(name, cfg.get("work_calendar_name_patterns", []))


def _title_excluded(title: str, cfg: dict) -> str | None:
    for exact in cfg.get("title_exclude_exact", []):
        if title.strip() == exact.strip():
            return f"exact:{exact}"
    for pattern in cfg.get("title_exclude_patterns", []):
        if re.search(pattern, title):
            return f"regex:{pattern}"
    return None


def _event_visible(
    title: str,
    start: datetime,
    calendar_name: str,
    cfg: dict,
) -> tuple[bool, str]:
    reason = _title_excluded(title, cfg)
    if reason:
        return False, reason

    if cfg.get("weekend_skip_for_work_calendars") and _is_work_calendar(calendar_name, cfg):
        if start.weekday() >= 5:
            return False, "weekend_work_calendar"

    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Yandex CalDAV probe")
    parser.add_argument("--days", type=int, default=7, help="Horizon forward from today")
    args = parser.parse_args()

    user = settings.yandex_caldav_user
    password = settings.yandex_caldav_app_password
    if not user or not password:
        print("❌ Заполните YANDEX_CALDAV_USER и YANDEX_CALDAV_APP_PASSWORD в .env")
        return 1

    try:
        import caldav
    except ImportError:
        print("❌ Установите: pip install caldav pyyaml")
        return 1

    cfg = _load_sync_config()
    principal_url = f"https://caldav.yandex.ru/principals/users/{user}/"

    print(f"🔗 Principal: {principal_url}\n")

    try:
        client = caldav.DAVClient(url=principal_url, username=user, password=password)
        principal = client.principal()
        calendars = principal.calendars()
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return 1

    print(f"📚 Найдено календарей: {len(calendars)}\n")
    print("-" * 72)

    today = date.today()
    start_dt = datetime.combine(today, datetime.min.time())
    end_dt = start_dt + timedelta(days=args.days)

    included_urls: list[str] = []
    imported: list[dict] = []
    skipped: list[dict] = []

    for cal in calendars:
        name = getattr(cal, "name", None) or str(cal)
        url = str(cal.url)
        included = _calendar_included(name, cfg)
        flag = "✅ SYNC" if included else "⏭ SKIP"

        print(f"{flag}  {name}")
        print(f"       {url}")

        if included:
            included_urls.append(url)

        if not included:
            print()
            continue

        try:
            search = getattr(cal, "search", None)
            if callable(search):
                events = search(
                    start=start_dt,
                    end=end_dt,
                    event=True,
                    expand=True,
                )
            else:
                events = cal.date_search(start=start_dt, end=end_dt, expand=True)
        except Exception as e:
            print(f"       ⚠️  events error: {e}\n")
            continue

        for ev in events:
            vevent = ev.vobject_instance.vevent
            title = str(getattr(vevent, "summary", {}).value) if hasattr(vevent, "summary") else "(без названия)"
            dtstart = vevent.dtstart.value
            if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
                start = datetime.combine(dtstart, datetime.min.time())
            else:
                start = dtstart
                if getattr(start, "tzinfo", None):
                    start = start.replace(tzinfo=None)

            uid = str(vevent.uid.value) if hasattr(vevent, "uid") else ""
            has_rrule = hasattr(vevent, "rrule")
            visible, reason = _event_visible(title, start, name, cfg)

            row = {
                "title": title,
                "start": start.strftime("%Y-%m-%d %H:%M"),
                "calendar": name,
                "uid": uid[:40],
                "recurring": has_rrule,
            }
            if visible:
                imported.append(row)
            else:
                skipped.append({**row, "reason": reason})

        print()

    print("=" * 72)
    print(f"\n📋 URL для .env (YANDEX_CALENDAR_URLS= через запятую):\n")
    print(",".join(included_urls))

    print(f"\n✅ Попадут в планер ({len(imported)}):")
    for r in sorted(imported, key=lambda x: x["start"]):
        rec = " 🔁" if r["recurring"] else ""
        print(f"   {r['start']}  {r['title'][:50]}{rec}  [{r['calendar'][:25]}]")

    print(f"\n⏭ Отфильтровано ({len(skipped)}):")
    for r in sorted(skipped, key=lambda x: x["start"]):
        print(f"   {r['start']}  {r['title'][:40]}  ({r['reason']})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
