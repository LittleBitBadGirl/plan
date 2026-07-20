# Portfolio Analyzer — Handoff для агентов

**Дата:** 2026-07-20  
**Статус:** код T1–T6 готов локально (uncommitted на момент написания)  
**Owner:** Vera  

> Этот документ — **единая точка входа** для любого нового агента.  
> Spec: `docs/superpowers/specs/2026-07-19-portfolio-analyzer-spec.md`  
> Plan (чеклист задач): `docs/superpowers/plans/2026-07-19-portfolio-analyzer.md`

---

## Что сделано (T1–T6)

| Task | Суть | Ключевые файлы |
|------|------|----------------|
| T1 | Schema, 4 portfolios, seed в migration | `alembic/versions/008_portfolio_analyzer.py`, `app/models/portfolio.py`, `app/models/investment.py` |
| T2 | Import service, dedup, instrument match | `app/services/portfolio_service.py`, `app/services/instrument_normalize.py` |
| T3 | API + backward-compat `/api/goals/{id}/analytics` | `app/web/routes/portfolio.py` |
| T4 | UI `/portfolio`, tabs, cashflow, drill-down | `app/web/templates/portfolio.html`, `app/web/static/js/portfolio-analytics.js` |
| T5 | Finance cleanup — ссылки вместо modal | `app/web/templates/finance.html`, `app/web/routes/finance.py` |
| T6 | API docs | `docs/ENDPOINTS.md` |

**Тесты:** `tests/test_portfolio_*.py` — green. Full suite: 179+ passed, 4 pre-existing failures (не portfolio).

---

## Архитектура (кратко)

```
Телефон (брокерские приложения)
    │  экспорт / zip / xls / скрин → файлы на сервер или в inbox Hermes
    ▼
Серверный Hermes (отдельный проект, VPS)
    │  парсит отчёт → JSON по контракту
    │  POST /api/portfolios/{id}/import  (Bearer API_TOKEN)
    ▼
Planner (этот репо, /opt/projects/planner)
    │  SQLite planner.db
    ▼
Браузер: /portfolio (аналитика) + /finance (балансы + ссылка «Аналитика →»)
```

**Два Hermes:**

| | Локальный Hermes | Серверный Hermes |
|---|------------------|------------------|
| Где | Mac, dev | VPS, рядом с planner |
| Зачем | Отладка парсеров, curl на localhost | **Боевой pipeline** — отчёты с телефона |
| Planner URL | `http://127.0.0.1:8000` | `https://planner.ВАШ-ДОМЕН` |
| Skill | опционально | **обязателен** — `docs/hermes/portfolio-import-skill.md` |

---

## Счета (portfolio map)

| id | slug | tab | Брокер | Договор | Import cadence |
|----|------|-----|--------|---------|----------------|
| 1 | `iis` | ИИС | Совкомбанк | 9248208 | monthly БО zip |
| 2 | `podushka` | Подушка | БКС | 1226101/21-л | monthly `.xls` |
| 3 | `broker-1` | Брокерский 1 | Совкомбанк | 1149213 | monthly БО zip |
| 4 | `broker-2` | Брокерский 2 | T-Bank | 2132867011 | quarterly + bulk once |

Цель «Автомобиль» — `portfolio_goals` на portfolio 3, не имя счёта.

---

## Документы по теме

| Файл | Для кого |
|------|----------|
| `docs/portfolio-hermes-contract.md` | JSON schema + curl |
| `docs/portfolio-sovcombank-report-format.md` | Парсер ИИС / Брокерский 1 |
| `docs/portfolio-bcs-report-format.md` | Парсер Подушка (БКС) |
| `docs/portfolio-tbank-report-format.md` | Парсер Брокерский 2 |
| `docs/hermes/portfolio-import-skill.md` | **Skill для серверного Hermes** |
| `docs/DEPLOY.md` | Деплой planner + auth |
| `docs/ENDPOINTS.md` | Все URL |

Fixtures: `tests/fixtures/sample_import.json`, `sample_sovcombank_import.json`

---

## Пошаговый rollout (порядок важен)

### Шаг 1 — Коммит и push planner (Vera или planner-агент)

**Зачем:** migration `008` и весь код должны попасть в git до деплоя.

**Действие:** commit всех portfolio-файлов → push `main`.

**Результат:** на VPS после `git pull` появится `alembic/versions/008_portfolio_analyzer.py`.

---

### Шаг 2 — Деплой planner на VPS

**Зачем:** поднять новый код и UI `/portfolio`.

**Действие:** по `docs/DEPLOY.md`:

```bash
cd /opt/projects/planner
git pull origin main
docker compose down && docker compose up -d --build
```

**Миграция:** **ручной alembic НЕ нужен.** При старте контейнера `init_db()` → `alembic upgrade head` автоматически.  
Migration `008` также **seed'ит** 4 portfolios и цель «Автомобиль», если таблица пустая.

**Результат:** контейнер `task_planner` healthy, `/portfolio` открывается.

---

### Шаг 3 — Post-deploy verify (Hermes-агент или Vera)

**Зачем:** убедиться, что migration применилась на prod DB.

**Действие:**

```bash
docker exec task_planner python -c "
import sqlite3
c=sqlite3.connect('/app/planner.db')
print('alembic:', c.execute('SELECT version_num FROM alembic_version').fetchone())
print('portfolios:', c.execute('SELECT id,slug FROM portfolios ORDER BY id').fetchall())
"
curl -s -H \"Authorization: Bearer \$API_TOKEN\" http://127.0.0.1:8000/api/portfolios
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/portfolio
```

**Ожидаемо:** `alembic: ('008_portfolio_analyzer',)`, 4 portfolios, HTTP 200.

**Если portfolios пусто** (редкий edge case): `docker exec task_planner python scripts/seed_portfolios.py`

---

### Шаг 4 — Установить skill на **серверный Hermes**

**Зачем:** агент Hermes на VPS должен знать, как парсить отчёты с телефона и слать import.

**Действие:** скопировать `docs/hermes/portfolio-import-skill.md` в репозиторий Hermes:

```
hermes/.cursor/skills/portfolio-import/SKILL.md
```

(или personal skill на машине, где крутится серверный Hermes)

**Результат:** при работе с broker-файлами Hermes-агент автоматически следует pipeline: parse → JSON → POST.

---

### Шаг 5 — Реализовать парсеры в серверном Hermes

**Зачем:** отчёты приходят с телефона в форматах брокера, не JSON.

**Действие (Hermes-агент):**

1. Inbox для файлов с телефона (Telegram bot / папка / email — как устроено в Hermes).
2. По типу файла выбрать parser doc (sovcombank / bcs / tbank).
3. Собрать JSON по `docs/portfolio-hermes-contract.md`.
4. POST на prod planner с `API_TOKEN` из `.env` Hermes (`PLANNER_URL`, `PLANNER_API_TOKEN`).

**Результат:** после import на `/portfolio?tab=iis` видны баланс, позиции, выплаты TRNFP и т.д.

**Cadence:**

| Portfolio | Когда |
|-----------|-------|
| 1, 3 | monthly после БО zip с телефона |
| 2 | monthly BCS xls |
| 4 | bulk once + quarterly |

---

### Шаг 6 — Manual QA (planner-агент + Vera)

**Чеклист:**

- [ ] `/finance` — транзакции, Зимовка, сбережения из investment_flows
- [ ] `/finance` — «Аналитика →» ведёт на `/portfolio?tab=iis` (и остальные slug)
- [ ] `/portfolio` — 4 tabs, KPI, cashflow table, year tabs
- [ ] Drill-down TRNFP на ИИС
- [ ] Цель «Автомобиль» на broker-1
- [ ] Повторный import → 409, без дублей flows

---

### Шаг 7 — Post-v1 (когда есть real reports)

**Зачем:** подогнать aliases, ПИФ подушки, bond maturity под реальные файлы.

**Действие:** Vera даёт свежие отчёты → Hermes-агент уточняет parser → planner-агент при необходимости правит `portfolio_service.py` / fixtures.

---

## Жёсткие ограничения

- Не коммитить/пушить без явной просьбы Vera (кроме шага 1 когда она попросит)
- Не ломать `/finance` transaction logic
- `Reflected Table()` в `finance.py` — не трогать
- `pytest tests/` — не ухудшать

## Verify (локально)

```bash
/tmp/plan-test-venv/bin/pytest tests/test_portfolio_page.py tests/test_portfolio_api.py tests/test_portfolio_import.py -q
/tmp/plan-test-venv/bin/pytest tests/ -q
```

## Verify (prod import smoke)

```bash
curl -X POST "https://ДОМЕН/api/portfolios/1/import" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_import.json
```
