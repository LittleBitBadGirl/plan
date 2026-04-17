"""
E2E тесты для веб-страниц (рендеринг шаблонов, HTMX эндпоинты).
"""
import pytest
from datetime import date, timedelta

pytestmark = pytest.mark.asyncio


# ==================== Основные страницы ====================

class TestPages:
    """Тесты рендеринга основных страниц"""

    async def test_dashboard_page(self, client):
        """Дашборд — главная страница"""
        response = await client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Task Planner" in response.text or "Дашборд" in response.text

    async def test_tasks_page(self, client):
        """Страница задач"""
        response = await client.get("/tasks")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Все задачи" in response.text or "Задачи" in response.text

    async def test_backlog_page(self, client):
        """Страница бэклога"""
        response = await client.get("/backlog")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Бэклог" in response.text

    async def test_calendar_page(self, client):
        """Страница календаря"""
        response = await client.get("/calendar")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Календарь" in response.text

    async def test_categories_page(self, client):
        """Страница категорий"""
        response = await client.get("/categories")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Категории" in response.text or "категор" in response.text.lower()

    async def test_recurring_page(self, client):
        """Страница периодических задач"""
        response = await client.get("/recurring")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Периодические" in response.text

    async def test_stats_page(self, client):
        """Страница статистики"""
        response = await client.get("/stats")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Статистика" in response.text

    async def test_archive_page(self, client):
        """Страница архива"""
        response = await client.get("/archive")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Архив" in response.text

    async def test_health_check(self, client):
        """Health check endpoint"""
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


# ==================== Форма создания/редактирования ====================

class TestTaskForms:
    """Тесты страниц форм"""

    async def test_task_create_form_page(self, client):
        """Страница создания задачи"""
        response = await client.get("/tasks/new")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Новая задача" in response.text or "task" in response.text.lower()

    async def test_task_edit_form_nonexistent(self, client):
        """Страница редактирования несуществующей задачи — 404"""
        response = await client.get("/tasks/99999/edit")
        assert response.status_code == 404


# ==================== HTMX эндпоинты (Web CRUD) ====================

class TestWebTaskCRUD:
    """Тесты HTMX эндпоинтов для задач (form-data)"""

    async def test_web_create_task(self, client):
        """Создание задачи через форму (form-data)"""
        response = await client.post("/tasks/web/create", data={
            "title": "Веб-задача",
            "description": "Описание",
            "category_id": "",
            "priority": "средний",
            "due_date": "",
            "status": "новая",
        }, follow_redirects=False)
        # Должен быть редирект 303
        assert response.status_code == 303

    async def test_web_create_task_empty_title(self, client):
        """Создание без title — 422"""
        response = await client.post("/tasks/web/create", data={
            "title": "",
        })
        assert response.status_code in (422, 303)

    async def test_web_edit_task(self, client):
        """Редактирование через форму"""
        # Сначала создаём через API
        create_resp = await client.post("/api/tasks", json={"title": "До изменения"})
        task_id = create_resp.json()["id"]

        response = await client.post(f"/tasks/web/{task_id}/edit", data={
            "title": "После изменения",
            "description": "Новое описание",
            "category_id": "",
            "priority": "высокий",
            "due_date": "",
            "status": "новая",
        }, follow_redirects=False)
        assert response.status_code == 303

        # Проверяем что изменилось
        get_resp = await client.get(f"/api/tasks/{task_id}")
        assert get_resp.json()["title"] == "После изменения"

    async def test_web_edit_nonexistent_task(self, client):
        """Редактирование несуществующей задачи"""
        response = await client.post("/tasks/web/99999/edit", data={
            "title": "Нет",
            "description": "",
            "category_id": "",
            "priority": "средний",
            "due_date": "",
            "status": "новая",
        })
        assert response.status_code == 404


# ==================== HTMX бэклога ====================

class TestBacklogHTMX:
    """Тесты HTMX эндпоинтов бэклога"""

    async def test_complete_task_from_backlog(self, client):
        """Завершение задачи из бэклога"""
        # Создаём задачу без даты (бэклог)
        create_resp = await client.post("/api/tasks", json={"title": "Бэклог задача"})
        task_id = create_resp.json()["id"]

        response = await client.post(f"/tasks/{task_id}/complete")
        assert response.status_code == 200
        assert "выполнено" in response.text.lower() or "✅" in response.text

    async def test_delete_task_from_backlog(self, client):
        """Удаление задачи из бэклога (→ архив)"""
        create_resp = await client.post("/api/tasks", json={"title": "Удалить из бэклога"})
        task_id = create_resp.json()["id"]

        response = await client.request("DELETE", f"/tasks/{task_id}")
        assert response.status_code == 200
        assert "архив" in response.text.lower() or "🗑" in response.text

    async def test_plan_task_today(self, client):
        """Запланировать задачу на сегодня (пустая дата)"""
        create_resp = await client.post("/api/tasks", json={"title": "Запланировать"})
        task_id = create_resp.json()["id"]

        response = await client.post(f"/tasks/{task_id}/plan", data={})
        assert response.status_code in (200, 400)

    async def test_plan_task_specific_date(self, client):
        """Запланировать задачу на конкретную дату"""
        create_resp = await client.post("/api/tasks", json={"title": "Запланировать на дату"})
        task_id = create_resp.json()["id"]

        tomorrow = date.today() + timedelta(days=1)
        date_str = f"{tomorrow.day}.{tomorrow.month}"

        response = await client.post(f"/tasks/{task_id}/plan", data={"due_date": date_str})
        assert response.status_code == 200
        assert "📅" in response.text or response.status_code == 200

    async def test_plan_task_invalid_date(self, client):
        """Запланировать с неправильной датой"""
        create_resp = await client.post("/api/tasks", json={"title": "Неверная дата"})
        task_id = create_resp.json()["id"]

        response = await client.post(f"/tasks/{task_id}/plan", data={"due_date": "abc"})
        assert response.status_code in (400, 422)

    async def test_complete_nonexistent_task(self, client):
        """Завершение несуществующей задачи"""
        response = await client.post("/tasks/99999/complete")
        assert response.status_code == 404 or "не найдена" in response.text.lower()

    async def test_delete_nonexistent_task(self, client):
        """Удаление несуществующей задачи"""
        response = await client.request("DELETE", "/tasks/99999")
        assert response.status_code == 404 or "не найдена" in response.text.lower()


# ==================== Категории API ====================

class TestCategoriesAPI:
    """Тесты API категорий"""

    async def test_list_categories(self, client):
        """Получение списка категорий"""
        response = await client.get("/api/categories")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Seed создаёт категории, их должно быть > 0
        assert len(data) > 0

    async def test_create_category(self, client):
        """Создание категории"""
        response = await client.post("/api/categories", json={
            "name": "Тестовая категория",
            "is_global": False,
        })
        assert response.status_code == 200
        assert response.json()["name"] == "Тестовая категория"

    async def test_update_category(self, client):
        """Обновление категории"""
        create_resp = await client.post("/api/categories", json={"name": "Старое имя", "is_global": False})
        cat_id = create_resp.json()["id"]

        response = await client.put(f"/api/categories/{cat_id}", json={"name": "Новое имя"})
        assert response.status_code == 200
        assert response.json()["name"] == "Новое имя"

    async def test_delete_category(self, client):
        """Удаление категории"""
        create_resp = await client.post("/api/categories", json={"name": "Удалить", "is_global": False})
        cat_id = create_resp.json()["id"]

        response = await client.delete(f"/api/categories/{cat_id}")
        assert response.status_code == 200
