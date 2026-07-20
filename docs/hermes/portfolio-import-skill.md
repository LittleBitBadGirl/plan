---
name: portfolio-import
description: >-
  Parse broker reports from phone exports and POST JSON to Planner Portfolio Analyzer.
  Use when Vera uploads Sovcombank БО zip, BCS xls, T-Bank xlsx, or asks to import
  portfolio / broker report / дивиденды / купоны into planner.
---

# Portfolio Import — серверный Hermes

Skill для **серверного Hermes** (VPS). Локальный Hermes — только dev; боевой pipeline здесь.

## Роль

Ты — Hermes на сервере. Vera скидывает отчёты **с телефона** из брокерских приложений.  
Твоя задача: **распарсить → собрать JSON → POST в Planner**.

Planner **не парсит** xlsx/zip сам — только принимает JSON.

---

## Конфиг (.env Hermes)

```env
PLANNER_URL=https://planner.ВАШ-ДОМЕН
PLANNER_API_TOKEN=...   # тот же API_TOKEN что в planner/.env
```

Локальная отладка: `PLANNER_URL=http://127.0.0.1:8000`

---

## Куда слать (portfolio_id)

| id | slug | Брокер | Файл / cadence |
|----|------|--------|----------------|
| **1** | iis | Совкомбанк 9248208 | БО zip monthly |
| **2** | podushka | БКС 1226101/21-л | `.xls` monthly |
| **3** | broker-1 | Совкомбанк 1149213 | БО zip monthly |
| **4** | broker-2 | T-Bank 2132867011 | bulk xlsx once + quarterly |

**Endpoint:**

```
POST {PLANNER_URL}/api/portfolios/{portfolio_id}/import
Authorization: Bearer {PLANNER_API_TOKEN}
Content-Type: application/json
```

Контракт JSON: **planner repo** → `docs/portfolio-hermes-contract.md`

---

## Pipeline (каждый import)

```
1. Получить файл (Telegram / inbox / папка uploads)
2. Определить брокера и portfolio_id (таблица выше)
3. Открыть parser doc:
   - Совкомбанк → docs/portfolio-sovcombank-report-format.md
   - БКС       → docs/portfolio-bcs-report-format.md
   - T-Bank    → docs/portfolio-tbank-report-format.md
4. Извлечь: snapshot, positions[], flows[]
5. Сохранить JSON локально (audit): reports/parsed/{slug}-{report_date}.json
6. POST import
7. Лог: import_id, flows_inserted, errors
8. При 409 (duplicate) — OK, отчёт уже был
```

---

## JSON (минимум)

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

### Flow types

| type | Когда |
|------|-------|
| `deposit` | Пополнение |
| `withdrawal` | Вывод |
| `dividend` | Дивиденды |
| `coupon` | Купоны |
| `redemption` | Погашение облигации |
| `pif_accrual` | ПИФ подушки (начисление) |
| `tax` | НДФЛ |
| `commission` | Комиссия |

---

## Sovcombank (portfolio 1, 3)

- Input: password-protected zip → xlsx
- **Нет ticker** — только name + ISIN; ticker выводить из справочника или MOEX
- Секция flows: «Движение денежных средств по неторговым операциям»
- `Выплата дивидендов` → dividend, `Выплата купонного дохода` → coupon
- Maturity облигаций **нет в отчёте** — enrich позже

Parser doc: `docs/portfolio-sovcombank-report-format.md`  
Fixture: `tests/fixtures/sample_sovcombank_import.json`

---

## BCS Подушка (portfolio 2)

- Input: `.xls` с телефона / почты
- ПИФ денежного рынка → flow type `pif_accrual`
- Parser doc: `docs/portfolio-bcs-report-format.md`

---

## T-Bank Брокерский 2 (portfolio 4)

- **Не monthly** — торгов почти нет
- Bulk cumulative xlsx **один раз** для истории
- Дальше quarterly или при выводе дивов (~35 выплат/год)
- Parser doc: `docs/portfolio-tbank-report-format.md`

---

## curl smoke test

```bash
curl -X POST "$PLANNER_URL/api/portfolios/1/import" \
  -H "Authorization: Bearer $PLANNER_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @report-iis-2025-07.json
```

Ответ 200: `{"ok":true,"import_id":...,"flows_inserted":...}`  
Ответ 409: duplicate report_date — не ошибка.

---

## Post-deploy verify (после деплоя planner)

Planner migration **008** применяется **автоматически** при `docker compose up` (init_db → alembic upgrade head).

Hermes после деплоя planner:

```bash
# 1. Planner жив
curl -s "$PLANNER_URL/api/health"

# 2. Portfolios есть
curl -s -H "Authorization: Bearer $PLANNER_API_TOKEN" "$PLANNER_URL/api/portfolios"

# 3. Test import
curl -X POST "$PLANNER_URL/api/portfolios/1/import" \
  -H "Authorization: Bearer $PLANNER_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"report_date":"2025-07-31","snapshot":{"date":"2025-07-31","total_balance":1},"positions":[],"flows":[]}'
```

---

## Ошибки

| HTTP | Действие |
|------|----------|
| 401 | Проверить PLANNER_API_TOKEN |
| 400 | JSON / unknown portfolio_id |
| 404 | Planner не задеплоен или migration не прошла |
| 409 | Duplicate — skip |
| 5xx | Лог planner: `docker logs task_planner` |

---

## Не делать

- Не писать напрямую в SQLite planner.db (legacy path deprecated)
- Не слать сырой xlsx в import endpoint — только JSON
- Не import portfolio 4 monthly если нет новых данных

---

## Ссылки в planner repo

- Handoff для всех агентов: `docs/portfolio-analyzer-handoff.md`
- JSON contract: `docs/portfolio-hermes-contract.md`
- Endpoints: `docs/ENDPOINTS.md`
