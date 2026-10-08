"""Разбор карьерного капитала: генератор и страница активов.

Страница показывает активы и пункты, а не список задач: список не разворачиваем
(решение Веры 07.10.2026). Числа на экране берутся из снимка, поэтому снимок и
проверяем: если генератор положил не то, страница покажет не то.
"""

import json
from datetime import date, datetime, timezone

import pytest

pytestmark = pytest.mark.asyncio


def _payload() -> dict:
    return {
        "period_month": "2026-09",
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "total_tasks": 3,
        "assets": [
            {
                "asset": "Тендеры и оценки",
                "count": 2,
                "lines": ["Считала смету Салини", "Проверяла смету Швецовой к подаче"],
                "low_confidence": 1,
                "maintenance": False,
                "tasks": [
                    {"id": 1, "title": "Салини смета", "p": 1.0},
                    {"id": 2, "title": "Проверить смету Швецовой", "p": 0.3},
                ],
            },
            {
                "asset": "Обслуживание",
                "count": 1,
                "lines": ["Завела 2fa на Битрикс"],
                "low_confidence": 0,
                "maintenance": True,
                "tasks": [{"id": 3, "title": "завести 2fa на битрикс", "p": 1.0}],
            },
        ],
        "failed": 0,
    }


async def _seed_review():
    from app.db.database import async_session
    from app.models.career_review import CareerReview

    async with async_session() as db:
        db.add(CareerReview(
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            period_month="2026-09",
            total_tasks=3,
            payload=json.dumps(_payload(), ensure_ascii=False),
            generator="test",
        ))
        await db.commit()


# --- генератор -------------------------------------------------------------


def test_month_bounds_and_previous_month():
    from scripts.career_review import month_bounds, previous_month

    assert month_bounds("2026-09") == (date(2026, 9, 1), date(2026, 9, 30))
    assert month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    # Переход через год: в январе разбираем декабрь, а не пустой месяц.
    assert previous_month(date(2026, 1, 15)) == "2025-12"
    assert previous_month(date(2026, 10, 7)) == "2026-09"


def test_local_date_is_local_not_utc(monkeypatch):
    """Закрытие в 00:30 МСК относится к своим суткам, а не к предыдущим.

    Зону прибиваем в самом тесте: без этого он проходил бы только на машине
    с московской зоной и падал бы в CI.
    """
    import time as _time

    from scripts.career_review import local_date

    monkeypatch.setenv("TZ", "Europe/Moscow")
    _time.tzset()

    assert local_date(datetime(2026, 10, 1, 22, 30, tzinfo=timezone.utc)) == date(2026, 10, 2)
    assert local_date(datetime(2026, 10, 1, 5, 0)) == date(2026, 10, 1)


def test_group_by_asset_keeps_maintenance_apart_from_failures():
    """Неразобранное не считается рутиной: ошибка модели не должна выглядеть работой."""
    from scripts.career_review import MAINTENANCE, group_by_asset

    grouped = group_by_asset([
        {"id": 1, "title": "смета", "asset": "Тендеры и оценки"},
        {"id": 2, "title": "завести 2fa", "asset": MAINTENANCE},
        {"id": 3, "title": "не разобралось", "asset": None},
        {"id": 4, "title": "чужой актив", "asset": "Придумано моделью"},
    ])

    assert len(grouped["Тендеры и оценки"]) == 1
    assert len(grouped[MAINTENANCE]) == 1, "в рутине только та задача, что модель правда туда отнесла"
    assert 3 not in [row["id"] for r in grouped.values() for row in r], "неразобранное не в активах"
    assert 4 not in [row["id"] for r in grouped.values() for row in r], "чужое название актива не создаёт новый актив"


def test_classify_rejects_asset_outside_the_list(monkeypatch):
    from scripts import career_review

    monkeypatch.setattr(career_review, "decide", lambda *a, **k: {"answers": {"asset": {"choice": "Продажи", "confidence": 0.9}}})
    rows = career_review.classify([{"id": 1, "title": "что-то", "p": None}], workers=1)

    assert rows[0]["asset"] is None
    assert "вне списка" in rows[0]["error"]


def test_clean_line_strips_dashes_and_emoji():
    from scripts.career_review import clean_line

    assert clean_line("Собрала смету — быстро 🚀") == "Собрала смету, быстро"
    assert "—" not in clean_line("итог — дело")


async def test_classify_marks_failed_tasks_instead_of_dropping(monkeypatch):
    """Ошибка модели не должна молча схлопывать месяц."""
    from app.services import jev_client
    from scripts import career_review

    def boom(*_args, **_kwargs):
        raise jev_client.JevError("HTTP 529")

    monkeypatch.setattr(career_review, "decide", boom)
    rows = career_review.classify([{"id": 1, "title": "смета", "p": None}], workers=1)

    assert rows[0]["asset"] is None
    assert "529" in rows[0]["error"]


# --- страница --------------------------------------------------------------


async def test_career_page_shows_assets_and_hides_the_task_list(client):
    await _seed_review()
    html = (await client.get("/career")).text

    assert "разбор за сентябрь 2026" in html
    assert "Считала смету Салини" in html
    assert "Тендеры и оценки" in html and "Обслуживание" in html
    assert "вне активов" in html, "рутина помечена отдельно от активов"
    assert "1 на проверку" in html, "бейдж низкой уверенности виден"
    # Список задач не разворачиваем: самих задач на странице быть не должно.
    assert "Салини смета" not in html
    assert "Показать" not in html


async def test_career_page_renders_without_review(client):
    """Пустая база — понятный экран, а не падение шаблона."""
    html = (await client.get("/career")).text

    assert "Разбор ещё не собирался" in html


async def test_export_returns_assets_markdown(client):
    await _seed_review()
    response = await client.get("/api/career/export")

    assert response.status_code == 200
    assert "### Тендеры и оценки (2)" in response.text
    assert "- Считала смету Салини" in response.text


async def test_career_capital_lives_inside_the_tasks_section(client):
    """В меню он стоит там же, где календарь и категории задач, а не отдельным пунктом."""
    html = (await client.get("/")).text

    assert 'href="/career"' in html
    child = html.split('href="/career"', 1)[1][:120]
    assert "pnav__child" in child, "карьерный капитал — пункт внутри раздела задач"
    # И не болтается вторым верхнеуровневым пунктом рядом с «Достижениями».
    assert html.count('href="/career"') == 1


async def test_career_refresh_runs_the_same_generator_as_cron(client, monkeypatch):
    """Кнопка и крон ходят одним кодом: страница не пересчитывает разбор сама."""
    calls = []

    async def fake_build(month):
        calls.append(month)
        return {"period_month": month, "total_tasks": 0, "assets": []}

    async def fake_save(review):
        return 1

    monkeypatch.setattr("scripts.career_review.build_review", fake_build)
    monkeypatch.setattr("scripts.career_review.save_review", fake_save)

    response = await client.post("/career/refresh")

    assert response.status_code == 303
    assert response.headers["location"] == "/career?started=1"
    assert len(calls) == 1, "сборка запускается ровно один раз"


async def test_save_review_updates_the_month_in_place(client):
    """Повторная сборка того же месяца обновляет строку, а не падает на уникальности."""
    from app.db.database import async_session
    from app.models.career_review import CareerReview
    from sqlalchemy import func, select
    from scripts.career_review import save_review

    first = await save_review(_payload())
    payload = _payload()
    payload["total_tasks"] = 42
    second = await save_review(payload)

    async with async_session() as db:
        rows = await db.execute(select(func.count(CareerReview.id)).where(CareerReview.period_month == "2026-09"))
        stored = await db.execute(select(CareerReview).where(CareerReview.period_month == "2026-09"))

    assert rows.scalar() == 1, "на месяц должна остаться одна строка"
    assert first == second, "строка обновляется на месте"
    assert json.loads(stored.scalars().first().payload)["total_tasks"] == 42


async def test_run_review_refuses_when_another_run_holds_the_lock(monkeypatch):
    """Крон и кнопка разводятся локом: вторая сборка не идёт поверх первой."""
    from scripts import career_review as cr

    monkeypatch.setattr(cr, "LOCK_PATH", "/tmp/career-review-test.lock")
    holder = cr.acquire_lock()
    assert holder is not None
    try:
        with pytest.raises(cr.ReviewBusy):
            await cr.run_review("2026-09")
    finally:
        cr.release_lock(holder)


async def test_build_review_refuses_when_the_model_is_down(monkeypatch):
    """Сбой модели не затирает прошлый разбор: лучше старый снимок, чем мусор."""
    from scripts import career_review

    async def fake_tasks(start, end):
        return [{"id": n, "title": f"задача {n}", "p": None} for n in range(1, 5)]

    monkeypatch.setattr(career_review, "load_tasks", fake_tasks)
    monkeypatch.setattr(career_review, "classify", lambda tasks, **k: [{**t, "asset": None, "p": None} for t in tasks])

    with pytest.raises(career_review.ReviewRefused):
        await career_review.build_review("2026-09")


async def test_career_page_renders_review_without_assets(client):
    from app.db.database import async_session
    from app.models.career_review import CareerReview

    payload = {"period_month": "2026-08", "total_tasks": 0, "assets": [], "unclassified": []}
    async with async_session() as db:
        db.add(CareerReview(period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
                            period_month="2026-08", total_tasks=0,
                            payload=json.dumps(payload, ensure_ascii=False), generator="test"))
        await db.commit()

    response = await client.get("/career")

    assert response.status_code == 200
    assert "разбор за август 2026" in response.text


async def test_career_page_shows_unclassified_and_escapes_model_text(client):
    """Разметка из ответа модели не должна попадать на страницу как разметка."""
    from app.db.database import async_session
    from app.models.career_review import CareerReview

    payload = _payload()
    payload["assets"][0]["lines"] = ["Считала смету <script>alert(1)</script>"]
    payload["unclassified"] = [{"id": 9, "title": "не разобралось", "error": "актив вне списка"}]
    async with async_session() as db:
        db.add(CareerReview(period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                            period_month="2026-09", total_tasks=4,
                            payload=json.dumps(payload, ensure_ascii=False), generator="test"))
        await db.commit()

    html = (await client.get("/career")).text

    assert "<script>alert(1)</script>" not in html, "разметка из модели экранируется"
    assert "&lt;script&gt;" in html
    assert "Не разобрано" in html and "не разобралось" in html


async def test_removed_hermes_endpoints_are_gone(client):
    """Маршруты удалённого «Анализа Гермеса» больше не отвечают."""
    gone = [
        ("GET", "/api/ai/prepare-analysis"),
        ("GET", "/api/hermes/analyze"),
        ("POST", "/api/hermes/request-analysis"),
        ("POST", "/api/hermes/save-analysis"),
        ("POST", "/api/ai/save-report"),
    ]
    for method, url in gone:
        response = await client.request(method, url)
        assert response.status_code in (404, 405), f"{method} {url} всё ещё живой: {response.status_code}"
