"""
E2E тесты для API задач (CRUD, фильтрация, archive).
"""
import pytest
from datetime import date, timedelta

pytestmark = pytest.mark.asyncio


# ==================== Создание задач ====================

class TestCreateTask:
    """Тесты создания задач через API"""

    async def test_create_task_minimal(self, client):
        """Создание задачи с минимальными данными"""
        response = await client.post("/api/tasks", json={
            "title": "Тестовая задача",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Тестовая задача"
        assert data["id"] is not None
        assert data["status"] == "новая"
        assert data["is_archived"] is False

    async def test_create_task_full(self, client):
        """Создание задачи со всеми полями"""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        response = await client.post("/api/tasks", json={
            "title": "Полная задача",
            "description": "Подробное описание",
            "priority": "высокий",
            "due_date": tomorrow,
            "source": "web",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Полная задача"
        assert data["description"] == "Подробное описание"
        assert data["priority"] == "высокий"
        assert data["due_date"] == tomorrow

    async def test_create_task_empty_title(self, client):
        """Создание задачи с пустым названием должно вернуть ошибку"""
        response = await client.post("/api/tasks", json={"title": ""})
        assert response.status_code == 422

    async def test_create_task_no_title(self, client):
        """Создание задачи без title — 422"""
        response = await client.post("/api/tasks", json={"description": "без названия"})
        assert response.status_code == 422


# ==================== Получение задач ====================

class TestListTasks:
    """Тесты получения списка задач"""

    async def test_list_tasks_empty(self, client):
        """Список задач пуст"""
        response = await client.get("/api/tasks")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    async def test_list_tasks_with_data(self, client):
        """Список задач содержит данные"""
        # Создаём задачу
        await client.post("/api/tasks", json={"title": "Задача 1", "due_date": date.today().isoformat()})
        await client.post("/api/tasks", json={"title": "Задача 2", "due_date": date.today().isoformat()})

        response = await client.get("/api/tasks")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        titles = [t["title"] for t in data]
        assert "Задача 1" in titles
        assert "Задача 2" in titles

    async def test_filter_by_status(self, client):
        """Фильтрация по статусу"""
        today = date.today().isoformat()
        await client.post("/api/tasks", json={"title": "Новая", "status": "новая", "due_date": today})
        await client.post("/api/tasks", json={"title": "В работе", "status": "в_работе", "due_date": today})

        response = await client.get("/api/tasks", params={"status": "новая"})
        assert response.status_code == 200
        data = response.json()
        assert all(t["status"] == "новая" for t in data)

    async def test_filter_by_date_range(self, client):
        """Фильтрация по диапазону дат"""
        tomorrow = date.today() + timedelta(days=1)
        next_week = date.today() + timedelta(days=7)

        await client.post("/api/tasks", json={"title": "Завтра", "due_date": tomorrow.isoformat()})
        await client.post("/api/tasks", json={"title": "Через неделю", "due_date": next_week.isoformat()})

        response = await client.get("/api/tasks", params={
            "from_date": tomorrow.isoformat(),
            "to_date": tomorrow.isoformat(),
        })
        assert response.status_code == 200
        data = response.json()
        assert any(t["title"] == "Завтра" for t in data)


# ==================== Получение одной задачи ====================

class TestGetTask:
    """Тесты получения задачи по ID"""

    async def test_get_existing_task(self, client):
        """Получение существующей задачи"""
        create_resp = await client.post("/api/tasks", json={"title": "Найти меня"})
        task_id = create_resp.json()["id"]

        response = await client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json()["title"] == "Найти меня"

    async def test_get_nonexistent_task(self, client):
        """Получение несуществующей задачи — 404"""
        response = await client.get("/api/tasks/99999")
        assert response.status_code == 404


# ==================== Обновление задач ====================

class TestUpdateTask:
    """Тесты обновления задач"""

    async def test_update_task_title(self, client):
        """Обновление заголовка"""
        create_resp = await client.post("/api/tasks", json={"title": "Старый"})
        task_id = create_resp.json()["id"]

        response = await client.put(f"/api/tasks/{task_id}", json={"title": "Новый"})
        assert response.status_code == 200
        assert response.json()["title"] == "Новый"

    async def test_update_task_status(self, client):
        """Обновление статуса"""
        create_resp = await client.post("/api/tasks", json={"title": "Тест"})
        task_id = create_resp.json()["id"]

        response = await client.put(f"/api/tasks/{task_id}", json={"status": "в_работе"})
        assert response.status_code == 200
        assert response.json()["status"] == "в_работе"

    async def test_update_nonexistent_task(self, client):
        """Обновление несуществующей задачи — 404"""
        response = await client.put("/api/tasks/99999", json={"title": "Нет"})
        assert response.status_code == 404

    async def test_update_partial(self, client):
        """Частичное обновление (то exclude_unset)"""
        create_resp = await client.post("/api/tasks", json={
            "title": "Частичное обновление",
            "description": "Описание",
            "priority": "низкий",
        })
        task_id = create_resp.json()["id"]

        response = await client.put(f"/api/tasks/{task_id}", json={"priority": "высокий"})
        assert response.status_code == 200
        data = response.json()
        assert data["priority"] == "высокий"
        assert data["title"] == "Частичное обновление"  # не изменился
        assert data["description"] == "Описание"  # не изменился


# ==================== Удаление задач ====================

class TestDeleteTask:
    """Тесты удаления задач (soft delete → архив)"""

    async def test_soft_delete_task(self, client):
        """Мягкое удаление — is_archived = True"""
        create_resp = await client.post("/api/tasks", json={"title": "Удали меня"})
        task_id = create_resp.json()["id"]

        response = await client.delete(f"/api/tasks/{task_id}")
        assert response.status_code == 200

        # Проверяем что задача заархивирована
        get_resp = await client.get(f"/api/tasks/{task_id}")
        assert get_resp.json()["is_archived"] is True

    async def test_deleted_task_not_in_list(self, client):
        """Удалённая задача не появляется в списке"""
        create_resp = await client.post("/api/tasks", json={"title": "Скрыть"})
        task_id = create_resp.json()["id"]
        await client.delete(f"/api/tasks/{task_id}")

        response = await client.get("/api/tasks")
        data = response.json()
        assert not any(t["id"] == task_id for t in data)

    async def test_delete_nonexistent_task(self, client):
        """Удаление несуществующей задачи — 404"""
        response = await client.delete("/api/tasks/99999")
        assert response.status_code == 404


# ==================== Завершение задач ====================

class TestCompleteTask:
    """Тесты завершения задач"""

    async def test_complete_task(self, client):
        """Завершение задачи"""
        create_resp = await client.post("/api/tasks", json={"title": "Завершить"})
        task_id = create_resp.json()["id"]

        response = await client.post(f"/api/tasks/{task_id}/complete")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "выполнена"
        assert data["completed_at"] is not None

    async def test_complete_nonexistent_task(self, client):
        """Завершение несуществующей задачи — 404"""
        response = await client.post("/api/tasks/99999/complete")
        assert response.status_code == 404


# ==================== Задачи по дате ====================

class TestTasksByDate:
    """Тесты получения задач по конкретной дате"""

    async def test_tasks_by_date_with_tasks(self, client):
        """Задачи на конкретную дату"""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        await client.post("/api/tasks", json={"title": "Завтра 1", "due_date": tomorrow})
        await client.post("/api/tasks", json={"title": "Завтра 2", "due_date": tomorrow})

        response = await client.get(f"/api/tasks/date/{tomorrow}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2

    async def test_tasks_by_date_no_tasks(self, client):
        """Нет задач на дату — пустой список"""
        future = (date.today() + timedelta(days=30)).isoformat()
        response = await client.get(f"/api/tasks/date/{future}")
        assert response.status_code == 200
        assert response.json() == []


# ==================== Подзадачи ====================

class TestSubtasks:
    """Тесты подзадач"""

    async def test_add_subtask(self, client):
        """Добавление подзадачи"""
        parent = await client.post("/api/tasks", json={"title": "Родитель", "category_id": 1})
        parent_id = parent.json()["id"]

        response = await client.post(f"/api/tasks/{parent_id}/subtasks", json={
            "title": "Подзадача",
            "source": "web",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Подзадача"
        assert data["parent_task_id"] == parent_id

    async def test_add_subtask_to_nonexistent(self, client):
        """Подзадача к несуществующей задаче — 404"""
        response = await client.post("/api/tasks/99999/subtasks", json={"title": "Нет", "source": "web"})
        assert response.status_code == 404


# ==================== Архив ====================

class TestArchive:
    """Тесты архива"""

    async def test_get_archive(self, client):
        """Получение архива"""
        # Создаём и архивируем
        create_resp = await client.post("/api/tasks", json={"title": "В архив"})
        task_id = create_resp.json()["id"]
        await client.delete(f"/api/tasks/{task_id}")

        response = await client.get("/api/tasks/archive")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert any(t["id"] == task_id for t in data)
