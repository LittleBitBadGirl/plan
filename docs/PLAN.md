# План проекта

**Обновлено:** 04.06.2026 · **Тесты:** 99/99

---

## Текущий статус

- Веб-роуты разрезаны: `pages.py` + `routes/*.py` + `deps.py`
- Chart.js на `/finance` — готово
- Auth + NPM — готово
- Alembic + бэкап SQLite — готово
- Календарь Yandex + Google — готово
- Карьерный капитал: экспорт `.md` на `/stats` — достаточно
- **Статистика** (`f07872c`): рабочие дни, layout, Δ неделя, зависшие задачи — на main

---

## Следующий шаг

**Deploy VPS** — выкатить `f07872c` на сервер (статистика + дашборд). Чеклист: [DEPLOY.md](./DEPLOY.md)

1. `git pull` на `/opt/projects/planner`
2. `.env`: `API_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID`
3. `docker compose up -d --build` + `docker image prune -f`
4. NPM: Forward `task_planner:8000`, SSL
5. Проверить `/login`, дашборд, `/finance`, `/stats` (график, Δ неделя)
6. `docker exec task_planner curl -s http://127.0.0.1:8000/api/health`

---

## Стандарты

- Перед пушем: `pytest tests/`
- Deploy: `docker compose up -d --build` + `docker image prune -f`
- Один шаг за раз, не расширять scope без запроса

---

## Для AI-агентов

Промпты и архитектурные детали — в `docs/internal/` (локально, не в git).  
См. `docs/internal/AGENTS.md` и `docs/internal/STATS.md` после первого клона (`cp -r docs/internal.template docs/internal`).
