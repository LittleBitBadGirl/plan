#!/usr/bin/env python3
"""Разбор карьерного капитала за месяц: активы, пункты, доказательства.

Запуск (внутри контейнера планировщика):
    python scripts/career_review.py                # прошлый полный месяц
    python scripts/career_review.py --month 2026-09
    python scripts/career_review.py --dry-run      # посчитать и напечатать, не сохранять

Что делает:
1. Берёт ЗАКРЫТЫЕ корневые задачи месяца (локальные сутки, без регулярных и подзадач).
2. Раскладывает каждую по активам через Jev (decision-модель на OpenRouter).
3. Пишет по каждому активу короткие пункты строго по списку задач месяца.
4. Кладёт готовый снимок в `career_reviews`: страница рисуется из него и ничего
   не считает на лету.

Рутина («Обслуживание») остаётся отдельным блоком и не растворяется в активах.
Оценок «высокий или низкий вклад» нет: на коротких заголовках Jev ставит их
наугад (проверено на 126 задачах 07.10.2026), поэтому страница показывает
активу работу, а не рейтинг.
"""

import argparse
import asyncio
import fcntl
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.database import async_session  # noqa: E402
from app.models.career_review import CareerReview  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.services.ai_service import _text_key, _text_url  # noqa: E402
from app.services.jev_client import JevError, decide  # noqa: E402

MAINTENANCE = "Обслуживание"

# Если модель не разобрала больше четверти задач месяца, снимок не сохраняем:
# иначе сбой модели затрёт прошлый разбор, а в логах будет «успех».
MAX_UNCLASSIFIED = 0.25


class ReviewRefused(RuntimeError):
    """Разбор не сохранён: слишком много задач осталось без актива."""

# Активы Веры: цифры важнее названия, поэтому описания длинные и с примерами,
# а «Обслуживание» описано как последнее средство, иначе оно собирает лишнее.
ASSET_CRITERIA = {
    "Проекты и сдача": "управление проектом: сроки, бюджет, риски, сдача этапов, эскалации, работа с заказчиком",
    "Тендеры и оценки": "оценка, смета, коммерческое предложение, защита цены, квалификация",
    "Команда и подрядчики": "найм, собеседования, распределение работы, развитие людей, контроль подрядчика",
    "Продукт и запуск": "новая функциональность, внедрение, изменение процесса в продукте",
    "Инструменты и ИИ": "свой инструмент или автоматизация, инфраструктура, работа с ИИ-моделями, разбор кода",
    "Публичность и бренд": "статья, выступление, конференция, резюме, обучение других, PR",
    "Обучение": "курс, сертификат, новая компетенция для себя",
    MAINTENANCE: (
        "сюда только то, что ничего не создаёт и не меняет: доступы, подписки, оплаты, "
        "уборка, мелкие технические правки, пересылка документов"
    ),
}

ASSET_ORDER = [
    "Проекты и сдача",
    "Тендеры и оценки",
    "Инструменты и ИИ",
    "Команда и подрядчики",
    "Продукт и запуск",
    "Публичность и бренд",
    "Обучение",
    MAINTENANCE,
]

CLASSIFY_QUESTION = {
    "asset": {
        "type": "choice",
        "instructions": (
            "В какой карьерный актив идёт эта работа. Вера — Account Director в digital-агентстве, "
            "подрядная разработка (СберМобайл, СберМаркетинг, Атол, Майоли). Выбирай по СУТИ работы, "
            "а не по слову в заголовке."
        ),
        "criteria": ASSET_CRITERIA,
    }
}


def month_bounds(month: str) -> tuple[date, date]:
    """Границы месяца «2026-09» → (1 сентября, 30 сентября)."""
    year, mon = (int(part) for part in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start, end.fromordinal(end.toordinal() - 1)


def previous_month(today: date | None = None) -> str:
    today = today or date.today()
    first = today.replace(day=1)
    previous = first.fromordinal(first.toordinal() - 1)
    return f"{previous.year:04d}-{previous.month:02d}"


def local_date(moment: datetime) -> date:
    """Момент из базы → локальная календарная дата (в базе UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().date()


async def load_tasks(start: date, end: date) -> list[dict]:
    """Закрытые корневые задачи месяца по локальным суткам."""
    async with async_session() as db:
        rows = await db.execute(
            select(Task.id, Task.title, Task.completed_at, Task.item_kind)
            .where(
                Task.status == "выполнена",
                Task.completed_at.is_not(None),
                Task.parent_task_id.is_(None),
                Task.item_kind == "task",
            )
            .order_by(Task.completed_at)
        )
    tasks = []
    for task_id, title, completed_at, _kind in rows.all():
        day = local_date(completed_at)
        if start <= day <= end:
            tasks.append({"id": task_id, "title": (title or "").strip(), "p": None})
    return tasks


def clean_line(text: str) -> str:
    """Пункт от модели — без длинных тире и эмодзи: запрет в промпте не гарантия."""
    text = text.replace(" — ", ", ").replace("—", "-").replace("–", "-")
    return "".join(ch for ch in text if ch not in EMOJI).strip()


def classify(tasks: list[dict], workers: int = 4) -> list[dict]:
    """Актив для каждой задачи. Ошибки Jev не роняют разбор: задача помечается.

    Ответ модели проверяется по списку активов: название вне списка считается
    ошибкой, а не новым активом, иначе задача выпала бы из разбора молча.
    """

    def one(task: dict) -> dict:
        try:
            answer = decide({"задача": task["title"], "категория": "", "описание": "нет"}, CLASSIFY_QUESTION)
            asset = answer["answers"]["asset"]
            choice = str(asset["choice"]).strip()
            if choice not in ASSET_ORDER:
                return {**task, "asset": None, "p": None, "error": f"актив вне списка: {choice[:60]}"}
            return {**task, "asset": choice, "p": asset.get("confidence")}
        except (JevError, KeyError, TypeError) as error:
            return {**task, "asset": None, "p": None, "error": str(error)[:120]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, tasks))


EMOJI = set("\U0001F300\U0001F600\U0001F900\U0001F680\U0001F1E6\u2600\u2705\u274C\u2757\u2B50\U0001F44D")


WRITER_RULES = (
    "Пиши по-русски, короткими пунктами, каждый пункт с новой строки и без нумерации. "
    "Время прошедшее. Без эмодзи. Без длинных тире. Без канцелярита и рекламных слов "
    "(эффективно, успешно, на высоком уровне, ключевой). Строго без чисел, процентов, "
    "сумм и сроков, которых нет в списке задач. Не придумывай проекты, которых нет в списке. "
    "Каждый пункт должен опираться на одну или несколько задач из списка, и его должно быть "
    "можно проверить по этому списку. Две-три короткие фразы, не больше."
)


async def write_lines(asset: str, tasks: list[dict]) -> list[str]:
    """Пункты по активу. Ничего не выдумываем: на входе только заголовки задач."""
    if not tasks:
        return []
    titles = "\n".join(f"- {task['title']}" for task in tasks)
    prompt = (
        f"Ниже список закрытых рабочих задач Веры за месяц, отнесённых к активу «{asset}».\n"
        f"Сформулируй, что она сделала по этому активу и что это даёт в карьерном капитале.\n"
        f"{WRITER_RULES}\n\nЗадачи:\n{titles}\n\n"
        'Ответ строго в JSON: {"lines": ["первый пункт", "второй"]}'
    )
    url = _text_url() if _text_key() else "https://api.groq.com/openai/v1/chat/completions"
    key = _text_key() or settings.groq_api_key
    if not key:
        return []
    model = settings.ai_categorize_model if _text_key() else "llama-3.3-70b-versatile"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "system", "content": prompt}],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                },
                timeout=60.0,
            )
        if response.status_code == 200:
            data = json.loads(response.json()["choices"][0]["message"]["content"])
            lines = data.get("lines") or []
            return [str(line).strip() for line in lines if str(line).strip()]
    except Exception as error:  # noqa: BLE001
        print(f"  пункты для «{asset}» не получились: {error}", file=sys.stderr)
    return []


def group_by_asset(tasks: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {asset: [] for asset in ASSET_ORDER}
    for task in tasks:
        asset = task.get("asset")
        if asset in grouped:
            grouped[asset].append(task)
    # Неразобранные задачи сюда не попадают: их считает отдельный блок, иначе
    # ошибка модели выглядела бы как честная рутина.
    return {asset: rows for asset, rows in grouped.items() if rows}


async def build_review(month: str) -> dict:
    start, end = month_bounds(month)
    tasks = await load_tasks(start, end)
    print(f"{month}: закрытых корневых задач {len(tasks)}")
    # classify крутит threads и блокирующий urllib: в общем цикле событий это
    # остановило бы весь планировщик на время опроса модели.
    classified = await asyncio.to_thread(classify, tasks) if tasks else []
    unclassified = [task for task in classified if not task.get("asset")]
    grouped = group_by_asset(classified)
    print("по активам:", {asset: len(rows) for asset, rows in grouped.items()})

    assets = []
    for asset in ASSET_ORDER:
        rows = grouped.get(asset)
        if not rows:
            continue
        rows.sort(key=lambda row: -(row.get("p") or 0))
        lines = [line for line in (clean_line(raw) for raw in await write_lines(asset, rows)) if line]
        low = sum(1 for row in rows if (row.get("p") or 0) < 0.5)
        assets.append({
            "asset": asset,
            "count": len(rows),
            "lines": lines,
            "low_confidence": low,
            "maintenance": asset == MAINTENANCE,
            "tasks": [
                {"id": row["id"], "title": row["title"], "p": row.get("p")}
                for row in rows
            ],
        })

    share = len(unclassified) / len(tasks) if tasks else 0.0
    if tasks and share > MAX_UNCLASSIFIED:
        raise ReviewRefused(
            f"{month}: модель не разобрала {len(unclassified)} задач из {len(tasks)} "
            f"({share:.0%}), порог {MAX_UNCLASSIFIED:.0%}. Снимок не сохраняется."
        )

    return {
        "period_month": month,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "total_tasks": len(tasks),
        "assets": assets,
        "unclassified": [
            {"id": row["id"], "title": row["title"], "error": row.get("error")}
            for row in unclassified
        ],
        "failed": len(unclassified),
    }


async def save_review(review: dict) -> int:
    """Снимок месяца: строка обновляется на месте, второй строки быть не может.

    Раньше старый снимок удалялся и на его место писался новый. При уникальном
    месяце это зависело от порядка DELETE и INSERT внутри одной транзакции, и
    повторная сборка того же месяца могла упасть на ограничении. Обновление
    существующей строки убирает эту лотерею совсем.
    """
    async with async_session() as db:
        existing = await db.execute(
            select(CareerReview).where(CareerReview.period_month == review["period_month"])
        )
        row = existing.scalars().first()
        if row is None:
            row = CareerReview(period_month=review["period_month"])
            db.add(row)
        row.period_start = date.fromisoformat(review["period_start"])
        row.period_end = date.fromisoformat(review["period_end"])
        row.total_tasks = review["total_tasks"]
        row.payload = json.dumps(review, ensure_ascii=False)
        row.generator = "career_review.py"
        await db.commit()
        return row.id


LOCK_PATH = "/tmp/career-review.lock"


class ReviewBusy(RuntimeError):
    """Разбор уже идёт: лок занят другой сборкой (кнопка или крон)."""


def acquire_lock():
    """Лок на разбор: крон и кнопка ходят в одном контейнере, файл их разводит.

    Внутренний флаг в приложении крон не видит, поэтому без файла две сборки
    могли пойти одновременно и подраться за снимок одного месяца.
    """
    handle = None
    try:
        handle = open(LOCK_PATH, "w")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        if handle is not None:
            handle.close()
        return None


def release_lock(handle) -> None:
    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
    except OSError:
        pass


async def run_review(month: str) -> dict:
    """Лок, сборка и запись одним вызовом — то, что зовут и крон, и кнопка.

    Лок держится до конца записи: иначе между сборкой и сохранением остаётся
    окно, в которое вторая сборка успевает начать свою.
    """
    lock = acquire_lock()
    if lock is None:
        raise ReviewBusy("разбор уже идёт (лок занят)")
    try:
        review = await build_review(month)
        await save_review(review)
        return review
    finally:
        release_lock(lock)


def print_review(review: dict) -> None:
    """Печать разбора в лог: пункты по активам и число неразобранных задач."""
    for asset in review["assets"]:
        print(f"\n== {asset['asset']} ({asset['count']})")
        for line in asset["lines"]:
            print("   ", line)
        if not asset["lines"]:
            print("    (пунктов нет)")
    print(f"не разобрано: {review.get('failed') or 0} из {review['total_tasks']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Разбор карьерного капитала за месяц")
    parser.add_argument("--month", default=None, help="месяц в формате 2026-09 (по умолчанию прошлый полный)")
    parser.add_argument("--dry-run", action="store_true", help="посчитать и напечатать, не сохранять")
    args = parser.parse_args()

    month = args.month or previous_month()

    if args.dry_run:
        # Пробный прогон: считаем, печатаем, в базу не пишем. Лок берём и здесь,
        # чтобы не мешать идущей сборке.
        lock = acquire_lock()
        if lock is None:
            print("разбор уже идёт (лок занят), выходим", file=sys.stderr)
            return 1
        try:
            review = asyncio.run(build_review(month))
        except ReviewRefused as refusal:
            print(str(refusal), file=sys.stderr)
            return 1
        finally:
            release_lock(lock)
        print_review(review)
        print("\n--dry-run: в базу не писали")
        return 0

    try:
        # Тем же путём ходит кнопка на странице: лок держится до конца записи.
        review = asyncio.run(run_review(month))
    except (ReviewRefused, ReviewBusy) as problem:
        print(str(problem), file=sys.stderr)
        return 1
    print_review(review)
    print(f"\nсохранено: разбор за {review['period_month']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
