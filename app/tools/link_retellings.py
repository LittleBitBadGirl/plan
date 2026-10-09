"""CLI: связать пересказы с книгами из «Читать».

Запуск на сервере (из каталога проекта):

    docker compose exec -T planner python -m app.tools.link_retellings
    docker compose exec -T planner python -m app.tools.link_retellings --dry-run
    docker compose exec -T planner python -m app.tools.link_retellings --retelling-id 3

Серверный Hermes зовёт этот модуль сразу после того, как записал голосовое, и по
его ответу говорит Вере, нашлась книга или нет. Ночной прогон делает то же самое
для всего, что осталось без связи (например, книгу добавили в «Читать» позже).
"""

import argparse
import asyncio

from app.db.database import async_session
from app.services.retelling_service import link_retellings, recent_retellings


async def _run(args: argparse.Namespace) -> int:
    async with async_session() as db:
        report = await link_retellings(
            db,
            retelling_id=args.retelling_id,
            apply=not args.dry_run,
            force=args.force,
        )
        print(("Проверка без записи. " if args.dry_run else "") + report.as_text())
        if args.retelling_id is not None and not report.linked and not report.already:
            # Отдельной строкой: серверному Hermes нужен однозначный признак.
            print("книга не найдена")
        for retelling in await recent_retellings(db, args.recent):
            print(
                f"  запись #{retelling.id} {retelling.recorded_at} — "
                f"{retelling.source_title or '—'}"
                f"{' → книга #' + str(retelling.book_item_id) if retelling.book_item_id else ' (без книги)'}"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Связка пересказов с книгами в «Читать»")
    parser.add_argument("--dry-run", action="store_true", help="только показать, ничего не записывать")
    parser.add_argument("--retelling-id", type=int, default=None, help="связать один пересказ")
    parser.add_argument(
        "--force",
        action="store_true",
        help="пересмотреть связку и переставить её, даже если книга уже указана",
    )
    parser.add_argument("--recent", type=int, default=5, help="сколько последних записей показать")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
