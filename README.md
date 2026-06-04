# Task Planner & Finance Manager (AI-Powered)

Персональный планировщик задач и финансов: веб (HTMX) + Telegram-бот + SQLite.

---

## Возможности

### Telegram (aiogram 3)

- AI-категоризация и теги (DeepSeek / Groq)
- Голос → задача (Groq Whisper)
- Фото чеков → транзакции (Gemini / Groq Vision)
- Утренний план в 09:00

### Веб

- Дашборд, бэклог, календарь, периодические, финансы, статистика
- Синк Яндекс CalDAV и Google iCal (опционально)
- Карьерный капитал (AI impacts + экспорт `.md`)

### Безопасность (production)

- Секрет `API_TOKEN` в `.env`
- Вход: `/login` → cookie на 30 дней
- Доступ через Nginx Proxy Manager (HTTPS)

Подробно: [docs/AUTH.md](docs/AUTH.md), [docs/NPM_PROXY.md](docs/NPM_PROXY.md).

---

## Стек

- FastAPI, SQLAlchemy 2, SQLite (WAL)
- HTMX, Alpine.js, Tailwind
- aiogram 3, APScheduler
- DeepSeek, Gemini, Groq
- Docker Compose + NPM

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
./run.sh              # только веб :8000
python run_bot.py     # бот в отдельном терминале
```

**Вход (если задан `API_TOKEN`):** http://localhost:8000/login

---

## Переменные окружения

| Переменная | Назначение |
|------------|------------|
| `API_TOKEN` | Пароль веб/API (пустой = dev без пароля) |
| `TELEGRAM_BOT_TOKEN` | Бот |
| `TELEGRAM_ADMIN_CHAT_ID` | Кому слать план 09:00 |
| `DEEPSEEK_API_KEY` | Категории задач |
| `GEMINI_API_KEY` | Vision / чеки |
| `GROQ_API_KEY` | Whisper, fallback |
| `CALENDAR_SYNC_ENABLED` | Yandex CalDAV |
| `GOOGLE_CALENDAR_SYNC_ENABLED` | Google iCal URL |

Полный шаблон: `.env.example`.

---

## Тесты

```bash
pytest tests/   # 95 passed
```

---

## Документация

| Файл | Содержание |
|------|------------|
| [HANDOVER.md](HANDOVER.md) | Состояние проекта, деплой, команды VPS |
| [docs/ROADMAP.md](docs/ROADMAP.md) | План фич (кратко) |
| [docs/SUBAGENT_PLAN.md](docs/SUBAGENT_PLAN.md) | **Очередь шагов для субагентов** |
| [docs/AUTH.md](docs/AUTH.md) | Cookie, API_TOKEN |
| [docs/NPM_PROXY.md](docs/NPM_PROXY.md) | Nginx Proxy Manager |
| [docs/calendar-yandex-integration.md](docs/calendar-yandex-integration.md) | CalDAV |

---

## Деплой на VPS

```bash
cd /opt/projects/planner
git pull origin main
docker compose down && docker compose up -d --build
docker image prune -f
```

Первый заход: `https://ваш-домен/login`.
