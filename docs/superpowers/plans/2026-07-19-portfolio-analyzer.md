# Portfolio Analyzer — Implementation Plan

> **For agentic workers:** Read spec first. Parallel tasks marked 🟢 can run simultaneously.  
> **Spec:** `docs/superpowers/specs/2026-07-19-portfolio-analyzer-spec.md`  
> **Hermes contract:** `docs/portfolio-hermes-contract.md`  
> **Sovcombank format:** `docs/portfolio-sovcombank-report-format.md`  
> **BCS (Подушка):** `docs/portfolio-bcs-report-format.md`  
> **T-Bank:** `docs/portfolio-tbank-report-format.md`

**Goal:** Отдельный `/portfolio` с tabs, Excel-таблицей доходов, составом, drill-down, HTTP import от Hermes.

**Architecture:** Новая сущность `portfolios` поверх legacy `financial_goals`. Formalize `investment_*` tables. Hermes → JSON import. UI — вынести и расширить analytics из `finance.html`.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Jinja2, HTMX/Alpine, vanilla JS (как finance modal), pytest

---

## Agent entry point

```
START HERE → docs/superpowers/specs/2026-07-19-portfolio-analyzer-spec.md
PLAN       → docs/superpowers/plans/2026-07-19-portfolio-analyzer.md  (this file)
HERMES     → docs/portfolio-hermes-contract.md
```

**Before coding:** `pytest tests/` must stay green. After your task: run affected tests.

**Do not:** commit without user request; push; change unrelated finance transaction logic.

---

## File map (target)

| File | Agent task | Responsibility |
|------|------------|----------------|
| `alembic/versions/008_portfolio_analyzer.py` | T1 | Schema migration |
| `app/models/portfolio.py` | T1 | Portfolio, PortfolioGoal, Instrument, Position, ImportLog |
| `app/models/investment.py` | T1 | InvestmentSnapshot, InvestmentFlow (ORM) |
| `app/models/__init__.py` | T1 | Register models |
| `app/services/portfolio_service.py` | T2 | Import logic, instrument matching, analytics queries |
| `app/services/instrument_normalize.py` | T2 | Alias resolution TRNFP ↔ Транснефть |
| `app/web/routes/portfolio.py` | T3 | Page + JSON APIs |
| `app/web/templates/portfolio.html` | T4 | Full UI, tabs, tables |
| `app/web/static/js/portfolio-analytics.js` | T4 | Extracted from finance.html + extensions |
| `app/web/templates/base.html` | T4 | Sidebar link |
| `app/web/pages.py` | T3 | Include portfolio router |
| `app/web/routes/finance.py` | T5 | Slim down, alias to portfolio, fix bugs |
| `app/web/templates/finance.html` | T5 | Remove modal, add link |
| `docs/portfolio-hermes-contract.md` | T1 | JSON schema + examples for Hermes |
| `docs/ENDPOINTS.md` | T6 | API docs |
| `tests/test_portfolio_import.py` | T2 | Import idempotency, matching |
| `tests/test_portfolio_api.py` | T3 | Routes, analytics all-years |
| `tests/test_portfolio_page.py` | T4 | SSR smoke |
| `scripts/seed_portfolios.py` | T1 | Seed 4 portfolios from legacy goals |

---

## Dependency graph

```
T1 (Schema + models + Hermes doc)
 ├── T2 (Import service) 🟢 after T1 models exist
 ├── T3 (API routes) 🟢 after T1
 └── T4 (UI) — needs T3 API, can mock initially

T5 (Finance cleanup) — after T3+T4
T6 (Docs + integration test) — last
```

---

## Task T1 — Schema & models 🟢

**Owner hint:** backend / DB agent  
**Files:** migration, models, seed, hermes contract doc

### Steps

- [ ] Create `app/models/portfolio.py` — Portfolio, PortfolioGoal, Instrument, Position, ImportLog
- [ ] Create `app/models/investment.py` — InvestmentSnapshot, InvestmentFlow
- [ ] Alembic `008_portfolio_analyzer.py`:
  - CREATE portfolios, portfolio_goals, instruments, positions, import_log
  - CREATE investment_snapshots, investment_flows IF NOT EXISTS (formalize orphan tables)
  - ADD portfolio_id to investment_* ; backfill from goal_id
- [ ] Seed script: 4 portfolios (ИИС, Подушка, Брокерский 1, Брокerский 2), portfolio_goal «Автомобиль» on id=3
- [ ] Rename financial_goals id=6 name → «Брокерский 1» in seed/migration data step
- [ ] Write `docs/portfolio-hermes-contract.md` — copy JSON from spec + curl examples

### Verify

```bash
alembic upgrade head
python scripts/seed_portfolios.py  # or seed in migration
pytest tests/test_migrations.py -q
```

---

## Task T2 — Import service 🟢

**Owner hint:** backend agent  
**Depends on:** T1 models

### Steps

- [ ] `app/services/portfolio_service.py`:
  - `import_report(portfolio_id, payload) -> ImportResult`
  - Upsert snapshot, positions, flows
  - Instrument match: ticker → aliases JSON → fuzzy name
  - Dedup flows
  - Write import_log
- [ ] `app/services/instrument_normalize.py` — `resolve_instrument(name, ticker)`
- [ ] `tests/test_portfolio_import.py`:
  - Sample JSON from spec
  - Duplicate import skips flows
  - TRNFP + «Транснефть (п)» → same instrument
  - pif_accrual flow type

### Verify

```bash
pytest tests/test_portfolio_import.py -q
```

---

## Task T3 — API & routes 🟢

**Owner hint:** backend agent  
**Depends on:** T1; uses T2 for import endpoint

### Steps

- [ ] `app/web/routes/portfolio.py`:
  - `GET /portfolio` — SSR, active tab from `?tab=slug`
  - `GET /api/portfolios`
  - `GET /api/portfolios/{id}/analytics` — **no 2025 cutoff**, fix summary keys bug
  - `GET /api/portfolios/{id}/composition` — latest positions
  - `GET /api/portfolios/{id}/payments?instrument=&year=`
  - `POST /api/portfolios/{id}/import` — call portfolio_service
- [ ] Register in `app/web/pages.py`
- [ ] Backward compat: `GET /api/goals/{goal_id}/analytics` → resolve portfolio by legacy_goal_id
- [ ] `tests/test_portfolio_api.py`

### Verify

```bash
pytest tests/test_portfolio_api.py -q
curl -H "Authorization: Bearer $API_TOKEN" -X POST .../api/portfolios/1/import -d @fixtures/sample_import.json
```

---

## Task T4 — UI 🟢

**Owner hint:** frontend agent  
**Depends on:** T3 (can develop against mock JSON first)

### Steps

- [ ] `app/web/templates/portfolio.html`:
  - Tab bar: 4 portfolios
  - KPI cards, sparkline (reuse logic from finance modal)
  - Portfolio goal progress (Автомобиль on broker-1)
  - Full-width cashflow table — extract `buildCashflowTable` → `portfolio-analytics.js`
  - Year tabs: all years with data + «Все»
  - Search/filter instrument
  - Composition table
  - Drill-down panel on row click
- [ ] `app/web/static/js/portfolio-analytics.js` — extracted + extended
- [ ] `base.html` — nav item «Портфель» after «Финансы»
- [ ] `tests/test_portfolio_page.py` — GET /portfolio 200, tabs present

### Verify

```bash
pytest tests/test_portfolio_page.py -q
# Manual: open /portfolio, switch tabs, search «транс»
```

---

## Task T5 — Finance cleanup

**Depends on:** T3, T4

### Steps

- [ ] `finance.html` — remove analytics modal JS (~400 lines) or thin wrapper linking to `/portfolio?tab=...`
- [ ] Investment goals grid → compact: balance + link «Аналитика → /portfolio?tab=...»
- [ ] `finance.py` — use portfolios table instead of `INVESTMENT_GOAL_IDS = [1,3,6,7]`
- [ ] Keep savings stats from investment_flows working
- [ ] Other goals (Зимовка) unchanged

### Verify

```bash
pytest tests/test_finance.py -q
```

---

## Task T6 — Docs & E2E

### Steps

- [ ] Update `docs/ENDPOINTS.md` — portfolio endpoints
- [ ] Add `tests/fixtures/sample_import.json` — TRNFP dividend on ИИС + PIF подушка
- [ ] Full suite: `pytest tests/ -q`

---

## Sample test fixture

`tests/fixtures/sample_import.json` — minimal for ИИС (portfolio 1):

```json
{
  "report_date": "2025-07-31",
  "snapshot": {"date": "2025-07-31", "total_balance": 850000},
  "positions": [
    {"ticker": "TRNFP", "name": "Транснефть (п)", "asset_type": "stock", "quantity": 50, "market_value": 72000}
  ],
  "flows": [
    {"date": "2025-07-15", "type": "dividend", "amount": 10200, "instrument": "TRNFP", "description": "Дивиденды TRNFP"}
  ]
}
```

---

## Parallel agent assignment (suggested)

| Agent | Task | First file to open |
|-------|------|-------------------|
| Agent A | T1 Schema | `docs/superpowers/specs/2026-07-19-portfolio-analyzer-spec.md` |
| Agent B | T2 Import | Wait for T1 models, then `app/services/portfolio_service.py` |
| Agent C | T3 API | `app/web/routes/portfolio.py` |
| Agent D | T4 UI | `app/web/templates/finance.html` (copy modal JS) |
| Agent E | T5+T6 | After A–D merge |

---

## Open items (resolve during T1/T2)

- [ ] Vera provides sample Hermes broker report → refine `instruments.aliases` and flow types
- [ ] Confirm ПИФ подушки field names from real report
- [ ] Bond maturity: from report positions or separate Hermes field
