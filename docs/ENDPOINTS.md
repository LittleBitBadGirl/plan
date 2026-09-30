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
| `/portfolio` | Портфель: tabs (ИИС, Подушка, Брокерский 1/2), KPI, cashflow, состав |

---

## REST API — портфель

Auth: `Authorization: Bearer $API_TOKEN` для import.

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/api/portfolios` | Список портфелей (id, name, slug, type, legacy_goal_id) |
| GET | `/api/portfolios/{id}/analytics` | Аналитика: snapshots, flows, monthly_cashflow, summary |
| GET | `/api/portfolios/{id}/composition` | Последний состав + `closed[]` по sale/redemption (нет цены покупки → прибыль = полученные деньги) |
| GET | `/api/portfolios/{id}/payments?instrument=&year=` | Drill-down выплат по инструменту |
| POST | `/api/portfolios/{id}/import` | Hermes JSON import (409 при дубликате report_date) |
| GET | `/api/goals/{goal_id}/analytics` | Backward-compat alias → portfolio по legacy_goal_id |

Контракт import: `docs/portfolio-hermes-contract.md`, fixture: `tests/fixtures/sample_import.json`.

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
| `/api/habits` | Привычки: create, toggle, archive, next-cycle. Отметка принимается только внутри окна текущего цикла, иначе 409 |
| `/api/period/toggle` | Трекер периода |

---

## Мероприятия (страница `/events`)

Мероприятие — отдельная сущность, не задача и не встреча из календаря: у него может быть одна дата или диапазон (выставка идёт несколько дней).

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/events` | Страница: календарь месяца, панель дня, ближайшие и прошедшие |
| GET | `/events/board?month=YYYY-MM&day=YYYY-MM-DD` | Доска (месяц + выбранный день), ответ для htmx |
| GET | `/events/new-form` | Пустая форма (ручное добавление) |
| GET | `/events/{id}/form` | Форма правки с заполненными полями |
| POST | `/api/events/preview` | Прочитать ссылку: название, описание, картинка, место, даты (OpenGraph + schema.org) |
| POST | `/api/events/create` | Создать (поля + своя картинка) |
| POST | `/api/events/{id}/update` | Обновить |
| POST | `/api/events/{id}/status` | Статус: `going` («иду») / `not_going` («не иду») / `none` — снять отметку |
| POST | `/api/events/{id}/delete` | Удалить |

Статус меняется и с дашборда: в ответе приходят две OOB-вставки — доска (`partials/events_board.html`) и блок недели (`partials/events_week_block.html`), поэтому витрина и блок обновляются вместе.

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
