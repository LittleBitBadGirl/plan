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
| POST | `/api/events/{id}/status` | Снять отметку: брони нет, день похода стирается |
| GET | `/events/{id}/visit-form` | Мини-форма похода (день и время сеанса) — открывается нажатием «Иду» |
| POST | `/api/events/{id}/visit` | «Иду»: сохранить бронь. Без дня сеанса или с днём вне мероприятия не принимается — форма вернётся с текстом ошибки |
| GET | `/events/{id}/visit.ics` | Файл календаря на поход: событие на 2 часа + напоминание за сутки (`VALARM`), 404 если брони нет |
| POST | `/api/events/{id}/delete` | Удалить |

Решение по мероприятию одно — «иду». «Иду» это действие, а не оценка: нажатие спрашивает день и время сеанса (билет куплен), из них собирается событие календаря. В Google Календарь ведут две дороги: ссылка `google_calendar_url` (открывается заполненная форма, доступы не нужны) и файл `.ics` (внутри `VALARM` — напоминание за сутки, ссылка Google его не переносит). Форма добавления/правки статус не меняет: из неё получался бы «иду» без дня, а из такого состояния календарь не собрать.

Статус и бронь меняются и с дашборда: ответ на изменение — корневой блок (доска или блок недели) плюс второй блок через OOB. Ответ на бронь состоит только из OOB: слот мини-формы (`#ev-visit-slot`) пустеет, доска и блок недели обновляются молча. Слот один на страницу, поэтому id не повторяются, даже когда мероприятие видно и в панели дня, и в карточке.

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
| GET | `/api/stats/flow?period=week\|month\|year` | Блок «Поток задач» (`partials/stats_flow.html`) |
| GET | `/api/ai/prepare-analysis` | AI-анализ продуктивности (partial) |
| POST | `/api/shopping/*` | CRUD списка покупок |

Полный список маршрутов — `app/web/routes/` и `app/api/`.
