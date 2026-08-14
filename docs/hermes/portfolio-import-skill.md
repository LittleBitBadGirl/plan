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
    {
      "ticker": "TRNFP",
      "name": "Транснефть (п)",
      "isin": "RU0009091578",
      "asset_type": "stock",
      "quantity": 50,
      "market_value": 72000,
      "avg_price": 1200
    }
  ],
  "flows": [
    {
      "date": "2025-07-15",
      "type": "dividend",
      "amount": 10200,
      "isin": "RU0009091578",
      "name": "Транснефть",
      "instrument": "TRNFP",
      "description": "Выплата дивидендов, ПАО \"Транснефть\", ISIN: RU0009091578"
    }
  ]
}
```

Полный контракт: `docs/portfolio-hermes-contract.md`

---

## Что Planner делает с JSON (логика 2026-08)

Не UI. Это правила, без которых аналитика врёт.

### Состав (`positions[]`)

- Состав на `/portfolio` = **последняя дата, где positions не пустые**.
- Пустой `positions: []` в следующем месяце **не закрывает бумаги**. Не слать пустой состав «для баланса», если бумаги ещё на счёте.
- Бумага пропала из портфеля → **убрать её из positions** этого отчёта **и** добавить flow `sale` или `redemption`.
- `asset_type`: `bond` / `stock` / `pif` — группировка состава (облигации → акции → ПИФ). Погашение в таблице только у облигаций.
- `maturity_date` для облигаций — слать, если знаешь; иначе Planner подставит ОФЗ из своего календаря (серия / «ОФЗ 29» = CNY фев 2029).
- `avg_price` — обязателен, если есть в отчёте. Без него закрытая позиция считается так: **всё полученное = прибыль**.
- `isin` — главный ключ склейки бумаги между отчётами. `name` — человеческое, **не** ISIN.

### Календарь доходов (`flows[]`)

| type | Куда попадает |
|------|----------------|
| `coupon` | Купоны (облигации) |
| `dividend` | Дивиденды (акции) |
| `pif_accrual` | ПИФ |
| `redemption` | Погашения / выкупы |
| `sale` | Продажа (закрытые позиции, не календарь купонов) |
| `deposit` / `withdrawal` / `tax` | Итого по счёту за год, не строка бумаги |

`description` обязан содержать **эмитента и ISIN**. Попап по клику фильтрует выплаты по этому тексту / ISIN / короткому имени. Без ISIN в примечании попап не отличит Роснефть от остальных.

НДФЛ (`tax`, amount **отрицательный**): копируй в flow тот же `isin` и по возможности эмитента в `description`. Голое «Списание НДФЛ» не привяжется к бумаге.

### Закрытые позиции

Planner строит их **только** из `sale` и `redemption`, если бумаги уже нет в текущем составе.

| Событие в отчёте | type | Пример |
|------------------|------|--------|
| Выкуп бумаг эмитентом | `redemption` | ИНГРАД, ISIN RU000A0DJ9B4 |
| Погашение облигации | `redemption` | |
| Продажа на рынке | `sale` | |

- `name` у выкупа: коротко `ИНГРАД`, не сырой ISIN.
- `description`: полная строка брокера, как есть (`Выкуп бумаг эмитентом, ПАО "ИНГРАД", ISIN RU000A0DJ9B4`).
- Нет цены покупки → не выдумывай `avg_price`. Planner покажет покупку «—» и прибыль = полученные деньги.

### Flow types (полный список)

| type | Когда |
|------|-------|
| `deposit` | Пополнение |
| `withdrawal` | Вывод |
| `dividend` | Дивиденды |
| `coupon` | Купоны |
| `redemption` | Погашение облигации **или выкуп эмитентом** |
| `sale` | Продажа бумаги на рынке |
| `pif_accrual` | ПИФ подушки (начисление) |
| `tax` | НДФЛ (отрицательная сумма) |
| `commission` | Комиссия |

---

## Sovcombank (portfolio 1, 3)

- Input: password-protected zip → xlsx
- **Нет ticker** — только name + ISIN; ticker выводить из справочника или MOEX
- Секция flows: «Движение денежных средств по неторговым операциям»
- `Выплата дивидендов` → dividend, `Выплата купонного дохода` → coupon
- `Выкуп бумаг эмитентом` → **`redemption`** (не sale), в flow обязательны `isin` + description с эмитентом
- Продажа на рынке → `sale`
- Maturity облигаций **нет в отчёте** — enrich из MOEX/календаря ОФЗ; без даты Planner подставит известные ОФЗ сам
- Налог: `tax` с тем же `isin`, что у соседней выплаты

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
