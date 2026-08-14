# T-Bank Broker Report — Format Reference

**Source:** `open_doc.xlsx` — cumulative report from T-Bank app/export  
**Account:** `2132867011` от 07.02.2024  
**Portfolio:** id=4, tab «Брокерский 2»

Unlike Sovcombank (password zip monthly), T-Bank exports a **single wide xlsx** covering a long period (example: 07.02.2024 – 08.07.2026).

---

## Key sections

| Section | Content |
|---------|---------|
| 1.1 | Trades (deals) |
| **2. Операции с денежными средствами** | **Main for flows** — dividends, coupons, tax, withdrawals |
| 3.1 | Security movement (start/end qty per instrument) |
| 4.1 | Instrument reference (ticker + ISIN) |

---

## Cash balance (example 13.07.2026)

| Currency | Outgoing balance |
|----------|------------------|
| RUB | **13.16** |
| USD | 0.57 |

Cash ≈ zero, but **positions remain** (bonds, stocks, ETFs) → coupons/dividends still arrive.

---

## Flow mapping (section 2)

| Operation (AL column) | import `type` | Notes |
|----------------------|---------------|-------|
| Выплата доходов по корпоративным действиям + «дивиденд» in note | `dividend` | ISIN in BY (Примечание) |
| Same + «облигациям» / coupon | `coupon` | |
| Same + «Погашение» | `redemption` | |
| Налог (дивиденды) / Налог | `tax` | |
| Вывод средств | `withdrawal` | |
| Ввод / Покупка/продажа | `deposit` / trade | |

**Instrument ID:** ticker in column Q of reference + ISIN in note: `ISIN: RU000A0DKVS5`

Example names: `Новатэk аo` (NVTK), `ОФЗ 26230` (SU26230RMFS1), `РЖД оббП27`

---

## Activity level (this account)

| Year | Income operations (coupons/dividends) |
|------|--------------------------------------|
| 2024 | 61 |
| 2025 | 35 |
| 2026 (YTD Jul) | 16 |

**Conclusion:** account is inactive for **trading**, but **NOT dead for income** — ~2–3 coupon payments per month from remaining bonds.

---

## Import strategy (recommended)

| Phase | Action |
|-------|--------|
| **Once** | Bulk import this cumulative report → full history 2024–2026 |
| **Ongoing** | **Quarterly** export from T-Bank OR import when withdrawing dividends |
| **NOT needed** | Monthly reports like Sovcombank ИИС |

Hermes: `POST /api/portfolios/4/import` after parsing T-Bank xlsx (separate parser from Sovcombank).

---

## Remaining positions (still generating income, Jul 2026)

Examples with end qty > 0: ОФЗ 26230 (11), ОФЗ 26233 (11), GTLK 1P-17 (10), РЖД оббП27 (1), РОСНАНО-8 (7), HeadHunter (1), various legacy stocks.

No Transneft on this account.
