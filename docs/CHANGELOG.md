# История изменений

**Обновлено:** 04.06.2026 · **Тесты:** 99/99 (`pytest tests/`)

---

## Июнь 2026

| Область | Что сделано |
|---------|-------------|
| **Безопасность** | `API_TOKEN`, middleware, `/login` + cookie 30 дней, `/logout` |
| **NPM** | Порт 8000 не публикуется наружу; доступ только через прокси |
| **n8n** | Удалён; бот — `run_bot.py` / Docker `bot` |
| **Recurring** | Единый модуль `app/services/recurring_schedule.py` |
| **Веб-роуты** | `pages.py` (~35 строк) + `app/web/routes/*.py` + `deps.py` |
| **Финансы UI** | Chart.js doughnut на `/finance` |
| **Telegram** | `TELEGRAM_ADMIN_CHAT_ID` — утренний план 09:00 |
| **Миграции** | Alembic (`001_baseline`, `002_legacy_schema`) |
| **Бэкап** | `sqlite3.Connection.backup()`, ротация 7 дней |
| **Календарь** | Yandex CalDAV + Google iCal, блоки на дашборде |

---

## Фоновые задачи (APScheduler)

| Время | Задача |
|-------|--------|
| 00:01 | Бэкап БД |
| 00:05 | Генерация recurring → `Task` |
| 00:10 | Rollover просроченных |
| */30 мин | Синк календаря (если включён) |

---

## Отменено (не планируем)

- PDF/milestone-экспорт — хватает `/api/career/export` (.md)
- Топ-3 на дашборде
- Излишек месяца → financial_goals
- Напоминания бота 15/30 мин
- Inline mode `@bot`
