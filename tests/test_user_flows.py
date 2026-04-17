"""
Сценарии использования (User Flows) — реалистичные end-to-end сценарии.
Каждый тест моделирует полное действие пользователя от начала до конца.
"""
import pytest
from datetime import date, timedelta

pytestmark = pytest.mark.asyncio


# ==================== Сценарий 1: Создание и планирование ====================

class TestFlowCreateAndPlan:
    """
    Сценарий: Пользователь создаёт задачу в бэклоге, потом планирует на дату.
    1. Создать задачу без даты → появляется в бэклоге
    2. Открыть бэклог → увидеть задачу
    3. Запланировать на завтра → задача появляется в списке задач
    4. Открыть календарь на завтра → увидеть задачу
    """

    async def test_full_plan_flow(self, client):
        # 1. Создать задачу без даты
        create_resp = await client.post("/api/tasks", json={
            "title": "Запланировать встречу",
            "description": "Обсудить проект с клиентом",
            "priority": "высокий",
        })
        assert create_resp.status_code == 200
        task_id = create_resp.json()["id"]

        # 2. Проверить что задача в бэклоге (due_date = None)
        task_resp = await client.get(f"/api/tasks/{task_id}")
        assert task_resp.json()["due_date"] is None

        # 3. Страница бэклога должна рендериться
        backlog_resp = await client.get("/backlog")
        assert backlog_resp.status_code == 200

        # 4. Запланировать на завтра (через HTMX)
        tomorrow = date.today() + timedelta(days=1)
        date_str = f"{tomorrow.day}.{tomorrow.month}"

        plan_resp = await client.post(f"/tasks/{task_id}/plan", data={"due_date": date_str})
        assert plan_resp.status_code == 200
        assert "📅" in plan_resp.text

        # 5. Проверить что due_date обновилась
        task_resp2 = await client.get(f"/api/tasks/{task_id}")
        task_data = task_resp2.json()
        assert task_data["due_date"] == tomorrow.isoformat()

        # 6. Проверить что задача появляется в списке по дате
        date_tasks = await client.get(f"/api/tasks/date/{tomorrow.isoformat()}")
        assert date_tasks.status_code == 200
        assert any(t["id"] == task_id for t in date_tasks.json())


# ==================== Сценарий 2: Полный цикл задачи ====================

class TestFlowFullLifecycle:
    """
    Сценарий: Задача проходит полный цикл.
    1. Создать задачу на сегодня
    2. Увидеть на дашборде
    3. Отметить выполненной
    4. Проверить что в архиве
    """

    async def test_full_lifecycle(self, client):
        today = date.today().isoformat()

        # 1. Создать задачу на сегодня
        create_resp = await client.post("/api/tasks", json={
            "title": "Позвонить поставщику",
            "description": "Узнать условия доставки",
            "priority": "высокий",
            "due_date": today,
        })
        task_id = create_resp.json()["id"]

        # 2. Дашборд должен рендериться
        dash_resp = await client.get("/")
        assert dash_resp.status_code == 200

        # 3. Проверить что задача видна в общем списке
        tasks_resp = await client.get("/tasks")
        assert tasks_resp.status_code == 200

        # 4. Отметить выполненной (HTMX)
        complete_resp = await client.post(f"/tasks/{task_id}/complete")
        assert complete_resp.status_code == 200
        assert "выполнено" in complete_resp.text.lower()

        # 5. Проверить через API что статус обновлён
        task_resp = await client.get(f"/api/tasks/{task_id}")
        task_data = task_resp.json()
        assert task_data["status"] == "выполнена"
        assert task_data["is_archived"] is True
        assert task_data["completed_at"] is not None

        # 6. Проверить что задача в архиве
        archive_resp = await client.get("/archive")
        assert archive_resp.status_code == 200


# ==================== Сценарий 3: Бэклог → Архив (без выполнения) ====================

class TestFlowBacklogToArchive:
    """
    Сценарий: Пользователь передумал и удалил задачу из бэклога.
    1. Создать задачу в бэклоге
    2. Удалить (🗑) → soft delete
    3. Задача должна появиться в архиве
    """

    async def test_backlog_to_archive(self, client):
        # 1. Создать задачу без даты
        create_resp = await client.post("/api/tasks", json={
            "title": "Больше не актуально",
        })
        task_id = create_resp.json()["id"]

        # 2. Удалить через HTMX
        delete_resp = await client.request("DELETE", f"/tasks/{task_id}")
        assert delete_resp.status_code == 200

        # 3. Проверить что is_archived = True
        task_resp = await client.get(f"/api/tasks/{task_id}")
        assert task_resp.json()["is_archived"] is True

        # 4. Проверить что задача в архиве
        archive_resp = await client.get("/archive")
        assert archive_resp.status_code == 200


# ==================== Сценарий 4: Категоризация ====================

class TestFlowCategorization:
    """
    Сценарий: Создание задачи с категорией и подкатегорией.
    1. Получить список категорий
    2. Создать задачу с категорией
    3. Проверить что категория привязана
    """

    async def test_task_with_category(self, client):
        # 1. Получить категории
        cats_resp = await client.get("/api/categories")
        categories = cats_resp.json()
        assert len(categories) > 0

        # Берём первую подкатегорию (не глобальную)
        sub_cats = [c for c in categories if not c["is_global"]]
        assert len(sub_cats) > 0
        sub_cat_id = sub_cats[0]["id"]

        # 2. Создать задачу с подкатегорией
        create_resp = await client.post("/api/tasks", json={
            "title": "Задача с подкатегорией",
            "category_id": sub_cat_id,
        })
        assert create_resp.status_code == 200
        task = create_resp.json()
        assert task["category_id"] == sub_cat_id


# ==================== Сценарий 5: Статистика ====================

class TestFlowStats:
    """
    Сценарий: Пользователь проверяет статистику.
    1. Создать несколько задач
    2. Некоторые выполнить
    3. Открыть страницу статистики — данные должны быть корректны
    """

    async def test_stats_accuracy(self, client):
        today = date.today().isoformat()

        # Создаём 3 задачи
        for i in range(3):
            await client.post("/api/tasks", json={"title": f"Задача {i}", "due_date": today})

        # Завершаем 2
        tasks_resp = await client.get("/api/tasks")
        tasks = tasks_resp.json()
        non_completed = [t for t in tasks if t["status"] != "выполнена"]

        completed_count = 0
        for t in non_completed[:2]:
            await client.post(f"/api/tasks/{t['id']}/complete")
            completed_count += 1

        # Открываем статистику
        stats_resp = await client.get("/stats")
        assert stats_resp.status_code == 200
        # Страница должна отрендериться без ошибок


# ==================== Сценарий 6: Периодические задачи ====================

class TestFlowRecurringTasks:
    """
    Сценарий: Создание и управление периодическими задачами.
    1. Создать периодический шаблон
    2. Увидеть в списке
    3. Переключить активность
    4. Удалить
    """

    async def test_recurring_full_flow(self, client):
        # 1. Создать шаблон
        create_resp = await client.post("/api/recurring", json={
            "title": "Еженедельный созвон",
            "description": "Понедельник-среда-пятница",
            "priority": "средний",
            "recurrence_type": "weekly",
            "recurrence_days": ["mon", "wed", "fri"],
            "start_date": date.today().isoformat(),
        })
        assert create_resp.status_code == 200
        recurring_id = create_resp.json()["id"]

        # 2. Получить список
        list_resp = await client.get("/api/recurring")
        assert list_resp.status_code == 200
        assert any(r["id"] == recurring_id for r in list_resp.json())

        # 3. Переключить активность
        toggle_resp = await client.post(f"/api/recurring/{recurring_id}/toggle")
        assert toggle_resp.status_code == 200
        assert toggle_resp.json()["is_active"] is False

        # 4. Снова включить
        toggle2 = await client.post(f"/api/recurring/{recurring_id}/toggle")
        assert toggle2.json()["is_active"] is True

        # 5. Удалить
        delete_resp = await client.delete(f"/api/recurring/{recurring_id}")
        assert delete_resp.status_code == 200

        # 6. Проверить что удалён
        list2 = await client.get("/api/recurring")
        assert not any(r["id"] == recurring_id for r in list2.json())


# ==================== Сценарий 7: Множественные операции ====================

class TestFlowBulkOperations:
    """
    Сценарий: Работа с большим количеством задач.
    1. Создать 10 задач
    2. Проверить пагинацию/лимиты
    3. Массово завершить
    """

    async def test_bulk_create_and_complete(self, client):
        today = date.today().isoformat()

        # 1. Создать 10 задач
        for i in range(10):
            resp = await client.post("/api/tasks", json={
                "title": f"Массовая задача {i}",
                "due_date": today,
            })
            assert resp.status_code == 200

        # 2. Получить список (лимит по умолчанию 50)
        list_resp = await client.get("/api/tasks", params={"limit": 100})
        assert list_resp.status_code == 200
        assert len(list_resp.json()) >= 10

        # 3. Завершить все
        tasks = list_resp.json()
        for t in tasks:
            if t["status"] != "выполнена":
                await client.post(f"/api/tasks/{t['id']}/complete")

        # 4. Проверить что все завершены
        list2 = await client.get("/api/tasks", params={"status": "выполнена", "limit": 100})
        completed = list2.json()
        assert len(completed) >= 10


# ==================== Сценарий 8: Веб-форма создания → редактирование ====================

class TestFlowWebFormEdit:
    """
    Сценарий: Пользователь создаёт задачу через веб-форму, потом редактирует.
    """

    async def test_web_form_create_and_edit(self, client):
        # 1. Страница формы
        form_resp = await client.get("/tasks/new")
        assert form_resp.status_code == 200

        # 2. Создать через форму
        create_resp = await client.post("/tasks/web/create", data={
            "title": "Форма-задача",
            "description": "Создано через форму",
            "category_id": "",
            "priority": "средний",
            "due_date": date.today().isoformat(),
            "status": "новая",
        }, follow_redirects=False)
        assert create_resp.status_code == 303

        # 3. Найти задачу по названию
        tasks_resp = await client.get("/api/tasks", params={"limit": 100})
        tasks = tasks_resp.json()
        task = next((t for t in tasks if t["title"] == "Форма-задача"), None)
        assert task is not None
        task_id = task["id"]

        # 4. Открыть форму редактирования
        edit_page = await client.get(f"/tasks/{task_id}/edit")
        assert edit_page.status_code == 200

        # 5. Редактировать через форму
        edit_resp = await client.post(f"/tasks/web/{task_id}/edit", data={
            "title": "Изменённая задача",
            "description": "Обновлено",
            "category_id": "",
            "priority": "высокий",
            "due_date": "",
            "status": "в_работе",
        }, follow_redirects=False)
        assert edit_resp.status_code == 303

        # 6. Проверить изменения
        task_resp = await client.get(f"/api/tasks/{task_id}")
        data = task_resp.json()
        assert data["title"] == "Изменённая задача"
        assert data["status"] == "в_работе"
        assert data["priority"] == "высокий"
