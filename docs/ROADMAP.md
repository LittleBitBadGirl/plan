# План развития Planner

## Текущий статус (04.06.2026)

- **Тесты:** 99/99 — `pytest tests/`
- **Веб:** `app/web/pages.py` (сборка) + `routes/*.py` + `deps.py` — рефакторинг завершён
- **Финансы:** Chart.js doughnut расходов по категориям на `/finance` — готово
- **Инфра:** auth, NPM, `recurring_schedule.py` — см. [SUBAGENT_PLAN.md](./SUBAGENT_PLAN.md)
- **Календарь:** Yandex CalDAV + Google iCal — Phase 0–3 готовы
- **Карьерный капитал:** экспорт `.md` на `/stats` — **достаточно, расширять не планируем**
- **Следующий шаг субагента:** Deploy VPS (шаг 0) — по необходимости ([SUBAGENT_PLAN.md](./SUBAGENT_PLAN.md))

---

## Сделано (Q2 инфраструктура + UI)

- [x] n8n убран, Telegram через `run_bot.py`
- [x] `API_TOKEN` + `/login` + cookie
- [x] NPM, закрытый порт 8000
- [x] `recurring_schedule.py`
- [x] `TELEGRAM_ADMIN_CHAT_ID`
- [x] Тесты и документация
- [x] Разрезать `app/web/pages.py` → `routes/` + `deps.py`
- [x] Chart.js — круговая диаграмма расходов на `/finance`

---

## В очереди (см. [SUBAGENT_PLAN.md](./SUBAGENT_PLAN.md))

| # | Шаг | Приоритет |
|---|-----|-----------|
| 0 | Deploy VPS (если main не на сервере) | По необходимости |
| ~~1~~ | ~~Разрезать `pages.py`~~ | ✅ Готово |
| ~~2~~ | ~~Chart.js — pie расходов на `/finance`~~ | ✅ Готово |
| ~~3~~ | ~~Alembic / бэкап SQLite~~ | ✅ Готово |

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
- Субагенты: один шаг из `SUBAGENT_PLAN.md`, **не расширять scope**, `pytest tests/` в конце
