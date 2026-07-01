# Эндпоинты

Auth: если задан `API_TOKEN` — cookie после `/login` или заголовок `Authorization: Bearer …` / `X-API-Token`.

---

## Система

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/api/health` | Health check |
| GET | `/login` | Форма входа |
| POST | `/login` | Установка cookie |
| GET | `/logout` | Выход |

---

## Веб-страницы

| URL | Страница |
|-----|----------|
| `/` | Дашборд (задачи, встречи, регулярные) |
| `/tasks`, `/tasks/new`, `/tasks/{id}/edit` | Задачи |
| `/backlog` | Бэклог |
| `/calendar` | Календарь |
| `/categories` | Категории |
| `/archive` | Архив |
| `/stats` | KPI, динамика (HTMX нед/мес/год), инсайты, карьерный капитал, AI-анализ |
| `/tasks?status=в_работе` | Фильтр по статусу (в т.ч. ссылка «зависшие» со `/stats`) |
| `/recurring` | Периодические |
| `/shopping` | Список покупок |
| `/finance` | Финансы + диаграмма расходов |

---

## REST API — задачи

Префикс `/api/tasks`

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/api/tasks` | Список (фильтры: date, status) |
| POST | `/api/tasks` | Создать |
| GET | `/api/tasks/{id}` | Одна задача |
| PUT | `/api/tasks/{id}` | Обновить |
| DELETE | `/api/tasks/{id}` | Удалить |
| POST | `/api/tasks/{id}/complete` | Завершить |
| POST | `/api/tasks/{id}/subtasks` | Подзадача |
| POST | `/api/tasks/{id}/archive` | В архив |
| GET | `/api/tasks/archive` | Архив |
| GET | `/api/tasks/date/{YYYY-MM-DD}` | На дату |

---

## REST API — категории, recurring, habits

| Префикс | Основное |
|---------|----------|
| `/api/categories` | CRUD категорий |
| `/api/recurring` | CRUD периодических, toggle, complete, for-date |
| `/api/habits` | Привычки: create, toggle, archive, next-cycle |
| `/api/period/toggle` | Трекер периода |

---

## REST API — AI и карьера

| Метод | URL | Описание |
|-------|-----|----------|
| POST | `/api/ai/categorize` | AI-категоризация |
| POST | `/api/ai/feedback` | Обратная связь |
| POST | `/api/ai/save-report` | Сохранить отчёт |
| GET | `/api/ai/load-analysis` | Загрузить анализ |
| GET | `/api/ai/stats` | AI-статистика |
| GET | `/api/career/export` | Экспорт карьеры (.md) |
| POST | `/api/screenshot` | Скриншот календаря |

---

## HTMX partials (основные)

| Метод | URL | Описание |
|-------|-----|----------|
| POST | `/tasks/{id}/plan` | Запланировать на дату |
| POST | `/tasks/{id}/complete` | Завершить (partial) |
| POST | `/api/calendar/{id}/decline` | «Не пойду» на встречу |
| POST | `/api/transactions/{id}/category` | Категория транзакции |
| GET | `/api/stats/chart?period=week\|month\|year` | Partial графика (`partials/stats_chart.html`) |
| GET | `/api/ai/prepare-analysis` | AI-анализ продуктивности (partial) |
| POST | `/api/shopping/*` | CRUD списка покупок |

Полный список маршрутов — `app/web/routes/` и `app/api/`.
