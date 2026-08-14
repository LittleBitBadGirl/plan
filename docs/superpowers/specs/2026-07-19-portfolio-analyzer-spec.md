# Portfolio Analyzer — Design Spec

**Date:** 2026-07-19  
**Status:** Approved (Vera)  
**Owner:** Vera  

---

## Problem

Инвестиционная аналитика спрятана в popup на `/finance`, смешана с расходами и целями накопления. Нужен отдельный раздел для брокерских счетов: состав, дивиденды/купоны/погашения, доход по инструментам (в т.ч. ПИФ подушки), цели на счёте.

**Acceptance example:** владелец TRNFP на ИИС видит в таблице — сколько и в какие месяцы пришли дивиденды по Транснефти.

---

## User decisions (final)

| # | Question | Decision |
|---|----------|----------|
| 1 | Раздел | **A** — отдельный `/portfolio` в sidebar |
| 2 | Подушка | **В новом разделе** — деньги на брокерском, ПИФ ден. рынка, растёт сам |
| 3 | Hermes | **HTTP import** — `POST /api/portfolios/{id}/import` |
| 4 | Переключатель | **Tabs** (4 счёта, все влезают) |
| 5 | «Автомобиль» | **a)** счёт → «Брокерский 1», цель «Автомобиль» отдельно на странице портфеля |
| 6 | Содержимое отчёта Hermes | **d)** покажет пример — уточним при реализации |
| 7 | Формат отчёта | Определим контракт JSON сами, попросим Hermes слать так |
| 8 | ПИФ подушки | **d)** пока не парсится — добавить в import |
| 9 | Названия инструментов | Из примера отчёта при реализации + нормализация |
| 10 | TRNFP | На счёте **ИИС** (portfolio id=1 / legacy goal_id=1) |
| 11 | Scope | **Всё сразу** — schema + import + UI + drill-down |
| 12 | Drill-down | Где удобно (клик по строке → выплаты с датами) |
| 13 | Зимовка и прочие | Остаются только в `/finance` |
| 14 | Брокерский 2 (T-Broker) | **Quarterly/on-demand** — торгов нет, купоны ~35/год; bulk import once |

---

## Account map (confirmed)

| portfolio | Tab | Broker | Contract | Import |
|-----------|-----|--------|----------|--------|
| 1 | ИИС | Совкомбанк | 9248208 | Monthly БО zip → Hermes |
| 2 | Подушка | **БКС** | 1226101/21-л | Monthly `.xls` → `portfolio-bcs-report-format.md` |
| 3 | Брокерский 1 | Совкомбанк | 1149213 | Monthly БО zip → Hermes |
| 4 | Брокерский 2 | **T-Broker** | 2132867011 | Quarterly + bulk once → `portfolio-tbank-report-format.md` |

### Брокерский 2 — T-Bank

- **Не monthly** — торгов почти нет, кэш ~13 ₽
- **Купоны/дивы идут** (~35 выплат/год) — остались ОФЗ, корп. облигации, хвосты акций
- **Import:** (1) разово bulk cumulative xlsx, (2) дальше **раз в квартал** или при выводе дивов

| Asset | Location |
|-------|----------|
| Goals ≈ broker accounts | `financial_goals` ids 1,3,6,7 hardcoded in `finance.py` |
| Snapshots / flows | `investment_snapshots`, `investment_flows` — reflected tables, no ORM, no Alembic, Hermes writes SQLite directly |
| Analytics UI | Modal in `finance.html`, `buildCashflowTable()`, `GET /api/goals/{id}/analytics` |
| Hermes in repo | Productivity only (`/stats`); finance import **external** |

**Legacy goal mapping:**

| legacy goal_id | Current name | New portfolio name | Tab order |
|----------------|--------------|-------------------|-----------|
| 1 | ИИС | ИИС | 1 |
| 3 | Подушка | Подушка | 2 |
| 6 | Автомобиль | **Брокерский 1** | 3 |
| 7 | Брокерский 2 | Брокерский 2 | 4 |

Goal «Автомобиль» (target/current) → `portfolio_goals` on portfolio 3, not the account name.

---

## Target architecture

```
Hermes (cron / manual)
    │
    POST /api/portfolios/{id}/import  (Bearer API_TOKEN)
    │
    ▼
┌─────────────────────────────────────────────────┐
│ portfolios                                       │
│ portfolio_goals                                  │
│ investment_snapshots  (+ portfolio_id)           │
│ investment_flows      (+ portfolio_id)           │
│ instruments                                      │
│ positions                                        │
│ import_log                                       │
└─────────────────────────────────────────────────┘
    │
    ▼
GET /portfolio  +  GET /api/portfolios/{id}/analytics
                  GET /api/portfolios/{id}/composition
                  GET /api/portfolios/{id}/payments?instrument=
```

---

## Data model

### `portfolios`

| Column | Type | Notes |
|--------|------|-------|
| id | PK | |
| name | String(200) | ИИС, Подушка, Брокерский 1, Брокерский 2 |
| slug | String(50) | `iis`, `podushka`, `broker-1`, `broker-2` |
| type | String(20) | `iis`, `broker`, `reserve` |
| legacy_goal_id | Integer | FK → financial_goals.id (migration) |
| sort_order | Integer | Tab order |
| broker_contract | String(50) | nullable, e.g. `9248208` (Совкомбанк ИИС) |
| created_at | DateTime | |

### `portfolio_goals`

| Column | Type | Notes |
|--------|------|-------|
| id | PK | |
| portfolio_id | FK | |
| name | String(200) | «Автомобиль» |
| target_amount | Float | |
| current_amount | Float | Sync from goal or snapshot |
| created_at | DateTime | |

Seed: portfolio 1 (ИИС) → `broker_contract='9248208'`; portfolio 3 (Брокерский 1) → `broker_contract='1149213'` + goal «Автомобиль» from legacy financial_goals id=6.

### `instruments`

| Column | Type | Notes |
|--------|------|-------|
| id | PK | |
| ticker | String(20) | TRNFP, SU26207… nullable |
| name | String(200) | Display name |
| asset_type | String(20) | `stock`, `bond`, `etf`, `pif`, `other` |
| maturity_date | Date | nullable, bonds |
| coupon_rate | Float | nullable |
| aliases | JSON/Text | `["TRNFP","Транснефть","Транснефть ап"]` for normalization |

### `positions` (snapshot per import)

| Column | Type | Notes |
|--------|------|-------|
| id | PK | |
| portfolio_id | FK | |
| instrument_id | FK | |
| snapshot_date | Date | |
| quantity | Float | |
| avg_price | Float | nullable |
| market_value | Float | |
| weight_pct | Float | nullable |

Unique: `(portfolio_id, instrument_id, snapshot_date)`.

### `investment_snapshots` / `investment_flows` (formalize)

Add columns via migration:
- `portfolio_id` FK (backfill from `goal_id` / `legacy_goal_id`)
- Keep `goal_id` nullable during transition for Hermes compat

Flow types: `deposit`, `withdrawal`, `coupon`, `dividend`, `tax`, `commission`, **`redemption`** (погашение), **`pif_accrual`** (начисление ПИФ ден. рынка).

### `import_log`

| Column | Type | Notes |
|--------|------|-------|
| id | PK | |
| portfolio_id | FK | |
| report_date | Date | Period of broker report |
| source | String(50) | `hermes` |
| payload_hash | String(64) | Dedup |
| status | String(20) | `ok`, `error` |
| error_message | Text | nullable |
| created_at | DateTime | |

---

## HTTP Import contract

**Endpoint:** `POST /api/portfolios/{portfolio_id}/import`  
**Auth:** `Authorization: Bearer {API_TOKEN}` (same as other `/api/*`)

**Request body (JSON):**

```json
{
  "report_date": "2026-06-30",
  "snapshot": {
    "date": "2026-06-30",
    "total_balance": 1234567.89
  },
  "positions": [
    {
      "ticker": "TRNFP",
      "name": "Транснефть (п)",
      "asset_type": "stock",
      "quantity": 50,
      "market_value": 72000,
      "avg_price": 1200,
      "maturity_date": null
    },
    {
      "ticker": null,
      "name": "ПИФ Денежный рынок",
      "asset_type": "pif",
      "quantity": 1234.56,
      "market_value": 500000,
      "avg_price": null,
      "maturity_date": null
    }
  ],
  "flows": [
    {
      "date": "2026-07-15",
      "type": "dividend",
      "amount": 10200,
      "instrument": "TRNFP",
      "description": "Трансneft (п) дивиденды"
    },
    {
      "date": "2026-06-28",
      "type": "pif_accrual",
      "amount": 3200,
      "instrument": null,
      "description": "ПИФ Денежный рынок — начисление"
    },
    {
      "date": "2026-12-15",
      "type": "redemption",
      "amount": 100000,
      "instrument": "SU26207",
      "description": "Погашение ОФЗ 26207"
    }
  ]
}
```

**Response:**

```json
{
  "ok": true,
  "import_id": 42,
  "positions_upserted": 12,
  "flows_inserted": 8,
  "flows_skipped_duplicate": 2
}
```

**Rules:**
- Idempotent by `(portfolio_id, report_date, payload_hash)` or flow dedup `(portfolio_id, date, type, amount, description)`
- `instrument` in flows matched to `instruments` via ticker → aliases → name
- Unknown instruments auto-created on first import
- Validate `portfolio_id` exists

**Hermes handoff doc:** см. `docs/portfolio-hermes-contract.md` (создаётся агентом Task 1).

---

## UI: `/portfolio`

### Layout

```
[ ИИС ] [ Подушка ] [ Брокерский 1 ] [ Брокерский 2 ]   ← tabs

KPI row: Баланс | Δ мес | Δ год | Купоны YTD | Дивиденды YTD

[ Цель: Автомobile ████░░ 72% ]   ← only if portfolio_goals exist

Balance sparkline (period filters: prev month / year / custom)

┌─ КАЛЕНДАРЬ ДОХОДОВ ─────────────────────────────────┐
│ [2024] [2025] [2026] [Все]    🔍 поиск инструмента    │
│ Excel table: instrument × 12 months + summary rows    │
│ Rows: instruments, then Купоны / Дивиденды / Погашения│
└───────────────────────────────────────────────────────┘

┌─ СОСТАВ ПОРТФЕЛЯ ────────────────────────────────────┐
│ Ticker | Название | Доля | Кол-во | След. купон | Погашение │
└───────────────────────────────────────────────────────┘
```

### Drill-down

Click instrument row → panel/modal:
- List of payments: `date`, `type`, `amount`, `description`
- Source: `GET /api/portfolios/{id}/payments?instrument=TRNFP&year=2025`

### `/finance` changes

- Remove or simplify «Инвестиции» 2×2 grid → compact summary + link «Портфель →»
- Keep Подушка progress visible in finance OR only link — **TBD in implementation** (prefer link only to avoid duplicate)
- Analytics modal: deprecate, redirect to `/portfolio?tab=iis`

---

## API endpoints (new)

| Method | URL | Purpose |
|--------|-----|---------|
| GET | `/portfolio` | SSR page, `?tab=iis` |
| GET | `/api/portfolios` | List tabs metadata |
| GET | `/api/portfolios/{id}/analytics` | Snapshots, flows, monthly_cashflow (all years) |
| GET | `/api/portfolios/{id}/composition` | Latest positions + upcoming maturities |
| GET | `/api/portfolios/{id}/payments` | Drill-down: `?instrument=&year=` |
| POST | `/api/portfolios/{id}/import` | Hermes import |

Keep `GET /api/goals/{id}/analytics` as alias → portfolio by legacy_goal_id (backward compat).

---

## Fixes to existing code

1. Remove `date >= "2025-01-01"` filter in analytics — show full history
2. Fix `summary[ym][row.type]` bug (singular `deposit` vs plural `deposits`)
3. Replace hardcoded `INVESTMENT_GOAL_IDS` with `portfolios` table
4. Promote reflected `Table()` to SQLAlchemy models + Alembic

---

## Out of scope (v1)

- Expected vs actual dividends (204,17 ₽ recommendation)
- Dividend yield to entry price
- Multi-user / auth per portfolio
- Real-time MOEX prices

---

## Verification checklist

- [ ] `/portfolio` in sidebar, 4 tabs switch without reload (or HTMX partial)
- [ ] ИИС tab: TRNFP row visible when data imported
- [ ] Click TRNFP → payment dates and amounts
- [ ] Подушка tab: PIF accruals visible (`pif_accrual` or dividend-like)
- [ ] Брокerский 1: goal «Автомобиль» progress bar
- [ ] `POST /api/portfolios/1/import` with sample JSON → data in DB
- [ ] Duplicate import → no double flows
- [ ] `/finance` — Зимовка still in other goals; no broken savings stats
- [ ] `pytest tests/test_portfolio*.py` green

---

## Related files (existing)

| Path | Role |
|------|------|
| `app/web/routes/finance.py` | Current analytics, reflected tables, hardcoded IDs |
| `app/web/templates/finance.html` | Analytics modal, `buildCashflowTable()` |
| `app/models/goal.py` | FinancialGoal |
| `app/web/pages.py` | Router assembly |
| `app/web/templates/base.html` | Sidebar |
