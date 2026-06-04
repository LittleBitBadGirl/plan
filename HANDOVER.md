# Handover: Planner

**Обновлено:** 04.06.2026  
**Статус:** 95/95 тестов (`pytest tests/`). Готово к деплою на VPS.

---

## Что изменилось (июнь 2026)

| Область | Сделано |
|---------|---------|
| **Безопасность** | `API_TOKEN`, middleware, `/login` + cookie 30 дней, `/logout` |
| **NPM** | Порт 8000 не публикуется наружу; доступ только через прокси |
| **n8n** | Удалены упоминания; бот — `run_bot.py` / Docker `bot` |
| **Recurring** | Единый модуль `app/services/recurring_schedule.py` |
| **Telegram** | `TELEGRAM_ADMIN_CHAT_ID` в `.env` (утренний план 09:00) |
| **Тесты** | 95 passed; изоляция БД между кейсами |

Документация: [docs/AUTH.md](docs/AUTH.md), [docs/NPM_PROXY.md](docs/NPM_PROXY.md), [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Архитектура (кратко)

```
Браузер ──HTTPS──► NPM ──► task_planner:8000 (FastAPI + HTMX)
Telegram ──► telegram_bot (aiogram) ──► та же planner.db
```

**Сервисы:** `rollover_service`, `recurring_service`, `recurring_schedule`, `daily_plan_service`, `calendar_sync_service`, `ai_service`, `backup_service`.

**Точки входа:** `app/main.py` (веб + APScheduler), `run_bot.py` (бот + пуш 09:00).

---

## Переменные `.env` (обязательные на VPS)

```env
API_TOKEN=...                    # секрет для веб/API; пустой = без пароля (только dev)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=...       # chat id для утреннего плана
DEEPSEEK_API_KEY=...             # категоризация задач
GEMINI_API_KEY=...               # vision (чеки)
GROQ_API_KEY=...                 # Whisper + fallback
```

Опционально: CalDAV (`YANDEX_*`, `CALENDAR_SYNC_ENABLED`), Google iCal (`GOOGLE_CALENDAR_*`).

Шаблон: `.env.example`.

---

## AI и бот

- **Категоризация:** DeepSeek → fallback Groq.
- **Vision / чеки:** Gemini → fallback Groq (Llama 4 Scout / 3.2 Vision).
- **Голос:** Groq Whisper.
- **Фото:** сжатие Pillow до 2000px JPEG 85% перед API.
- **Финансы в БД:** траты `+`, доходы `-`.
- **Утренний план:** 09:00 → `build_daily_plan_text` (встречи → задачи → регулярные).

---

## Карьерный капитал

- Поля задачи: `impact_notes`, `is_milestone`, `estimated_minutes`, `actual_minutes`.
- Таблица `career_impacts` — AI-отчёты на `/stats`.
- **Экспорт:** `/api/career/export` → Markdown (кнопка «Скачать .md»). PDF и фильтр по `is_milestone` — в ROADMAP.

---

## Frontend (HTMX)

- Частичные ответы без полной перезагрузки.
- Планирование с дашборда: заголовок `HX-Target: task-{id}` → ответ `📅 ДД.ММ`.
- Вход: `/login` (не хранить токен в закладке).

---

## Сервер и деплой

- **IP:** 91.186.217.66
- **Путь:** `/opt/projects/planner/`

```bash
cd /opt/projects/planner
git pull origin main
docker compose down && docker compose up -d --build
docker image prune -f
```

**Проверки:**

```bash
docker exec task_planner curl -s http://127.0.0.1:8000/api/health
docker logs -f telegram_bot
grep API_TOKEN .env   # не светить в чатах
```

**Первый вход после деплоя:** `https://ТВОЙ-ДОМЕН/login` → ключ из `API_TOKEN`.

**NPM:** Forward Host = `task_planner`, port `8000`, Force SSL. См. [docs/NPM_PROXY.md](docs/NPM_PROXY.md).

**БД:** `planner.db` в volume; `sqlite3 planner.db` на хосте.

---

## Фоновые задачи (APScheduler в `main.py`)

| Время | Задача |
|-------|--------|
| 00:01 | Бэкап БД |
| 00:05 | Генерация recurring → `Task` |
| 00:10 | Rollover просроченных |
| */30 мин | Синк календаря (если включён) |

---

## Что делать дальше

Пошаговый план для субагентов: **[docs/SUBAGENT_PLAN.md](docs/SUBAGENT_PLAN.md)**

1. Deploy на VPS (auth уже в коде).
2. Разрезать `pages.py`.
3. Chart.js — pie расходов на `/finance`.

Отменено: PDF/milestone-экспорт, Топ-3 на дашборде, излишек→цели, напоминания бота, Inline mode.
