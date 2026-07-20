# Sovcombank Broker Report (БО) — Format Reference

**Source:** Monthly zip from broker, password-protected, contains one `.xlsx`  
**Example:** `9248208_БО_Осолодкина_В_Н_20250501_20250531.zip` → May 2025  
**Broker:** ПАО «Совкомбанк», договор `9248208`

Used by Hermes parser → `POST /api/portfolios/{id}/import`

---

## File structure

| Sheet | Content |
|-------|---------|
| **Сводный лист** | Summary: total balance, cash, stocks list (compact) |
| **Единый брокерский счет** | **Main sheet** — cash flows + full portfolio |
| **Заблокировано** | Blocked/withdrawing securities |

---

## Snapshot (for import `snapshot`)

From **Сводный лист**, row «Баланс активов»:

```
report_date = last day of period (2025-05-31)
total_balance = 205992.31  (example May 2025)
```

Cash portion: «Денежные средства» → 30320.84 RUR

---

## Positions (for import `positions[]`)

**Sheet:** `Единый брокерский счет`  
**Section:** «Состояние портфеля ценных бумаг и НФИ» (header ~row 36)

| Excel column | Field | Maps to |
|--------------|-------|---------|
| A — Наименование ЦБ | Full name | `name` |
| B — ISIN / гос номер | `RU0009029557/20301481B` | `isin` (split on `/`) |
| D — Остаток на начало | quantity start | — |
| G — Остаток на конец | quantity end | `quantity` |
| K — Цена одной ЦБ | price | `avg_price` / market price |
| L — Стоимость с учетом НКД | value | `market_value` |

**Asset type detection:**

| Pattern in name | asset_type |
|-----------------|------------|
| `Акции` | `stock` |
| `Облигации` / `ОФЗ` / `RMFS` | `bond` |
| `ПИФ` / `фонд` | `pif` |

**Ticker:** NOT present in report. Derive from ISIN via reference table or MOEX lookup.  
**Primary key for matching:** `ISIN` (first part before `/`).

**Bonds in example (May 2025):**

| ISIN | Name fragment | qty |
|------|---------------|-----|
| RU000A100EF5 | ОФЗ 26230 | 13 |
| RU000A1038V6 | ОФЗ 26238 | 14 |
| RU000A105L19 | ОФЗ 29023 | 1 |
| RU000A1066D5 | ОФЗ 29024 | 1 |
| RU000A106E90 | ОФЗ 26243 | 10 |
| RU000A106Z61 | ОФЗ 29025 | 1 |
| RU000A1074G2 | ОФЗ 26244 | 9 |
| RU000A1083U4 | МТС бирж. обл. | 1 |

**Maturity date:** NOT in this report. Hermes must enrich from MOEX/CBONDS or static OFZ calendar.

---

## Cash flows (for import `flows[]`)

**Section:** «Движение денежных средств по неторговым операциям» (~row 25)

| Column | Field |
|--------|-------|
| A — Дата | `date` (Excel serial → ISO) |
| B — Тип операции | Зачисление / Списание |
| C — Сумма | `amount` (positive for income) |
| E — Вид операции | operation kind |
| F — Примечание | instrument details + ISIN |

### Operation mapping

| Вид операции (Sovcombank) | import `type` | Notes |
|---------------------------|---------------|-------|
| Выплата дивидендов | `dividend` | Parse ISIN + issuer from Примечание |
| Выплата купонного дохода | `coupon` | Parse ISIN from Примечание |
| Списание НДФЛ | `tax` | Link to preceding dividend/coupon same date |
| Выкуп бумаг эмитентом | `redemption` | Corporate action (offer) |
| Зачисление (other) | `deposit` | If not dividend/coupon |
| Списание (other) | `withdrawal` / `commission` | Context-dependent |

### Example flows (May 2025)

| date | type | amount | instrument (from note) |
|------|------|--------|------------------------|
| 2025-05-19 | dividend | 139.95 | NОВАТЭK, ISIN RU000A0DKVS5 |
| 2025-05-19 | tax | -18.00 | (NDFL on NOVATEK) |
| 2025-05-23 | dividend | 44.00 | MD Medical Group, ISIN RU000A108KL3 |
| 2025-05-23 | tax | -6.00 | |
| 2025-05-28 | coupon | 52.64 | Минфин, ISIN RU000A106Z61 (ОФЗ 29025) |
| 2025-05-07 | redemption | 1806.60 | INGRAD buyback (BIDS) |

### Instrument extraction from Примечание

Regex patterns:

```
ISIN: RU000A0DKVS5
ISIN RU000A0DJ9B4
ПАО "НОВАТЭК"
1 ЦБ=46.65RUB   ← per-share amount (useful for validation)
```

Store `isin` as primary match key; `name` = short issuer name derived from note.

---

## Instrument naming (normalization)

Report uses **full legal names**, not tickers:

| Report name | Suggested display | ISIN | MOEX ticker |
|-------------|-------------------|------|-------------|
| Акции привилегированные ПАО Сбербанк | SBERP | RU0009029557 | SBERP |
| Акции обыкновенные ПАО "НОВАТЭК" | NVTK | RU000A0DKVS5 | NVTK |
| Облигации ... серия 26243RMFS | ОФЗ 26243 | RU000A106E90 | SU26243RMFS5 |

**TRNFP (Transneft):** NOT in this sample report. Expected format when present:

```
Акции привилегированные ПАО "Транснефть"
ISIN: RU0009091578  (verify)
```

---

## Portfolio mapping

| Contract / broker | portfolio_id | Tab | Confirmed |
|-------------------|--------------|-----|-----------|
| **9248208** Совкомбанк (от 22.03.2024) | **1** | **ИИС** | ✓ ~206k, май 2025 |
| **1149213** Совкомбанк (от 22.02.2023) | **3** | **Брокерский 1** | ✓ ~20k, сен 2024; цель «Автомобиль» |
| Подушка (ПИФ ден. рынка) | 2 | Подушка | ✓ **~513k**, июл 2026; БКС `1226101/21-л`; ISIN **RU000A108ZJ5** |
| **T-Broker** `2132867011` | **4** | **Брокерский 2** | ✓ quarterly; `docs/portfolio-tbank-report-format.md` |

---

## Hermes parser pipeline

```
1. Unzip (password from env / user input — NEVER store in repo)
2. Parse xlsx sheet "Единый брокерский счет"
3. Extract snapshot from "Сводный лист"
4. Map positions → positions[]
5. Map cash movements → flows[]
6. Resolve ISIN → instrument registry
7. POST /api/portfolios/{id}/import
```

---

## Sample import JSON (from May 2025 report, truncated)

See `tests/fixtures/sample_sovcombank_import.json`
