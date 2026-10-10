"""Архивные отметки цикла (миграция 027 + compute_period_data).

Старые отметки, перенесённые из другого приложения, помечаются is_archival=1:
в средние, текущий цикл, календарь и историю циклов они не входят — считаются
отдельным блоком `archive` на /cycle.
"""
from datetime import date, timedelta
from types import SimpleNamespace

from app.web.deps import compute_period_data

TODAY = date(2026, 10, 7)

# Отметки 2026 года — как в боевой базе (с мазнёй и болью).
CURRENT_2026 = [
    ("2026-05-23", 1, 0), ("2026-05-24", 0, 0), ("2026-05-25", 0, 0),
    ("2026-06-22", 0, 1), ("2026-06-23", 0, 0), ("2026-06-24", 0, 0),
    ("2026-06-25", 0, 0), ("2026-06-26", 0, 1),
    ("2026-07-20", 0, 1), ("2026-07-21", 1, 0), ("2026-07-22", 0, 0),
    ("2026-07-23", 0, 0), ("2026-07-24", 0, 0), ("2026-07-25", 0, 1),
    ("2026-07-26", 0, 1),
    ("2026-08-19", 0, 1), ("2026-08-20", 0, 1), ("2026-08-21", 0, 0),
    ("2026-08-22", 0, 0), ("2026-08-23", 0, 0),
    ("2026-09-27", 0, 0), ("2026-09-28", 0, 0), ("2026-09-29", 0, 0),
    ("2026-09-30", 0, 1),
]

# Архив из старого приложения: 6 циклов, 24 отмеченных дня.
ARCHIVE = [
    ("2023-09-12", "2023-09-14"), ("2023-10-22", "2023-10-27"),
    ("2023-11-24", "2023-11-27"), ("2024-01-07", "2024-01-10"),
    ("2024-02-18", "2024-02-20"), ("2024-03-22", "2024-03-25"),
]


def _entry(iso: str, pain: bool = False, spotting: bool = False, archival: bool = False):
    return SimpleNamespace(
        date=date.fromisoformat(iso),
        has_pain=pain,
        is_spotting=spotting,
        is_archival=archival,
    )


def current_entries():
    return [_entry(d, bool(p), bool(s)) for d, p, s in CURRENT_2026]


def archive_entries():
    out = []
    for start, end in ARCHIVE:
        day = date.fromisoformat(start)
        last = date.fromisoformat(end)
        while day <= last:
            out.append(_entry(day.isoformat(), archival=True))
            day += timedelta(days=1)
    return out


def test_current_metrics_without_archive():
    data = compute_period_data(current_entries(), TODAY)

    assert data["avg_cycle"] == 32
    assert data["avg_period"] == 3
    assert data["cycle_stddev"] == 3.8
    assert (data["cycle_min"], data["cycle_max"]) == (28, 37)
    assert len(data["cycles_history"]) == 5
    assert data["archive"] is None


def test_archive_does_not_change_current_metrics():
    base = compute_period_data(current_entries(), TODAY)
    data = compute_period_data(current_entries() + archive_entries(), TODAY)

    assert data["avg_cycle"] == base["avg_cycle"]
    assert data["avg_period"] == base["avg_period"]
    assert data["cycle_stddev"] == base["cycle_stddev"]
    assert (data["cycle_min"], data["cycle_max"]) == (base["cycle_min"], base["cycle_max"])
    assert data["current_cycle_day"] == base["current_cycle_day"]
    assert data["last_period_start"] == base["last_period_start"]
    assert [c["start"] for c in data["cycles_history"]] == [
        c["start"] for c in base["cycles_history"]
    ]
    assert len(data["cycles_history"]) == 5


def test_archive_block_counts_old_cycles_separately():
    data = compute_period_data(current_entries() + archive_entries(), TODAY)
    archive = data["archive"]

    assert archive["count"] == 6
    assert archive["tracked_days"] == 24
    assert archive["avg_cycle"] == 38
    assert archive["avg_period"] == 4
    assert (archive["min"], archive["max"]) == (33, 44)
    assert (archive["first"], archive["last"]) == ("12.09.2023", "25.03.2024")
    assert [c["length"] for c in archive["cycles"]] == [40, 33, 44, 42, 33, None]
    assert [c["period_days"] for c in archive["cycles"]] == [3, 6, 4, 4, 3, 4]
    assert archive["cycles"][0]["start"] == "12.09.2023"
    assert archive["cycles"][0]["end"] == "14.09.2023"


def test_only_archive_keeps_card_empty_but_shows_archive():
    data = compute_period_data(archive_entries(), TODAY)

    assert data["has_data"] is False
    assert data["cycles_history"] == []
    assert data["archive"]["count"] == 6


def test_entries_without_archival_attribute_still_work():
    """Старые вызовы/тесты без поля is_archival не должны падать."""
    entries = [e for e in current_entries()]
    for e in entries:
        del e.is_archival

    data = compute_period_data(entries, TODAY)

    assert data["avg_cycle"] == 32
    assert data["archive"] is None


def test_pauza_mezhdu_prilozheniyami_ne_schitaetsya_ciklem():
    """Разрыв между старым приложением и новым (792 дня) — пауза, а не цикл:
    в средние не входит, у цикла перед паузой длина не показывается («—»)."""
    entries = [
        _entry("2024-03-22", archival=True), _entry("2024-03-23", archival=True),
        _entry("2024-03-24", archival=True), _entry("2024-03-25", archival=True),
        _entry("2026-05-23", archival=True), _entry("2026-05-24", archival=True),
        _entry("2026-05-25", archival=True),
        _entry("2026-06-22", archival=True), _entry("2026-06-23", archival=True),
        _entry("2026-06-24", archival=True),
    ]
    archive = compute_period_data(entries, TODAY)["archive"]

    assert archive["count"] == 3
    assert [c["length"] for c in archive["cycles"]] == [None, 30, None]
    assert archive["avg_cycle"] == 30
    assert (archive["min"], archive["max"]) == (30, 30)
