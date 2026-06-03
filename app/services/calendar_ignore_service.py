"""Игнор встреч: «Не пойду» и правила на серии."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar_event import CalendarEvent
from app.models.calendar_ignore_rule import CalendarIgnoreRule


def resolve_ignore_target(event: CalendarEvent) -> tuple[str, str]:
    """Какое правило создать при отказе от встречи."""
    if event.is_recurring:
        if event.recurrence_id:
            return "recurrence_id", event.recurrence_id
        return "series_title", event.title.strip()
    return "external_uid", event.external_uid


def event_matches_rule(
    *,
    external_uid: str,
    title: str,
    recurrence_id: str | None,
    rule_type: str,
    value: str,
) -> bool:
    if rule_type == "external_uid":
        return external_uid == value
    if rule_type == "recurrence_id":
        return recurrence_id == value or external_uid == value
    if rule_type == "series_title":
        return title.strip() == value.strip()
    return False


def event_matches_any_rule(
    *,
    external_uid: str,
    title: str,
    recurrence_id: str | None,
    rules: list[CalendarIgnoreRule] | list[dict[str, str]],
) -> bool:
    for rule in rules:
        if isinstance(rule, CalendarIgnoreRule):
            rt, val = rule.rule_type, rule.value
        else:
            rt, val = rule["rule_type"], rule["value"]
        if event_matches_rule(
            external_uid=external_uid,
            title=title,
            recurrence_id=recurrence_id,
            rule_type=rt,
            value=val,
        ):
            return True
    return False


async def load_ignore_rules(db: AsyncSession) -> list[CalendarIgnoreRule]:
    result = await db.execute(select(CalendarIgnoreRule))
    return list(result.scalars().all())


async def _apply_rule_to_events(
    db: AsyncSession,
    rule_type: str,
    value: str,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now()
    result = await db.execute(select(CalendarEvent))
    count = 0
    for ev in result.scalars().all():
        if event_matches_rule(
            external_uid=ev.external_uid,
            title=ev.title,
            recurrence_id=ev.recurrence_id,
            rule_type=rule_type,
            value=value,
        ):
            ev.planner_visible = False
            ev.filter_reason = "user_ignore"
            ev.ignored_at = now
            count += 1
    return count


async def decline_calendar_event(db: AsyncSession, event_id: int) -> dict[str, Any] | None:
    """
    «Не пойду»: правило в БД + скрыть все совпадающие встречи (включая будущие инстансы серии).
    """
    event = await db.get(CalendarEvent, event_id)
    if not event:
        return None

    rule_type, value = resolve_ignore_target(event)
    note = event.title[:500]

    existing = await db.execute(
        select(CalendarIgnoreRule).where(
            CalendarIgnoreRule.rule_type == rule_type,
            CalendarIgnoreRule.value == value,
        )
    )
    rule = existing.scalar_one_or_none()
    if not rule:
        rule = CalendarIgnoreRule(
            rule_type=rule_type,
            value=value,
            created_from_event_uid=event.external_uid,
            note=note,
        )
        db.add(rule)

    hidden = await _apply_rule_to_events(db, rule_type, value)
    await db.commit()

    scope = "series" if rule_type in ("recurrence_id", "series_title") else "once"
    return {
        "rule_type": rule_type,
        "value": value,
        "hidden": hidden,
        "scope": scope,
        "title": event.title,
    }
