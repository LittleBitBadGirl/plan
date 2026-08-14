import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db.seed_portfolios import seed_portfolios
from app.models.goal import FinancialGoal
from app.models.investment import InvestmentFlow, InvestmentSnapshot
from app.models.portfolio import ImportLog, Instrument, Portfolio, PortfolioGoal, Position
from datetime import date

from app.services.instrument_normalize import core_issuer_token, display_name_from_description
from app.services.ofz_calendar import lookup_ofz_maturity
from app.services.portfolio_service import (
    estimate_position_cost,
    import_report,
    sort_cashflow_instruments,
    sort_composition_positions,
)

FIXTURES = Path(__file__).parent / "fixtures"


async def _reset_portfolio_tables(db) -> None:
    await db.execute(delete(InvestmentFlow))
    await db.execute(delete(InvestmentSnapshot))
    await db.execute(delete(Position))
    await db.execute(delete(ImportLog))
    await db.execute(delete(Instrument))
    await db.execute(delete(PortfolioGoal))
    await db.execute(delete(Portfolio))
    await db.execute(delete(FinancialGoal).where(FinancialGoal.id == 6))
    await db.commit()


@pytest_asyncio.fixture
async def portfolio_api_db(db):
    await _reset_portfolio_tables(db)
    db.add(
        FinancialGoal(
            id=6,
            name="Автомобиль",
            target_amount=1_500_000,
            current_amount=500_000,
        )
    )
    await db.flush()
    await seed_portfolios(db)
    await db.commit()
    return db


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_sort_cashflow_instruments_bonds_before_stocks():
    items = [
        {"name": "Дивиденды A", "type": "dividend", "total": 100},
        {"name": "Купон B", "type": "coupon", "total": 1},
        {"name": "Выкуп C", "type": "redemption", "total": 50},
        {"name": "Дивиденды D", "type": "dividend", "total": 8},
    ]
    names = [row["name"] for row in sort_cashflow_instruments(items)]
    assert names == ["Купон B", "Дивиденды A", "Дивиденды D", "Выкуп C"]


def test_sort_composition_positions_bonds_before_stocks():
    items = [
        {"name": "Сбер", "asset_type": "stock", "weight_pct": 40, "market_value": 400},
        {"name": "ОФЗ", "asset_type": "bond", "weight_pct": 5, "market_value": 50},
        {"name": "Полюс", "asset_type": "stock", "weight_pct": 10, "market_value": 100},
        {"name": "Денежный рынок", "asset_type": "pif", "weight_pct": 20, "market_value": 200},
    ]
    names = [row["name"] for row in sort_composition_positions(items)]
    assert names == ["ОФЗ", "Сбер", "Полюс", "Денежный рынок"]


def test_display_name_from_ingrad_buyback():
    assert display_name_from_description(
        'Выкуп бумаг эмитентом, ПАО "ИНГРАД", ISIN RU000A0DJ9B4'
    ) == "ИНГРАД"
    assert display_name_from_description("Списание НДФЛ") is None


def test_core_issuer_token_nested_quotes():
    assert core_issuer_token('ПАО "НК "Роснефть"') == "Роснефть"
    assert core_issuer_token('Акции обыкновенные ПАО "НОВАТЭК"') == "НОВАТЭК"


def test_estimate_position_cost_stock_and_bond_percent():
    assert estimate_position_cost(50, 1200, 72000) == 60000
    assert estimate_position_cost(1, 94.275, 944.46) == 942.75
    assert estimate_position_cost(50, None, 72000) is None


def test_lookup_ofz_maturity_series_and_cny():
    assert lookup_ofz_maturity("ОФЗ 29025") == date(2037, 8, 12)
    assert lookup_ofz_maturity("серия 29025RMFS") == date(2037, 8, 12)
    assert lookup_ofz_maturity("ОФЗ 29 CNY") == date(2029, 2, 28)
    assert lookup_ofz_maturity("ОФЗ 29") == date(2029, 2, 28)
    assert lookup_ofz_maturity("Купон: ОФЗ 26230") == date(2039, 3, 16)
    assert lookup_ofz_maturity("Сбербанк") is None


@pytest.mark.asyncio
async def test_list_portfolios(client, portfolio_api_db):
    response = await client.get("/api/portfolios")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 4
    assert data[0]["slug"] == "iis"
    assert data[2]["slug"] == "broker-1"
    assert data[2]["goals"][0]["name"] == "Автомобиль"


@pytest.mark.asyncio
async def test_portfolio_page_ssr(client, portfolio_api_db):
    response = await client.get("/portfolio?tab=podushka")
    assert response.status_code == 200
    assert "Портфель" in response.text
    assert "Подушка" in response.text
    assert "bg-yellow-600/20" in response.text


@pytest.mark.asyncio
async def test_import_endpoint(client, portfolio_api_db):
    payload = _load_fixture("sample_import.json")
    response = await client.post("/api/portfolios/1/import", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["flows_inserted"] == 1
    assert body["positions_upserted"] == 1


@pytest.mark.asyncio
async def test_import_duplicate_returns_409(client, portfolio_api_db):
    payload = _load_fixture("sample_import.json")
    first = await client.post("/api/portfolios/1/import", json=payload)
    assert first.status_code == 200

    second = await client.post("/api/portfolios/1/import", json=payload)
    assert second.status_code == 409
    assert second.json()["duplicate"] is True


@pytest.mark.asyncio
async def test_analytics_all_years_and_summary_keys(client, portfolio_api_db):
    payload = {
        "report_date": "2024-06-30",
        "snapshot": {"date": "2024-06-30", "total_balance": 500000},
        "positions": [],
        "flows": [
            {
                "date": "2024-03-10",
                "type": "deposit",
                "amount": 100000,
                "description": "Пополнение",
            },
            {
                "date": "2024-04-15",
                "type": "dividend",
                "amount": 5000,
                "instrument": "TRNFP",
                "description": "Дивиденды TRNFP",
            },
        ],
    }
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    response = await client.get("/api/portfolios/1/analytics")
    assert response.status_code == 200
    data = response.json()

    assert data["portfolio_id"] == 1
    assert len(data["flows"]) == 2
    assert any(f["date"] == "2024-03-10" for f in data["flows"])

    summary = data["monthly_cashflow"]["summary"]
    assert summary["2024-03"]["deposits"] == 100000
    assert summary["2024-04"]["dividends"] == 5000


@pytest.mark.asyncio
async def test_analytics_income_calendar_bonds_before_stocks(client, portfolio_api_db):
    """Купоны (облигации) идут раньше дивидендов, даже если дивиденды больше по сумме."""
    payload = {
        "report_date": "2024-06-30",
        "snapshot": {"date": "2024-06-30", "total_balance": 500000},
        "positions": [],
        "flows": [
            {
                "date": "2024-04-15",
                "type": "dividend",
                "amount": 50000,
                "description": "Дивиденды: ПАО Сбербанк",
            },
            {
                "date": "2024-03-10",
                "type": "coupon",
                "amount": 2000,
                "description": "Купон: Минфин России",
            },
            {
                "date": "2024-05-20",
                "type": "redemption",
                "amount": 10000,
                "description": "Выкуп бумаг эмитентом, ПАО ИНГРАД",
            },
            {
                "date": "2024-06-01",
                "type": "dividend",
                "amount": 8000,
                "description": "Дивиденды: ПАО Полюс",
            },
        ],
    }
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    response = await client.get("/api/portfolios/1/analytics")
    assert response.status_code == 200
    names = [row["name"] for row in response.json()["monthly_cashflow"]["instruments"]]
    types = [row["type"] for row in response.json()["monthly_cashflow"]["instruments"]]

    assert names[0] == "Купон: Минфин России"
    assert names[1] == "Дивиденды: ПАО Сбербанк"
    assert names[2] == "Дивиденды: ПАО Полюс"
    assert names[3] == "Выкуп бумаг эмитентом, ПАО ИНГРАД"
    assert types == ["coupon", "dividend", "dividend", "redemption"]


@pytest.mark.asyncio
async def test_analytics_coupon_has_ofz_maturity(client, portfolio_api_db):
    payload = {
        "report_date": "2024-06-30",
        "snapshot": {"date": "2024-06-30", "total_balance": 500000},
        "positions": [],
        "flows": [
            {
                "date": "2024-03-10",
                "type": "coupon",
                "amount": 2000,
                "description": "Купон: ОФЗ 29",
            },
            {
                "date": "2024-04-10",
                "type": "coupon",
                "amount": 500,
                "description": "Купон: ОФЗ 29025",
            },
        ],
    }
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    response = await client.get("/api/portfolios/1/analytics")
    by_name = {
        row["name"]: row["maturity_date"]
        for row in response.json()["monthly_cashflow"]["instruments"]
    }
    assert by_name["Купон: ОФЗ 29"] == "2029-02-28"
    assert by_name["Купон: ОФЗ 29025"] == "2037-08-12"


@pytest.mark.asyncio
async def test_composition_after_import(client, portfolio_api_db):
    payload = _load_fixture("sample_import.json")
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    response = await client.get("/api/portfolios/1/composition")
    assert response.status_code == 200
    data = response.json()
    assert data["snapshot_date"] == "2025-07-31"
    assert len(data["positions"]) == 1
    assert data["positions"][0]["ticker"] == "TRNFP"
    assert data["positions"][0]["quantity"] == 50
    assert data["closed"] == []


@pytest.mark.asyncio
async def test_composition_keeps_last_positions_if_later_snapshot_empty(
    client, portfolio_api_db
):
    first = {
        "report_date": "2025-07-31",
        "snapshot": {"date": "2025-07-31", "total_balance": 850000},
        "positions": [
            {
                "ticker": "TRNFP",
                "name": "Транснефть (п)",
                "asset_type": "stock",
                "quantity": 50,
                "market_value": 72000,
                "avg_price": 1200,
            }
        ],
        "flows": [],
    }
    second = {
        "report_date": "2025-08-31",
        "snapshot": {"date": "2025-08-31", "total_balance": 800000},
        "positions": [],
        "flows": [],
    }
    await import_report(portfolio_api_db, 1, first)
    await import_report(portfolio_api_db, 1, second)
    await portfolio_api_db.commit()

    data = (await client.get("/api/portfolios/1/composition")).json()
    assert data["snapshot_date"] == "2025-07-31"
    assert data["positions"][0]["ticker"] == "TRNFP"
    assert data["closed"] == []


@pytest.mark.asyncio
async def test_closed_ingrad_redemption_unknown_cost_is_profit(client, portfolio_api_db):
    payload = _load_fixture("sample_sovcombank_import.json")
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    data = (await client.get("/api/portfolios/1/composition")).json()
    assert len(data["closed"]) == 1
    row = data["closed"][0]
    assert row["name"] == "ИНГРАД"
    assert row["cost"] is None
    assert row["exit"] == 1806.6
    assert row["income"] == 0
    assert row["result"] == 1806.6
    assert row["exit_kind"] == "buyback"
    assert row["closed_on"] == "2025-05-07"
    assert "ИНГРАД" not in {item["name"] for item in data["positions"]}


@pytest.mark.asyncio
async def test_closed_sale_with_known_cost(client, portfolio_api_db):
    first = {
        "report_date": "2025-07-31",
        "snapshot": {"date": "2025-07-31", "total_balance": 850000},
        "positions": [
            {
                "ticker": "TRNFP",
                "name": "Транснефть (п)",
                "asset_type": "stock",
                "quantity": 50,
                "market_value": 72000,
                "avg_price": 1200,
            }
        ],
        "flows": [
            {
                "date": "2025-07-15",
                "type": "dividend",
                "amount": 10200,
                "instrument": "TRNFP",
                "description": "Дивиденды TRNFP",
            }
        ],
    }
    second = {
        "report_date": "2025-08-31",
        "snapshot": {"date": "2025-08-31", "total_balance": 800000},
        "positions": [
            {
                "ticker": "SBER",
                "name": "Сбербанк",
                "asset_type": "stock",
                "quantity": 10,
                "market_value": 3000,
            }
        ],
        "flows": [
            {
                "date": "2025-08-10",
                "type": "sale",
                "amount": 75000,
                "instrument": "TRNFP",
                "description": "Продажа Транснефть (п)",
            }
        ],
    }
    await import_report(portfolio_api_db, 1, first)
    await import_report(portfolio_api_db, 1, second)
    await portfolio_api_db.commit()

    data = (await client.get("/api/portfolios/1/composition")).json()
    assert data["positions"][0]["ticker"] == "SBER"
    assert len(data["closed"]) == 1
    row = data["closed"][0]
    assert row["ticker"] == "TRNFP"
    assert row["cost"] == 60000
    assert row["income"] == 10200
    assert row["exit"] == 75000
    assert row["exit_kind"] == "sale"
    assert row["result"] == 25200


@pytest.mark.asyncio
async def test_composition_groups_bonds_before_stocks(client, portfolio_api_db):
    payload = _load_fixture("sample_sovcombank_import.json")
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    response = await client.get("/api/portfolios/1/composition")
    assert response.status_code == 200
    types = [row["asset_type"] for row in response.json()["positions"]]
    names = [row["name"] for row in response.json()["positions"]]
    assert types == ["bond", "stock", "stock"]
    assert names[0] == 'Облигации ФЗ перем. купон серия 29025RMFS'
    assert names[1] == 'Акции привилегированные ПАО Сбербанк'
    assert response.json()["positions"][0]["maturity_date"] == "2037-08-12"


@pytest.mark.asyncio
async def test_payments_drilldown(client, portfolio_api_db):
    payload = _load_fixture("sample_import.json")
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    response = await client.get(
        "/api/portfolios/1/payments",
        params={"instrument": "TRNFP", "year": 2025},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["payments"]) == 1
    assert data["payments"][0]["type"] == "dividend"
    assert data["payments"][0]["amount"] == 10200


@pytest.mark.asyncio
async def test_payments_unknown_instrument_is_empty_not_dump(client, portfolio_api_db):
    payload = _load_fixture("sample_sovcombank_import.json")
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    data = (
        await client.get(
            "/api/portfolios/1/payments",
            params={"instrument": "НесуществующаяБумагаXYZ"},
        )
    ).json()
    assert data["payments"] == []


@pytest.mark.asyncio
async def test_payments_filters_to_clicked_issuer(client, portfolio_api_db):
    payload = _load_fixture("sample_sovcombank_import.json")
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    clicked = 'Выплата дивидендов, ПАО "НОВАТЭК", ISIN: RU000A0DKVS5, 1 ЦБ=46.65RUB'
    data = (
        await client.get(
            "/api/portfolios/1/payments",
            params={"instrument": clicked},
        )
    ).json()
    assert data["payments"]
    blobs = " ".join(row["description"] or "" for row in data["payments"])
    assert "НОВАТЭК" in blobs
    assert "ИНГРАД" not in blobs
    assert "Минфин" not in blobs
    assert "Медикал" not in blobs


@pytest.mark.asyncio
async def test_goal_analytics_backward_compat(client, portfolio_api_db):
    payload = _load_fixture("sample_import.json")
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    response = await client.get("/api/goals/1/analytics")
    assert response.status_code == 200
    data = response.json()
    assert data["goal_id"] == 1
    assert data["portfolio_id"] == 1
    assert data["monthly_cashflow"]["summary"]["2025-07"]["dividends"] == 10200


@pytest.mark.asyncio
async def test_unknown_portfolio_404(client, portfolio_api_db):
    response = await client.get("/api/portfolios/99/analytics")
    assert response.status_code == 404
