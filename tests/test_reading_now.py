"""Блок «Читаю сейчас», пауза «не читаю» и связка пересказов с книгами.

Вера: «те книги которые я сейчас читаю надо выносить в дашборд», «надо иметь
возможность нажать кнопку не читаю и не потерять прогресс», «интереснее делать
несколько связей» — пересказы-голосовые должны находить свою книгу.
"""

import pytest

from app.models.retelling import Retelling, RetellingThought
from app.models.shopping import ShoppingItem
from app.services.retelling_service import link_retellings, match_score


async def _book(db, title, status="reading", pages_read=0, pages_total=None):
    item = ShoppingItem(
        title=title,
        item_kind="reading",
        reading_status=status,
        pages_read=pages_read,
        pages_total=pages_total,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@pytest.mark.asyncio
async def test_dashboard_shows_book_with_progress_and_retellings(client, db):
    """Книга на дашборде видна вместе с прогрессом и пересказами по ней."""
    book = await _book(db, "Русская модель управления (А. Прохоров)", pages_read=112, pages_total=488)
    retelling = Retelling(
        recorded_at="2026-10-06 11:48",
        source_title="русская модель управления",
        book_item_id=book.id,
    )
    db.add(retelling)
    await db.commit()
    db.add(RetellingThought(retelling_id=retelling.id, position=1, thought="Порядок важнее плана"))
    db.add(RetellingThought(retelling_id=retelling.id, position=2, thought="Решения обсуждают до старта"))
    await db.commit()

    html = (await client.get("/")).text
    assert 'id="reading-now"' in html
    assert "Читаю сейчас" in html
    assert "Русская модель управления" in html
    assert "112/488" in html
    assert "пересказов 1" in html and "мыслей 2" in html
    assert "Порядок важнее плана" in html


@pytest.mark.asyncio
async def test_pause_keeps_progress_and_moves_book_out_of_dashboard(client, db):
    """«Не читаю» — пауза: страницы целы, с дашборда книга уходит.

    Вера: «на паузе не надо оставлять на дашборде — нет в этом смысла! Но есть
    смысл на странице риддинг вверху сделать большой блок что читаю и там уже что
    на паузе выложить, чтоб прям видно было, что я тормознула».
    """
    book = await _book(db, "Ненасильственное общение", pages_read=31, pages_total=254)

    resp = await client.post(f"/api/reading/{book.id}/pause", data={"surface": "dashboard"})
    assert resp.status_code == 200
    assert 'id="reading-now"' in resp.text  # ответом приезжает сам блок
    assert "Ничего не читаю" in resp.text  # и отложенной книги в нём уже нет
    await db.refresh(book)
    assert book.reading_status == "paused"
    assert (book.pages_read, book.pages_total) == (31, 254)
    assert book.reading_paused_at is not None

    html = (await client.get("/")).text
    assert "Ненасильственное общение" not in html
    assert "на паузе" not in html

    reading = (await client.get("/reading")).text
    assert "Я читаю сейчас" in reading
    assert "На паузе" in reading
    assert "Ненасильственное общение" in reading
    assert "31 / 254" in reading
    assert "стоит 0 дн." in reading

    # возврат из большого блока: блок и список подменяются вместе
    resp = await client.post(f"/api/reading/{book.id}/pause", data={"surface": "top"})
    assert resp.status_code == 200
    assert 'id="reading-top"' in resp.text and "hx-swap-oob" in resp.text
    assert 'id="reading-list"' in resp.text
    assert "не читаю" in resp.text
    await db.refresh(book)
    assert book.reading_status == "reading"
    assert book.reading_paused_at is None
    assert (book.pages_read, book.pages_total) == (31, 254)


@pytest.mark.asyncio
async def test_pause_from_reading_page_returns_list(client, db):
    """С карточки на странице «Читать» пауза обновляет список, а не дашборд."""
    book = await _book(db, "Бизнес по-русски", pages_read=100, pages_total=249)
    resp = await client.post(f"/api/reading/{book.id}/pause", data={"surface": "list"})
    assert resp.status_code == 200
    # ответ — сам список карточек (обёртку #reading-list держит страница)
    assert "reading-shelf" in resp.text
    assert "reading-now" not in resp.text
    assert "продолжаю" in resp.text
    assert "100/249" in resp.text


@pytest.mark.asyncio
async def test_status_button_does_not_archive_paused_book(client, db):
    """Кнопка статуса у отложенной книги возвращает в чтение, а не в архив."""
    book = await _book(db, "Отложенная", pages_read=10, pages_total=100)
    await client.post(f"/api/reading/{book.id}/pause", data={"surface": "list"})
    await client.post(f"/api/reading/{book.id}/progress", data={})
    await db.refresh(book)
    assert book.reading_status == "reading"
    assert not book.is_archived


@pytest.mark.asyncio
async def test_link_retellings_matches_book_by_title(db):
    """Пересказ находит свою книгу по названию, чужая книга не привязывается."""
    book = await _book(db, "ненасильственное общение", pages_read=31, pages_total=254)
    await _book(db, "Алмазный огранщик", status="want_to_read")
    matched = Retelling(
        recorded_at="2026-10-06 11:48",
        source_title="Ненасильственное общение в повседневной жизни",
        source_author="Маршалл Розенберг",
    )
    stranger = Retelling(recorded_at="2026-10-07 09:00", source_title="Подкаст про огород")
    db.add_all([matched, stranger])
    await db.commit()

    report = await link_retellings(db)
    await db.refresh(matched)
    await db.refresh(stranger)
    assert matched.book_item_id == book.id
    assert stranger.book_item_id is None
    assert len(report.linked) == 1
    assert report.unmatched == [(stranger.id, "Подкаст про огород")]

    again = await link_retellings(db)
    assert not again.linked
    # несвязанный пересказ пересматривается каждый прогон: книгу могли добавить
    # в «Читать» позже — поэтому он снова в отчёте, а не потерян
    assert again.unmatched == [(stranger.id, "Подкаст про огород")]


@pytest.mark.asyncio
async def test_link_retellings_dry_run_writes_nothing(db):
    """Проверка без записи: видно, что нашлось, но связка не ставится."""
    book = await _book(db, "Русская модель управления", pages_read=112, pages_total=488)
    retelling = Retelling(recorded_at="2026-10-08 20:00", source_title="Русская модель управления")
    db.add(retelling)
    await db.commit()

    report = await link_retellings(db, retelling_id=retelling.id, apply=False)
    await db.refresh(retelling)
    assert report.linked and report.linked[0][1] == book.title
    assert retelling.book_item_id is None


@pytest.mark.asyncio
async def test_match_score_keeps_unrelated_titles_apart():
    """Порог: похожее связываем, чужое — нет."""
    assert (
        match_score(
            "ненасильственное общение",
            "Ненасильственное общение в повседневной жизни",
            "Маршалл Розенберг",
        )
        >= 0.62
    )
    assert match_score("Алмазный огранщик", "Ненасильственное общение в повседневной жизни") < 0.62
    assert match_score("Бизнес по-русски. Не усложняй. Управление (Иленков)", "Бизнес по-русски") >= 0.62
