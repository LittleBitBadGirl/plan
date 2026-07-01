<!-- Импорт в GBrain: slug projects/plan. put_page 2026-06-04: Invalid OpenAI embed key -->

---
type: project
title: Task Planner (plan) — индекс
owner: Vera
tags:
  - planner
  - personal-tools
  - fastapi
updated: 2026-06-04
---

# Task Planner & Finance Manager (plan)

Персональный планировщик задач и финансов Веры: веб (HTMX) + Telegram-бот + SQLite. Репозиторий: `LittleBitBadGirl/plan` (локально: `СLI/plan`).

## Назначение

- Задачи на день / бэклог / архив / recurring
- Финансы (траты/доходы, Chart.js по категориям)
- Календарь (Yandex CalDAV + Google iCal → блоки на дашборде)
- Статистика продуктивности и карьерный капитал
- Telegram: текст/голос → задача с AI-категоризацией; фото чека → транзакция; утренний план 09:00

## Стек

FastAPI · SQLAlchemy 2 async · SQLite (`planner.db`) · Alembic · HTMX · Alpine.js · Tailwind · Chart.js · aiogram 3 · APScheduler · DeepSeek (категории) · Gemini (vision/чеки) · Groq (Whisper, fallback) · Docker Compose

## Архитектура (высокоуровнево)

```
Браузер ──HTTPS──► NPM (443) ──► task_planner:8000 (FastAPI + HTMX)
Telegram ──► telegram_bot (aiogram, run_bot.py) ──► та же planner.db
```

**Точки входа:** `app/main.py` (веб + APScheduler), `run_bot.py` (бот + push 09:00)

**Ключевые сервисы:** `rollover_service`, `recurring_service`, `recurring_schedule` (единое расписание periodic), `daily_plan_service`, `calendar_sync_service`, `ai_service`, `backup_service`, `calendar_filter_service`, `calendar_ignore_service`

### Веб-слой

- `app/main.py` импортирует `from app.web.pages import router` — **не менять**
- `app/web/pages.py` — сборка APIRouter (~35 строк), include всех доменов
- `app/web/deps.py` — templates, статистика (`get_history_data`, `get_productivity_insights`, `get_avg_completed_per_day`, subtasks map), period calendar
- `app/web/routes/`: dashboard, tasks, backlog, calendar, categories, archive, stats, recurring, shopping, career, finance
- `app/web/auth_routes.py` — `/login`, `/logout`

### API

Префиксы в `app/api/`: tasks, categories, recurring, habits, period, ai, screenshot

### БД и миграции

- `init_db`: `create_all` + `alembic upgrade head`
- Ревизии: `001_baseline`, `002_legacy_schema`
- **Новые колонки только через Alembic**, не ALTER в `database.py`
- `planner.db` не в git; volume в Docker

### Подзадачи (июнь 2026, main)

Модель: `Task.parent_task_id` + relationship `subtasks`.

- Отдельный partial `partials/subtask_row.html` — единый HTMX для списка и панели подзадач
- `POST /tasks/{sub_id}/complete-subtask` — подзадача: `status=выполнена`, `is_archived=False` (зачёркнута, видна)
- Гибридный прогресс дня: X/Y на карточках; weekly stats — родитель как одна единица
- Автозакрытие родителя когда все подзадачи выполнены
- `repair_archived_subtasks` в deps — починка legacy archived subtasks

Последние коммиты (04.06.2026): `81b86f9` feat subtasks, `3f3dec1` прогресс дня + закрытые сегодня, `1076e1c` fix strike-through + X/Y

Спека: `docs/superpowers/specs/2026-06-04-subtasks-design.md`

## Страницы веб-UI

| URL | Назначение |
|-----|------------|
| `/` | Дашборд: задачи, встречи, recurring, нагрузка |
| `/tasks` | CRUD задач |
| `/backlog` | Отложенные → в план |
| `/calendar` | По датам |
| `/recurring` | Шаблоны periodic |
| `/finance` | Траты/доходы + Chart.js doughnut |
| `/stats` | KPI, график нед/мес/год, Δ неделя, зависшие, карьера |
| `/shopping` | Список покупок |
| `/archive` | Завершённые и удалённые |
| `/login` | Cookie 30 дней (production) |

## AI и бот

- Категоризация: DeepSeek → fallback Groq; контекст `config/categories_context.md`
- Vision/чеки: Gemini → Groq; фото Pillow до 2000px JPEG 85%
- Голос: Groq Whisper
- **Финансы в БД:** траты `+`, доходы `-` в `transactions`
- Теги в задачах: только люди/проекты (#Антон, #Сбер)
- Inline mode `@bot` — не планируется

## Фоновые задачи (APScheduler в main.py)

| Время | Задача |
|-------|--------|
| 00:01 | Бэкап SQLite (`backup_service`, ротация 7 дней) |
| 00:05 | `generate_recurring_tasks` |
| 00:10 | `rollover_overdue_tasks` |
| */30 мин | `sync_calendar_events` (если CalDAV/Google включены) |

При старте: init_db, seed categories, backup, rollover, calendar sync (timeout 45s).

## Статистика `/stats` (owner: deps.py)

- KPI: выполнено (всё время), в работе (не архив, ≠ выполнена)
- График: week/month — календарные дни + выходные подсветка; year — по месяцам
- Темп и Δ неделя — только рабочие дни (пн–пт)
- «Зависшие»: `в_работе`, корневая, не архив, `created_at <= now-7d`
- Дашборд: `build_daily_load_warning` при `remaining > 8` — тот же avg по рабочим дням
- Тесты: `tests/test_stats_workdays.py`

## Деплой (production)

- **VPS:** `91.186.217.66`, путь `/opt/projects/planner`
- **Docker:** `task_planner` + `telegram_bot`, сеть `npm_default` (external), порт 8000 **не** публикуется наружу
- **NPM:** Forward Host `task_planner:8000`, SSL Let's Encrypt
- **Проверка:** `docker exec task_planner curl -s http://127.0.0.1:8000/api/health`
- Деплой: `git pull` → `docker compose down && docker compose up -d --build` → `docker image prune -f`

### Auth

- Один секрет `API_TOKEN` в `.env` — пароль веб/API (значения **не** хранить в brain)
- Cookie 30 дней после `/login`; альтернатива `/?token=...` один раз
- API: `Authorization: Bearer` или `X-API-Token`
- Локально: пустой `API_TOKEN` = dev без пароля
- Бот **не** зависит от `API_TOKEN`

### Env (имена только, без значений)

`API_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `CALENDAR_SYNC_ENABLED`, `GOOGLE_CALENDAR_SYNC_ENABLED`, CalDAV/Google secrets — см. `.env.example` и локальный `CONTEXT.md` (не в git)

## Документация в репо

| Путь | Содержание |
|------|------------|
| `README.md` | Быстрый старт, страницы, env |
| `docs/PLAN.md` | Статус и следующий шаг |
| `docs/CHANGELOG.md` | История (обновлять при релизах) |
| `docs/DEPLOY.md` | VPS, NPM, auth |
| `docs/ENDPOINTS.md` | Маршруты API и веб |
| `docs/internal.template/` | Шаблон для `docs/internal/` (ARCHITECTURE, AGENTS, STATS, CALENDAR) — **в .gitignore**, копировать локально |

## Тесты и стандарты для агентов

- `pytest tests/` — целевое состояние 99+ passed; не трогает prod `planner.db`
- Перед пушем: pytest
- **Не расширять scope** без запроса; не коммитить `.env`
- Веб-логику добавлять в `routes/*` + `deps.py`, не раздувать `pages.py`
- Один шаг за раз

## Текущий статус (04.06.2026)

**На main:** auth+NPM, Alembic, calendar sync, stats overhaul (`f07872c` и позже), Chart.js finance, subtasks UI+progress (коммиты до `1076e1c`).

**Следующий шаг по docs/PLAN.md:** deploy main на VPS (статистика + дашборд + subtasks) — чеклист в DEPLOY.md.

## Отменено / не планируем

- PDF milestone export (достаточно `/api/career/export` .md)
- Топ-3 на дашборде
- n8n (удалён, бот напрямую)
- Напоминания бота 15/30 мин
- Inline mode бота

## Связанные пути (локально Vera)

- Workspace: `/Users/vera/Desktop/личные_доки/СLI/plan`
- GitHub: `https://github.com/LittleBitBadGirl/plan.git`
