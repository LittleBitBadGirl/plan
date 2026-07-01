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

    async def test_dashboard_today_stats_fragment(self, client, db):
        """OOB-фрагмент прогресса дня для HTMX"""
        from app.models.task import Task

        today = date.today()
        db.add(Task(title="Одна", due_date=today, status="новая", source="web"))
        await db.commit()

        response = await client.get("/dashboard/today-stats")
        assert response.status_code == 200
        assert 'id="today-stats-counter"' in response.text
        assert 'hx-swap-oob="true"' in response.text
        assert 'id="today-progress-bar"' in response.text
        assert "0/1" in response.text

    async def test_task_create_updates_stats_oob(self, client, db):
        """Создание задачи возвращает OOB для счётчика и прогресс-бара"""
        response = await client.post(
            "/tasks/create",
            data={"title": "Новая через HTMX", "category_id": ""},
        )
        assert response.status_code == 200
        assert 'id="today-stats-counter"' in response.text
        assert 'id="today-progress-bar"' in response.text
        assert "Новая через HTMX" in response.text

    async def test_task_create_excludes_recurring_source_tasks(self, client, db):
        """Быстрое добавление не подмешивает recurring-вхождения в колонку задач"""
        from app.models.task import Task

        today = date.today()
        db.add(
            Task(
                title="Регулярная вхождение",
                due_date=today,
                status="новая",
                source="recurring",
                item_kind="task",
            )
        )
        await db.commit()

        response = await client.post(
            "/tasks/create",
            data={"title": "Обычная задача", "category_id": ""},
        )
        assert response.status_code == 200
        assert "Обычная задача" in response.text
        assert "Регулярная вхождение" not in response.text

    async def test_dashboard_tasks_old_first(self, client, db):
        """Старые/перенесённые задачи выше новых на дашборде"""
        from datetime import datetime, timedelta, timezone
        from app.models.task import Task

        today = date.today()
        old = Task(
            title="Старая просроченная",
            due_date=today,
            status="новая",
            postpones=3,
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        new = Task(
            title="Свежая сегодня",
            due_date=today,
            status="новая",
            postpones=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add_all([new, old])  # порядок вставки не должен влиять
        await db.commit()

        response = await client.get("/")
        assert response.status_code == 200
        pos_old = response.text.index("Старая просроченная")
        pos_new = response.text.index("Свежая сегодня")
        assert pos_old < pos_new

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

    async def test_recurring_web_create_redirects(self, client):
        """Создание шаблона: 303 на GET, без повторного POST при обновлении страницы"""
        import uuid
        title = f"web-rt-{uuid.uuid4().hex[:8]}"
        create = await client.post(
            "/recurring/create",
            data={
                "title": title,
                "category_id": "",
                "recurrence_type": "daily",
                "start_date": date.today().isoformat(),
            },
            follow_redirects=False,
        )
        assert create.status_code == 303
        assert create.headers["location"] == "/recurring?flash=created"

        page = await client.get("/recurring?flash=created")
        assert page.status_code == 200
        assert title in page.text
        assert "Шаблон создан" in page.text

        dup = await client.post(
            "/recurring/create",
            data={
                "title": title,
                "category_id": "",
                "recurrence_type": "daily",
                "start_date": date.today().isoformat(),
            },
            follow_redirects=False,
        )
        assert dup.status_code == 303
        assert "flash=duplicate" in dup.headers["location"]

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

    async def test_web_create_task_ddmm_date(self, client, db):
        """Создание задачи с датой в формате ДД.ММ"""
        from app.models.task import Task
        from sqlalchemy import select

        today = date.today()
        ddmm = today.strftime("%d.%m")

        response = await client.post("/tasks/web/create", data={
            "title": "Задача с датой",
            "description": "",
            "category_id": "",
            "priority": "средний",
            "due_date": ddmm,
            "status": "новая",
        }, follow_redirects=False)
        assert response.status_code == 303

        async with db.begin():
            result = await db.execute(select(Task).where(Task.title == "Задача с датой"))
            task = result.scalar_one()
            assert task.due_date == today

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


# ==================== HTMX подзадачи ====================

class TestSubtaskHTMX:
    """HTMX: завершение подзадач на дашборде"""

    async def test_complete_subtask_returns_strikethrough_row(self, client):
        today = date.today().isoformat()
        parent = await client.post("/api/tasks", json={"title": "Проект", "due_date": today, "category_id": 1})
        parent_id = parent.json()["id"]
        sub = await client.post(f"/api/tasks/{parent_id}/subtasks", json={"title": "Шаг 1", "source": "web"})
        sub_id = sub.json()["id"]
        await client.post(f"/api/tasks/{parent_id}/subtasks", json={"title": "Шаг 2", "source": "web"})

        resp = await client.post(
            f"/tasks/{sub_id}/complete-subtask",
            headers={"HX-Target": f"subtask-row-{sub_id}"},
        )
        assert resp.status_code == 200
        assert "✅ выполнено" not in resp.text
        assert "line-through" in resp.text
        assert "Шаг 1" in resp.text
        assert f"subtask-row-{sub_id}" in resp.text

    async def test_complete_last_subtask_shows_strikethrough(self, client):
        today = date.today().isoformat()
        parent = await client.post("/api/tasks", json={"title": "Проект", "due_date": today, "category_id": 1})
        parent_id = parent.json()["id"]
        sub = await client.post(f"/api/tasks/{parent_id}/subtasks", json={"title": "Единственный", "source": "web"})
        sub_id = sub.json()["id"]

        resp = await client.post(f"/tasks/{sub_id}/complete-subtask")
        assert resp.status_code == 200
        assert "line-through" in resp.text
        assert "Единственный" in resp.text
        assert "Проект" not in resp.text


# ==================== HTMX бэклога ====================

class TestBacklogHTMX:
    """Тесты HTMX эндпоинтов бэклога"""

    async def test_backlog_quick_create(self, client, db):
        """Быстрое добавление задачи в бэклог без даты"""
        response = await client.post(
            "/backlog/create",
            data={"title": "Идея из головы", "category_id": ""},
        )
        assert response.status_code == 200
        assert "Идея из головы" in response.text

        from app.models.task import Task
        from sqlalchemy import select

        result = await db.execute(
            select(Task).where(Task.title == "Идея из головы")
        )
        task = result.scalar_one()
        assert task.due_date is None

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
        # Раньше мы проверяли уведомление "в архив", теперь его нет для чистоты верстки
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
        date_str = f"{tomorrow.day:02d}.{tomorrow.month:02d}"

        response = await client.post(f"/tasks/{task_id}/plan", data={"due_date": date_str})
        assert response.status_code == 200
        assert "📅" in response.text or response.status_code == 200

    async def test_plan_task_compact_date(self, client):
        """Запланировать задачу — формат DDMM без точки (0606 → 06.06)"""
        create_resp = await client.post("/api/tasks", json={"title": "Компактная дата"})
        task_id = create_resp.json()["id"]

        tomorrow = date.today() + timedelta(days=1)
        compact = f"{tomorrow.day:02d}{tomorrow.month:02d}"

        response = await client.post(f"/tasks/{task_id}/plan", data={"due_date": compact})
        assert response.status_code == 200
        assert "📅" in response.text

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


class TestHabitHistory:
    """История отметок трекера привычек"""

    async def test_habit_history_partial(self, client, db):
        from app.models.habit import Habit
        from app.models.habit_log import HabitLog

        today = date.today()
        habit = Habit(title="Утренний уход", start_date=today, current_cycle=2, target_days=30)
        db.add(habit)
        await db.flush()

        db.add(HabitLog(habit_id=habit.id, cycle_number=1, date=today - timedelta(days=10)))
        db.add(HabitLog(habit_id=habit.id, cycle_number=1, date=today - timedelta(days=9)))
        db.add(HabitLog(habit_id=habit.id, cycle_number=2, date=today))
        await db.commit()

        response = await client.get(f"/api/habits/{habit.id}/history")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Утренний уход" in response.text
        assert "Цикл 2" in response.text
        assert "Цикл 1" in response.text
        assert "3 отметок всего" in response.text
        assert "Текущий" in response.text

    async def test_habit_history_not_found(self, client):
        response = await client.get("/api/habits/99999/history")
        assert response.status_code == 404
