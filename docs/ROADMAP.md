# План развития Planner

## Текущий статус (04.06.2026)

- **Тесты:** 95/95 — `pytest tests/`
- **Инфра:** auth, NPM, `recurring_schedule.py` — см. [SUBAGENT_PLAN.md](./SUBAGENT_PLAN.md)
- **Календарь:** Yandex CalDAV + Google iCal — Phase 0–3 готовы
- **Карьерный капитал:** экспорт `.md` на `/stats` — **достаточно, расширять не планируем**

---

## Сделано (Q2 инфраструктура)

- [x] n8n убран, Telegram через `run_bot.py`
- [x] `API_TOKEN` + `/login` + cookie
- [x] NPM, закрытый порт 8000
- [x] `recurring_schedule.py`
- [x] `TELEGRAM_ADMIN_CHAT_ID`
- [x] Тесты и документация

---

## В очереди (см. [SUBAGENT_PLAN.md](./SUBAGENT_PLAN.md))

| # | Шаг | Приоритет |
|---|-----|-----------|
| 0 | Commit + Deploy VPS | Сначала |
| 1 | Разрезать `pages.py` | Высокий |
| 2 | Chart.js — pie расходов на `/finance` | После шага 1 |
| 3 | Alembic / бэкап SQLite | Опционально |

---

## Отменено / вне scope

- ~~Экспорт PDF, milestone-фильтр~~ — хватает `/api/career/export` (.md)
- ~~Топ-3 достижения на дашборде~~
- ~~Излишек месяца → financial_goals~~
- ~~Напоминания бота 15/30 мин~~
- ~~Inline mode `@bot`~~

---

## Стандарты

- `pytest tests/` перед пушем
- Deploy: `docker compose up -d --build` + `docker image prune -f`
- Доки: `HANDOVER.md`, `AUTH.md`, `NPM_PROXY.md`
