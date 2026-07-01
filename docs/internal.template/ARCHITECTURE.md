# Архитектура

**Обновлено:** 04.06.2026 · Локальный файл (не в git)

---

## Схема

```
Браузер ──HTTPS──► NPM ──► task_planner:8000 (FastAPI + HTMX)
Telegram ──► telegram_bot (aiogram) ──► та же planner.db
```

**Точки входа:** `app/main.py` (веб + APScheduler), `run_bot.py` (бот + пуш 09:00)

**Сервисы:** `rollover_service`, `recurring_service`, `recurring_schedule`, `daily_plan_service`, `calendar_sync_service`, `ai_service`, `backup_service`

---

## Веб-слой

```
app/main.py
  └── app/web/pages.py          # APIRouter: include_router всех доменов
        ├── auth_routes.py      # /login, /logout
        └── routes/
              dashboard.py      # /
              tasks.py          # /tasks/*
              backlog.py        # /backlog/*
              calendar.py       # /calendar, decline API
              categories.py     # /categories/*
              archive.py        # /archive/*
              stats.py          # /stats, AI partials
              recurring.py      # /recurring
              shopping.py       # /shopping
              career.py         # export, milestones
              finance.py        # /finance, Chart.js
        deps.py                 # templates, stats insights, period calendar
```

- Импорт в `main.py`: `from app.web.pages import router` — **не менять**
- Статистика: см. `docs/internal/STATS.md` — owner `deps.py`, UI `stats.html` + `partials/stats_chart.html`
- `get_today_stats`: re-export из `pages` → `deps`
- Backlog **только** в `routes/backlog.py`

---

## AI и бот

- **Категоризация:** DeepSeek → fallback Groq
- **Vision / чеки:** Gemini → fallback Groq
- **Голос:** Groq Whisper
- **Фото:** Pillow до 2000px JPEG 85%
- **Финансы в БД:** траты `+`, доходы `-`
- **Утренний план:** 09:00 → `build_daily_plan_text`
- **Контекст категорий:** `config/categories_context.md`

---

## Миграции (Alembic)

- `init_db`: `create_all` + `alembic upgrade head`
- Ревизии: `alembic/versions/` (`001_baseline`, `002_legacy_schema`)
- **Новые колонки** — только новая ревизия, не `ALTER` в `database.py`

---

## Бэкап SQLite

- `backup_service.py`: `sqlite3.Connection.backup()`
- Файлы: `backups/planner_YYYY-MM-DD_HH-MM.db`, ротация 7 дней

---

## Frontend (HTMX)

- Планирование с дашборда: `HX-Target: task-{id}` → ответ `📅 ДД.ММ`
- `/finance`: Chart.js 4.4.1 CDN при наличии расходов за месяц
