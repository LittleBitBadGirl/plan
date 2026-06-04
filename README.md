# Task Planner & Finance Manager (AI-Powered)

Персональный планировщик задач и финансов: веб (HTMX) + Telegram-бот + SQLite.

---

## Как пользоваться

### Веб-интерфейс

| Страница | URL | Что делает |
|----------|-----|------------|
| Дашборд | `/` | Задачи на сегодня, встречи, регулярные |
| Задачи | `/tasks` | Список, создание, редактирование |
| Бэклог | `/backlog` | Отложенные → в план |
| Календарь | `/calendar` | Просмотр по датам |
| Периодические | `/recurring` | Шаблоны повторяющихся задач |
| Финансы | `/finance` | Траты/доходы, диаграмма по категориям |
| Статистика | `/stats` | Нагрузка, карьерный капитал, экспорт `.md` |
| Покупки | `/shopping` | Список покупок |
| Архив | `/archive` | Завершённые и удалённые |

**Вход (production):** `/login` → cookie на 30 дней. Подробнее: [docs/DEPLOY.md](docs/DEPLOY.md).

### Telegram-бот

- Текст / голос → задача с AI-категоризацией
- Фото чека → транзакция
- Утренний план в **09:00** (если задан `TELEGRAM_ADMIN_CHAT_ID`)

---

## Быстрый старт

```bash
git clone https://github.com/LittleBitBadGirl/plan.git
cd plan
cp .env.example .env   # заполнить токены
```

**Docker (VPS):**

```bash
docker compose up -d --build
```

**Локально:**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh              # веб :8000
python run_bot.py     # бот в отдельном терминале
```

**Вход (если задан `API_TOKEN`):** http://localhost:8000/login

---

## Документация

| Файл | Зачем |
|------|-------|
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Что изменилось в проекте |
| [docs/PLAN.md](docs/PLAN.md) | План и следующий шаг |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Деплой, auth, NPM |
| [docs/ENDPOINTS.md](docs/ENDPOINTS.md) | API и маршруты |

Техническая документация (архитектура, календарь, промпты для AI) — `docs/internal/` (локально, в `.gitignore`). Шаблон: `docs/internal.template/` → `cp -r docs/internal.template docs/internal`.

---

## Стек

FastAPI · SQLAlchemy 2 · SQLite · HTMX · Alpine.js · Tailwind · Chart.js · aiogram 3 · DeepSeek · Gemini · Groq · Docker

---

## Переменные окружения

| Переменная | Назначение |
|------------|------------|
| `API_TOKEN` | Пароль веб/API (пустой = dev без пароля) |
| `TELEGRAM_BOT_TOKEN` | Бот |
| `TELEGRAM_ADMIN_CHAT_ID` | Утренний план 09:00 |
| `DEEPSEEK_API_KEY` | Категории задач |
| `GEMINI_API_KEY` | Vision / чеки |
| `GROQ_API_KEY` | Whisper, fallback |
| `CALENDAR_SYNC_ENABLED` | Yandex CalDAV |
| `GOOGLE_CALENDAR_SYNC_ENABLED` | Google iCal |

Полный шаблон: `.env.example`.

---

## Тесты

```bash
pytest tests/   # 99 passed
```

---

## Деплой на VPS

```bash
cd /opt/projects/planner
git pull origin main
docker compose down && docker compose up -d --build
docker image prune -f
```

Детали: [docs/DEPLOY.md](docs/DEPLOY.md)
