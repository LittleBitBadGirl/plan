"""Вход в подзадачи: с карточки дня и из бэклога.

Вера 24.10.2026: «в плане дня в планере невозможно добавить подзадачи, в
бэклоге тоже не понятно как, а вот добавлять последующие логика работает
нормально». Серверная ручка при этом была цела (POST /tasks/{id}/subtasks
отвечал 200), ломался именно вход: на карточке дня это была иконка с одиноким
«плюсиком», а в бэклоге блок подзадач вообще не рисовался у задачи, где
подзадач ещё нет.

Проверки сужены до карточки конкретной задачи: слово «Подзадачи» на дашборде
есть всегда (заголовок сводки), поэтому глобальный `in html` ничего не
доказывает.
"""

from datetime import date

import pytest

from app.models.task import Task


async def _make_task(db, title, planned_for=None):
    task = Task(title=title, source="web", status="новая", planned_for=planned_for)
    db.add(task)
    await db.flush()
    return task


def _card(html: str, task_id: int) -> str:
    """Кусок HTML от карточки задачи до следующей карточки (или до конца <main>).

    Ограничение по </main> обязательно: ниже идёт общий скрипт страницы, и без
    него «карточка» последней задачи растягивалась на весь документ, а ассерты
    начинали ловить текст JS-комментариев.
    """
    start = html.find(f'id="task-{task_id}"')
    assert start != -1, "карточки задачи нет на странице"
    ends = [i for i in (html.find('id="task-', start + 10), html.find("</main>", start)) if i != -1]
    return html[start:min(ends) if ends else len(html)]


@pytest.mark.asyncio
async def test_day_card_shows_labeled_subtask_entry(client, db):
    """Карточка дня: вход подписан словом, есть счётчик, форма на месте."""
    task = await _make_task(db, "Задача дня без подзадач", planned_for=date.today())
    await db.commit()

    html = (await client.get("/")).text
    assert "Задача дня без подзадач" in html, "задача не попала в день"
    card = _card(html, task.id)

    assert f'id="subtasks-{task.id}"' in card, "на карточке дня нет блока подзадач"
    assert f'hx-post="/tasks/{task.id}/subtasks"' in card, "нет формы добавления подзадачи"
    assert "Подзадачи" in card, "вход в подзадачи не подписан словом"
    assert "subtask-count" in card, "у входа нет счётчика подзадач"
    # Старый вход: иконка с одиноким «плюсиком» 10px без подписи.
    assert 'text-[10px]' not in card, "вернулся нечитаемый «плюсик» 10px"


@pytest.mark.asyncio
async def test_backlog_has_subtask_entry_for_task_without_subtasks(client, db):
    """Бэклог: у задачи без подзадач тоже есть куда добавить первую."""
    task = await _make_task(db, "Задача бэклога без подзадач")
    await db.commit()

    card = _card((await client.get("/backlog")).text, task.id)

    assert "+ подзадача" in card, "нет строки-входа в подзадачи"
    assert f'id="subtasks-{task.id}"' in card, "нет блока подзадач у задачи без подзадач"
    assert f'hx-post="/tasks/{task.id}/subtasks"' in card, "нет формы добавления подзадачи"
    assert 'name="subtask_context" value="backlog"' in card, "форма не несёт бэклоговый контекст"


@pytest.mark.asyncio
async def test_backlog_subtask_summary_shows_count(client, db):
    """У задачи с подзадачами строка подписана счётчиком, а не «+ подзадача»."""
    parent = await _make_task(db, "Родитель с подзадачами")
    db.add(Task(title="Шаг один", parent_task_id=parent.id, source="web", status="новая"))
    await db.commit()

    card = _card((await client.get("/backlog")).text, parent.id)

    assert "подзадачи: 0 из 1" in card
    assert "+ подзадача" not in card


@pytest.mark.asyncio
async def test_first_subtask_keeps_add_form(client, db):
    """После первой подзадачи форма остаётся — «следующие» добавляются так же."""
    task = await _make_task(db, "Задача дня для первой подзадачи", planned_for=date.today())
    await db.commit()

    resp = await client.post(f"/tasks/{task.id}/subtasks", data={"title": "Первый шаг"})

    assert resp.status_code == 200
    assert "Первый шаг" in resp.text
    assert "subtask-add-input" in resp.text, "форма добавления пропала после первой подзадачи"


@pytest.mark.asyncio
async def test_subtask_created_from_backlog_keeps_backlog_actions(client, db):
    """Созданная из бэклога строка получает «взять на день» и меню «⋯».

    Без этого первая же подзадача, добавленная новым входом, теряла бэклоговые
    действия: ручка собирала контекст заново и отдавала строку как дневную.
    """
    task = await _make_task(db, "Задача бэклога с первой подзадачей")
    await db.commit()

    resp = await client.post(
        f"/tasks/{task.id}/subtasks",
        data={"title": "Шаг из бэклога", "subtask_context": "backlog"},
    )

    assert resp.status_code == 200
    assert "Шаг из бэклога" in resp.text
    assert "take-subtask" in resp.text, "нет кнопки «взять на день»"
    assert 'aria-label="Ещё действия"' in resp.text, "нет меню «⋯»"


@pytest.mark.asyncio
async def test_subtask_delete_returns_updated_list(client, db):
    """После удаления последней подзадачи список говорит «Нет подзадач», форма остаётся."""
    parent = await _make_task(db, "Родитель на удаление подзадачи")
    sub = Task(title="Единственный шаг", parent_task_id=parent.id, source="web", status="новая")
    db.add(sub)
    await db.commit()
    sub_id = sub.id

    resp = await client.delete(f"/tasks/{sub_id}/subtask?ctx=backlog")

    assert resp.status_code == 200
    assert "Нет подзадач" in resp.text, "список не обновился после удаления"
    assert f'id="subtask-row-{sub_id}"' not in resp.text, "удалённая строка осталась в ответе"
    assert f'hx-post="/tasks/{parent.id}/subtasks"' in resp.text, "пропала форма добавления"
    assert 'name="subtask_context" value="backlog"' in resp.text, "потерян бэклоговый контекст"
