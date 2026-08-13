# Planner — Project Knowledge Base

Для людей и AI-агентов (Cursor, Hermes, Claude). Читать перед любой работой.

---

## Что это

Персональный task-планировщик с Telegram-ботом. FastAPI + SQLite + HTMX + aiogram.
Деплой: Docker Compose на VPS (91.186.217.66), reverse proxy через Nginx Proxy Manager.

---

## Карта файлов (не читай всё подряд — иди по задаче)

```
plan/
├── app/
│   ├── main.py              # FastAPI entry point + lifespan + APScheduler
│   ├── config.py            # Pydantic Settings (.env)
│   ├── auth.py              # JWT + cookie auth
│   ├── api/                 # JSON API (Telegram bot, внешние вызовы)
│   │   ├── tasks.py         # /api/tasks — CRUD
│   │   ├── habits.py        # /api/habits
│   │   ├── recurring.py     # /api/recurring
│   │   ├── period.py        # /api/period/rollover
│   │   ├── categories.py    # /api/categories
│   │   ├── ai.py            # /api/ai — AI-классификация, план
│   │   └── screenshot.py    # /api/screenshots
│   ├── web/                 # Веб-интерфейс (Jinja2 + HTMX)
│   │   ├── pages.py         # Сборка APIRouter (НЕ пиши роуты сюда)
│   │   ├── deps.py          # get_today_stats, шаблонные хелперы
│   │   ├── auth_routes.py   # /login, /logout
│   │   └── routes/          # Роуты по доменам — пиши здесь
│   │       ├── dashboard.py # / (дашборд)
│   │       ├── tasks.py     # /tasks/*
│   │       ├── finance.py   # /finance (Chart.js)
│   │       ├── stats.py     # /stats (AI-аналитика, тепловая карта)
│   │       ├── portfolio.py # /portfolio
│   │       ├── shopping.py  # /shopping
│   │       ├── calendar.py  # /calendar
│   │       ├── backlog.py   # /backlog
│   │       ├── archive.py   # /archive
│   │       ├── recurring.py # /recurring
│   │       ├── categories.py
│   │       ├── reading.py
│   │       └── career.py
│   │   ├── templates/       # Jinja2: base.html, dashboard.html, finance.html...
│   │   │   └── partials/    # HTMX-компоненты: task_card, subtask_*, macros...
│   │   └── static/          # CSS (Tailwind), JS (Chart.js), manifest.json
│   ├── models/              # SQLAlchemy ORM (21 модель)
│   ├── services/            # Бизнес-логика (19 сервисов)
│   ├── bot/                 # Telegram bot (aiogram)
│   │   ├── handlers.py      # Обработчики сообщений, фото, callback
│   │   └── task_logic.py    # Логика обработки задач из бота
│   └── middleware/          # API auth middleware
├── run_bot.py               # Entry point бота + утренний пуш 09:00
├── alembic/                 # Миграции (9 ревизий: 001..009)
├── tests/                   # pytest (30 файлов, 95+ тестов)
├── docs/
│   ├── internal/            # Архитектура, календарь, статистика (локально)
│   └── DEPLOY.md, ENDPOINTS.md, CHANGELOG.md, PLAN.md
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env                     # НЕ в git
```

---

## Быстрая навигация: куда идти за задачей

| Задача | Файлы |
|--------|-------|
| Добавить веб-страницу/эндпоинт | `app/web/routes/<domain>.py` + шаблон в `templates/` |
| Новая бизнес-логика | `app/services/<new_service>.py` |
| Новая таблица/колонка | Модель в `app/models/` + Alembic-ревизия |
| Telegram-бот: новая команда | `app/bot/handlers.py` |
| AI: смена модели/промпта | `app/services/ai_service.py` |
| Финансы: отчёты/импорт | `app/models/finance.py`, `routes/finance.py`, `templates/finance.html` |
| Календари: фильтры/синк | `app/services/calendar_sync_service.py` |
| Портфель: новый брокер | `app/services/portfolio_service.py` + модель `portfolio.py` |
| Статистика/дашборд | `app/web/deps.py` + `routes/dashboard.py` + `routes/stats.py` |
| Шаблоны/UI | `app/web/templates/` (страницы) или `partials/` (HTMX-компоненты) |

---

## Стек

| Слой | Технология |
|------|-----------|
| Backend | FastAPI 0.104+ (Python 3.12) |
| ORM | SQLAlchemy 2.0 async + aiosqlite |
| Миграции | Alembic |
| Фронтенд | Jinja2 + HTMX + Tailwind CSS (без React/Vue) |
| Бот | aiogram 3.4+ |
| AI (текст) | DeepSeek (основной) → Groq (Llama 3.3 70B, резерв) |
| AI (vision) | Gemini (основной) → Groq (Llama 4 Scout, резерв) |
| AI (голос) | Groq Whisper |
| Календари | Yandex CalDAV + Google iCal |
| База | SQLite (`planner.db`) |
| Фоновые задачи | APScheduler |
| Деплой | Docker Compose, Nginx Proxy Manager |

---

## Ключевые конвенции

### Код
- **Язык:** русский для комментариев и сообщений пользователю; английский для кода и имён переменных
- **Не расширяй scope** без явного запроса. Делай ровно то, что попросили.
- **Новые колонки:** только Alembic-ревизия → `alembic revision -m "..."`, не ALTER вручную
- **Роуты:** только в `app/web/routes/<domain>.py`. `pages.py` — только сборка router'ов.
- **Импорт в main.py:** `from app.web.pages import router` — не менять

### Финансы
- **Расход = положительное число (+), доход = отрицательное (-)** в `transactions.amount`
- UI: Chart.js 4.4.1, график `category_summary`

### Бот
- Новый пользователь: категоризация через историю → DeepSeek/Groq
- Фото: Pillow до 2000px JPEG 85% → Gemini/Groq vision
- Теги: только люди и проекты (#Антон, #Сбер), не хэштеги на всё

### Календари
- Тянем 4 включённых под-календаря; «не обязательные» — не тянем
- Сб/Вс из рабочих календарей — не импортируем
- Кнопка «Не пойду»: разовое — скрыть один раз; повторяющееся — игнор всей серии

---

## Безопасность (нарушать нельзя)

| Правило | Почему |
|---------|--------|
| `planner.db` НЕ в git | volume в Docker, `.gitignore` |
| `.env` НЕ в git | API-ключи, токены, пароли |
| `API_TOKEN` обязателен на VPS | Без него API открыто |
| Порт 8000 только внутри Docker | Снаружи через NPM |
| `docker image prune -f` после билда | Не забивать диск |
| Новые колонки — только Alembic | Не ломать продакшн |

---

## Перед пушем

```bash
pytest tests/ -q
```

Упало → чини. НЕ пушить сломанные тесты.

---

## Git-воркфлоу (обязательно)

- **Пушь напрямую в `main`.** НЕ создавай feature-ветки (`cursor/...`, `fix/...` и т.п.), если Вера явно не попросила отдельную ветку.
- Порядок: `git checkout main` → `git pull` → правки → `git add -A` → `git commit -m "..."` → `git push origin main`.
- Если оказался не на `main` — сначала `git checkout main`.
- Перед началом работы всегда `git pull origin main`, чтобы не конфликтовать.

---

## Деплой (VPS)

```
Сервер: root@91.186.217.66
Проект: /opt/projects/planner
Деплой: docker compose up -d --build
Проверка: curl http://91.186.217.66/login
```

Подробно: `docs/DEPLOY.md`

---

## Документация

| Файл | Что внутри |
|------|-----------|
| `README.md` | Быстрый старт |
| `docs/DEPLOY.md` | Деплой на VPS, NPM, SSL |
| `docs/ENDPOINTS.md` | Все маршруты |
| `docs/CHANGELOG.md` | История изменений |
| `docs/PLAN.md` | Roadmap |
| `docs/internal/ARCHITECTURE.md` | Схема, слои, AI-стек |
| `docs/internal/CALENDAR.md` | Календарная интеграция |
| `docs/internal/STATS.md` | Статистика и AI-аналитика |
| `docs/internal/AGENTS.md` | Старая версия (если этот файл не читается) |

---

## Частые паттерны

### Добавить веб-страницу
1. Создать `app/web/routes/<name>.py` с новым `APIRouter`
2. Зарегистрировать в `app/web/pages.py`
3. Шаблон → `app/web/templates/<name>.html`
4. Если HTMX — частичный ответ через `partials/`

### Добавить модель БД
1. Создать `app/models/<name>.py` (SQLAlchemy)
2. Импортировать в `app/models/__init__.py`
3. `alembic revision --autogenerate -m "add <name>"`
4. `alembic upgrade head`

### Починить баг
1. Найти домен по карте выше
2. Прочитать соответствующий файл (НЕ все .py подряд)
3. Найти тест в `tests/test_<domain>.py`
4. Починить → `pytest tests/test_<domain>.py -q`
5. Если теста нет → добавить
