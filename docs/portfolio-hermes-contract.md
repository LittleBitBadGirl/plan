# Hermes → Planner: Portfolio Import Contract

**Endpoint:** `POST /api/portfolios/{portfolio_id}/import`  
**Auth:** `Authorization: Bearer {PLANNER_API_TOKEN}`
**Content-Type:** `application/json`

Spec: `docs/superpowers/specs/2026-07-19-portfolio-analyzer-spec.md`

---

## Portfolio IDs (after seed)

| portfolio_id | slug | Name | Legacy goal_id |
|--------------|------|------|----------------|
| 1 | `iis` | ИИС | 1 | broker_contract: **9248208** |
| 2 | `podushka` | Подушка | 3 | broker_contract: **1226101/21-л** (БКС) |
| 3 | `broker-1` | Брокерский 1 | 6 | broker_contract: **1149213**; goal «Автомобиль» |
| 4 | `broker-2` | Брокерский 2 | 7 | **T-Broker** — без monthly import; ручные snapshot/flows при выводе дивов |

---

## Request body

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
      "maturity_date": null,
      "coupon_rate": null
    }
  ],
  "flows": [
    {
      "date": "2026-07-15",
      "type": "dividend",
      "amount": 10200,
      "instrument": "TRNFP",
      "description": "Дивиденды TRNFP"
    }
  ]
}
```

### Field reference

**snapshot** (required)
- `date` — date of balance snapshot (ISO `YYYY-MM-DD`)
- `total_balance` — total account value in RUB

**positions[]** (optional but recommended)
- `ticker` — MOEX ticker if known (`TRNFP`, `SU26207`), else null
- `name` — as in broker report (human name, **not** ISIN)
- `isin` — **required when the report has it** (primary match key)
- `asset_type` — `stock` | `bond` | `etf` | `pif` | `other`. Группировка состава: bond → stock → pif (в этом порядке); `etf` и неизвестные значения валидации нет — код примет, но в составе они окажутся после ПИФ (сортировка `other`)
- `quantity` — units/shares at report date
- `market_value` — RUB
- `avg_price` — **send if the report has it** (needed for closed P&L)
- `maturity_date` — ISO date for bonds (enrich from MOEX/OFZ calendar if missing)
- `coupon_rate` — optional, annual %

**flows[]** (optional)
- `date` — payment date
- `type` — see table below
- `amount` — RUB; income/inflow positive; `tax` negative
- `isin` — **required when the note has ISIN**
- `name` — short issuer (`ИНГРАД`, `НОВАТЭК`), not the full legal line if you can parse it
- `instrument` — ticker or short name (alias of `name`)
- `ticker` — optional
- `asset_type` — `stock` | `bond` | `pif` | `other` (set on sale/redemption)
- `description` — raw broker note; **must keep issuer + ISIN** (Planner filters the paper popup by this text)

### Flow types

| type | Use |
|------|-----|
| `deposit` | Пополнение счёта |
| `withdrawal` | Вывод |
| `dividend` | Дивиденды по акциям |
| `coupon` | Купоны по облигациям |
| `redemption` | Погашение облигации **или выкуп эмитентом** (BIDS / «Выкуп бумаг эмитентом») |
| `sale` | Продажа бумаги на рынке (не выкуп) |
| `pif_accrual` | Начисление ПИФ (ден. рынок, подушка) |
| `tax` | Налог у источника / НДФЛ (amount отрицательный) |
| `commission` | Комиссия брокера |

### Closed positions (Planner logic — parser must feed it)

Planner shows **Закрытые позиции** only from `sale` / `redemption` flows whose paper is **not** in the latest non-empty `positions[]`.

- Empty `positions: []` on a later report **does not** mean everything was sold. Omit a paper from positions **and** emit `sale` or `redemption`.
- No `avg_price` ever seen → profit = all cash received (exit + coupons/dividends/taxes matched to that paper).
- Bond maturity → `redemption`. Issuer buyback → `redemption`. Market sell → `sale`.

---

## Response

**200 OK**

```json
{
  "ok": true,
  "import_id": 42,
  "positions_upserted": 12,
  "flows_inserted": 8,
  "flows_skipped_duplicate": 2
}
```

**400** — invalid JSON or unknown portfolio_id  
**401** — missing/invalid token  
**409** — duplicate report (same payload_hash) — returns previous import_id

---

## Example: curl

```bash
curl -X POST "https://planner.example/api/portfolios/1/import" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @report-iis-2025-07.json
```

---

## Idempotency

Re-sending the same report (same flows) must not duplicate rows.  
Match key: `(portfolio_id, date, type, amount, description)`.

---

## Source format: Sovcombank БО (xlsx in password zip)

Full parsing reference: **`docs/portfolio-sovcombank-report-format.md`**

Key facts from May 2025 sample:
- **No tickers** — only full names + ISIN (`RU0009029557`)
- **Primary match key:** ISIN (extract from column B or Примечание)
- **Flows section:** «Движение денежных средств по неторговым операциям»
- **Operation mapping:** `Выплата дивидендов` → dividend, `Выплата купонного дохода` → coupon, `Списание НДФЛ` → tax
- **Maturity dates:** NOT in report — enrich externally for bonds
- **Test fixture:** `tests/fixtures/sample_sovcombank_import.json`

Import payload: add optional `isin` field on positions and flows.

---

## TODO

- [x] **Подушка** — БКС `1226101/21-л`, `docs/portfolio-bcs-report-format.md`
- [x] **Брокерский 2** — T-Broker, без отчётов (manual/sparse only)
- [ ] ISIN → ticker lookup table (MOEX)
- [ ] Bond maturity enrichment (MOEX/CBONDS)
- [ ] Monthly job: unzip → parse → POST per portfolio
