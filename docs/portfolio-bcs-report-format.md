# BCS Broker Report — Format Reference (Подушка)

**Source:** `.xls` (Excel 97–2003, OLE)  
**Example:** `Отчет с 13.04.2026 по 13.07.2026.xls`  
**Broker:** ООО «Компания БКС»  
**Agreement:** `1226101/21-л` от 02.06.2021  
**Portfolio:** id=2, tab «Подушка»

---

## Summary (Apr–Jul 2026 example)

| Metric | Start (13.04) | End (13.07) |
|--------|---------------|-------------|
| **Стоимость портфеля** | **~209 268 ₽** | **~512 920 ₽** |
| Деньги (RUB) | 884 | **230 594** |
| ПИФ паи RU000A108ZJ5 | 10 324 @ 13.555 | **15 533 @ 14.025** |
| Облигация RU000A10EEZ9 | 44 шт | 44 шт |

---

## Sheet structure

Single sheet `Лист_1`:

| Section | Content |
|---------|---------|
| **1. Движение денежных средств** | Cash ops: deposits, PIF buys, coupons, withdrawals |
| **2.1. Сделки** | Trades (PIF purchases by ISIN) |
| **3. Активы** | Portfolio snapshot start/end |
| **4. Движение ЦБ** | Security qty movement |

---

## Key instrument: ПИФ денежного рынка

| Field | Value |
|-------|-------|
| ISIN | **RU000A108ZJ5** |
| Type | **Пай** (PIF unit) |
| Reg # | 6330 |
| Ticker in deals | RU000A108ZJ5 |

**Income model:** NAV grows (13.555 → 14.025), no separate «дивидend» line.  
Planner: compute `pif_accrual` as Δ(market_value) − net_purchases per period, OR import snapshot diff.

---

## Cash flow mapping (section 1.1.1)

| BCS «Операция» | import `type` |
|----------------|---------------|
| Приход ДС | `deposit` |
| Вывод ДС | `withdrawal` |
| Погашение купона | `coupon` |
| Покупка/Продажа | (PIF buy — not income; links to RU000A108ZJ5 in section 2) |
| НДФЛ | `tax` |
| Вознаграждение компании | `commission` |
| Покупка/Продажа (репо) | internal / ignore for income table |

### Example coupons (bond RU000A10EEZ9)

| Date | Amount |
|------|--------|
| 04.05.2026 | 542.52 ₽ |
| 02.06.2026 | 542.52 ₽ |
| 02.07.2026 | 542.52 ₽ |

---

## Positions (section 3, end of period)

| ISIN | Type | Qty end | Price | Value |
|------|------|---------|-------|-------|
| RU000A108ZJ5 | Пай (PIF) | 15 533 | 14.025 | ~217 850 ₽ |
| RU000A10EEZ9 | Обл. | 44 | 99.71 | ~4 387 ₽ + NKD |
| PAAS, PCG, SCCO… | US stocks | legacy | frozen | small |

---

## Positions for import JSON

```json
{
  "report_date": "2026-07-13",
  "snapshot": {
    "date": "2026-07-13",
    "total_balance": 512920.0
  },
  "positions": [
    {
      "isin": "RU000A108ZJ5",
      "name": "ПИФ денежного рынка БКС (пай)",
      "asset_type": "pif",
      "quantity": 15533,
      "market_value": 217850,
      "avg_price": 14.025
    },
    {
      "isin": "RU000A10EEZ9",
      "name": "Облигация (RU000A10EEZ9)",
      "asset_type": "bond",
      "quantity": 44,
      "market_value": 4500
    }
  ],
  "flows": [
    {"date": "2026-05-04", "type": "coupon", "amount": 542.52, "isin": "RU000A10EEZ9"},
    {"date": "2026-06-02", "type": "coupon", "amount": 542.52, "isin": "RU000A10EEZ9"},
    {"date": "2026-07-02", "type": "coupon", "amount": 542.52, "isin": "RU000A10EEZ9"},
    {"date": "2026-07-12", "type": "deposit", "amount": 214000, "description": "Приход ДС"}
  ]
}
```

**PIF yield row:** add computed `pif_accrual` per month from NAV × qty delta (Hermes logic).

---

## Parser notes

- Format: **xlrd** for `.xls` (not openpyxl)
- Dates: `02.05.26` → 2026-05-02
- Third-party broker — **different parser** from Sovcombank xlsx and T-Bank xlsx
- Hermes: `POST /api/portfolios/2/import`

---

## Import frequency

**Monthly** (like ИИС) — active deposits/PIF purchases; report can cover 1–3 months.

---

## Full account map (all 4 confirmed)

| portfolio | Tab | Broker | Contract |
|-----------|-----|--------|----------|
| 1 | ИИС | Совкомбанк | 9248208 |
| 2 | **Подушка** | **БКС** | **1226101/21-л** |
| 3 | Бrokerский 1 | Совкомбанк | 1149213 |
| 4 | Бrokerский 2 | T-Bank | 2132867011 |
