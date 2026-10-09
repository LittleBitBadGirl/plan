"""Пересказы: связка с книгами из «Читать» и метрики для дашборда.

Вера: «я серверному гермесу пишу голосовые с 5 мыслями по прочитанному — он их
куда-то сохраняет; выясни куда; как будто интереснее делать несколько связей».
Он сохраняет их в planner.db (таблицы ``retellings``/``retelling_thoughts``) и
связывает с отметкой привычки 11. Второй связи — с книгой в списке «Читать» — не
было: ``source_title`` свободный текст, и «Ненасильственное общение в
повседневной жизни» никак не указывало на запись «ненасильственное общение».

Здесь эта связь и находится. Сопоставляем по названию, а не требуем точного
совпадения: Вера называет книгу в голосовом как помнит, в списке она записана
короче. Книга необязательна: пересказ подкаста или книги, которой нет в
«Читать», остаётся без связи — это нормальное состояние, а не ошибка.
"""

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.retelling import Retelling, RetellingThought
from app.models.shopping import ShoppingItem

# Слова, которые встречаются в половине названий и потому ничего не различают.
STOPWORDS = {
    "книга", "книги", "книгу", "часть", "том", "издание", "версия", "выпуск",
    "статья", "подкаст", "видео", "перевод", "серия", "полное", "сборник",
}

# Ниже этого счёта связку не ставим: лучше «не нашёл», чем связать с чужой
# книгой и показывать на дашборде чужие пересказы.
MATCH_THRESHOLD = 0.62

_QUOTES = "«»\"'“”„`"
_PARENTHESIS = re.compile(r"\([^)]*\)")
_NOT_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize_title(raw: str | None) -> str:
    """Название к сравнению: регистр, ё, кавычки, скобки, знаки.

    «Ненасильственное общение в повседневной жизни» → «ненасильственное общение
    в повседневной жизни», «Русская модель управления (А. Прохоров)» → «русская
    модель управления». Автор в скобках не мешает: он отдельным полем.
    """
    text = (raw or "").strip()
    text = _PARENTHESIS.sub(" ", text)
    text = text.translate({ord(ch): " " for ch in _QUOTES})
    text = text.lower().replace("ё", "е")
    text = _NOT_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def _tokens(raw: str | None) -> set[str]:
    """Слова длиннее трёх букв: «в», «и», «на» ничего не решают."""
    return {
        word
        for word in normalize_title(raw).split()
        if len(word) > 3 and word not in STOPWORDS
    }


def match_score(book_title: str, retelling_title: str, author: str | None = None) -> float:
    """Насколько название книги из «Читать» похоже на то, что Вера назвала вслух."""
    book = normalize_title(book_title)
    said = normalize_title(retelling_title)
    if not book or not said:
        return 0.0
    if book == said:
        return 1.0
    # Автор в названии записи — сильный признак: «Маршалл Розенберг».
    with_author = f"{said} {normalize_title(author)}".strip() if author else said
    if book and book in with_author:
        return 0.95
    if len(book) >= 8 and (book in said or said in book):
        return 0.85
    book_tokens = _tokens(book_title)
    said_tokens = _tokens(retelling_title)
    if not book_tokens or not said_tokens:
        return 0.0
    shared = book_tokens & said_tokens
    if not shared:
        return 0.0
    # Делим на меньшее множество: книга в «Читать» обычно названа короче, чем
    # Вера говорит вслух, и лишние слова с её стороны не должны ронять связку.
    return 0.62 + 0.3 * (len(shared) / min(len(book_tokens), len(said_tokens)))


async def reading_books(db: AsyncSession) -> list[ShoppingItem]:
    """Все записи «Читать», включая прочитанные: книгу связываем и после архива."""
    result = await db.execute(
        select(ShoppingItem).where(ShoppingItem.item_kind == "reading")
    )
    return list(result.scalars().all())


async def find_book(
    db: AsyncSession, retelling: Retelling, books: list[ShoppingItem] | None = None
) -> ShoppingItem | None:
    """Книга для пересказа или None, если уверенного совпадения нет."""
    candidates = books if books is not None else await reading_books(db)
    best: ShoppingItem | None = None
    best_score = 0.0
    for book in candidates:
        score = match_score(book.title or "", retelling.source_title or "", retelling.source_author)
        if score > best_score:
            best, best_score = book, score
    return best if best_score >= MATCH_THRESHOLD else None


@dataclass
class LinkReport:
    """Что получилось при прогоне связывания — для CLI и для логов."""

    linked: list[tuple[int, str, str]]        # (id пересказа, книга, чем зацепилось)
    unmatched: list[tuple[int, str]]           # (id пересказа, как названо в записи)
    # Уже связанные в обычном прогоне не читаются вовсе (в выборку идут только
    # те, у кого книги нет), поэтому счётчик заполняется при запросе одного
    # пересказа или с --force.
    already: int = 0

    def as_text(self) -> str:
        lines = [f"связано: {len(self.linked)}, без книги: {len(self.unmatched)}, было связано: {self.already}"]
        for rid, book, reason in self.linked:
            lines.append(f"  #{rid} → «{book}» ({reason})")
        for rid, said in self.unmatched:
            lines.append(f"  #{rid} без книги: «{said}»")
        return "\n".join(lines)


async def link_retellings(
    db: AsyncSession,
    retelling_id: int | None = None,
    apply: bool = True,
    force: bool = False,
) -> LinkReport:
    """Связать пересказы с книгами. ``apply=False`` — только показать, что нашлось.

    ``retelling_id`` — один пересказ (серверный Hermes зовёт сразу после записи
    голосового, чтобы ответить Вере «нашёл книгу» или «не нашёл»), без него —
    все несвязанные (ночной прогон).
    """
    query = select(Retelling)
    if retelling_id is not None:
        query = query.where(Retelling.id == retelling_id)
    elif not force:
        query = query.where(Retelling.book_item_id.is_(None))
    restored = await db.execute(query)
    retellings = list(restored.scalars().all())

    books = await reading_books(db)
    report = LinkReport(linked=[], unmatched=[])

    for retelling in retellings:
        if retelling.book_item_id is not None:
            report.already += 1
            if retelling_id is not None and not force:
                continue
        book = await find_book(db, retelling, books)
        if book is None:
            report.unmatched.append((retelling.id, retelling.source_title or "—"))
            continue
        reason = "точное название" if match_score(book.title or "", retelling.source_title or "", retelling.source_author) >= 0.9 else "похожее название"
        report.linked.append((retelling.id, book.title or "", reason))
        if apply:
            retelling.book_item_id = book.id

    if apply and report.linked:
        await db.commit()
    return report


async def book_retelling_stats(db: AsyncSession, book_ids: list[int]) -> dict[int, dict]:
    """Сколько пересказов и мыслей у каждой книги + когда был последний.

    Ключ — id книги; книги без пересказов в словаре отсутствуют, вызывающий код
    показывает «пересказов пока нет» по умолчанию.
    """
    if not book_ids:
        return {}
    result = await db.execute(
        select(
            Retelling.book_item_id,
            func.count(Retelling.id),
            func.max(Retelling.recorded_at),
        )
        .where(Retelling.book_item_id.in_(book_ids))
        .group_by(Retelling.book_item_id)
    )
    stats: dict[int, dict] = {}
    for book_id, count, last_at in result.all():
        stats[book_id] = {
            "count": int(count or 0),
            "thoughts": 0,
            "last_day": (last_at or "")[:10],
        }
    thoughts = await db.execute(
        select(Retelling.book_item_id, func.count(RetellingThought.id))
        .join(RetellingThought, RetellingThought.retelling_id == Retelling.id)
        .where(Retelling.book_item_id.in_(book_ids))
        .group_by(Retelling.book_item_id)
    )
    for book_id, count in thoughts.all():
        stats.setdefault(book_id, {"count": 0, "thoughts": 0, "last_day": ""})["thoughts"] = int(count or 0)
    return stats


async def last_thoughts(db: AsyncSession, book_ids: list[int]) -> dict[int, dict]:
    """Самая свежая мысль по каждой книге — то, что Вера говорила последним.

    Показываем на дашборде рядом с книгой: связать прочитанное с тем, что уже
    сказано вслух, и есть смысл «нескольких связей».
    """
    if not book_ids:
        return {}
    result = await db.execute(
        select(Retelling)
        .where(Retelling.book_item_id.in_(book_ids))
        .order_by(Retelling.recorded_at.desc(), Retelling.id.desc())
    )
    out: dict[int, dict] = {}
    for retelling in result.scalars().all():
        if retelling.book_item_id in out:
            continue
        thoughts = sorted(retelling.thoughts or [], key=lambda t: t.position or 0)
        out[retelling.book_item_id] = {
            "day": (retelling.recorded_at or "")[:10],
            "chapter": retelling.chapter or "",
            "summary": retelling.summary or "",
            "thoughts": [t.thought for t in thoughts],
            "thought": thoughts[0].thought if thoughts else (retelling.summary or ""),
        }
    return out


async def recent_retellings(db: AsyncSession, limit: int = 5) -> list[Retelling]:
    """Последние пересказы — для блока на дашборде и для сверки в CLI."""
    result = await db.execute(
        select(Retelling).order_by(Retelling.recorded_at.desc(), Retelling.id.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def retellings_for_day(db: AsyncSession, day: date) -> int:
    """Сколько пересказов записано в этот день (по дате записи, МСК)."""
    prefix = day.isoformat()
    result = await db.execute(
        select(func.count()).select_from(Retelling).where(Retelling.recorded_at.like(f"{prefix}%"))
    )
    return int(result.scalar_one() or 0)


async def forget_link(db: AsyncSession, retelling_id: int) -> None:
    """Снять связку с книгой (если связалось не туда) — сама запись не трогается."""
    result = await db.execute(select(Retelling).where(Retelling.id == retelling_id))
    retelling = result.scalar_one_or_none()
    if retelling is not None:
        retelling.book_item_id = None
        await db.commit()
