import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db.seed_portfolios import seed_portfolios
from app.models.goal import FinancialGoal
from app.models.investment import InvestmentFlow, InvestmentSnapshot
from app.models.portfolio import ImportLog, Instrument, Portfolio, PortfolioGoal, Position
from app.services.portfolio_service import import_report

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
async def test_cashflow_hides_exited_instruments(client, portfolio_api_db):
    """В календаре active=false у бумаг, которых нет в текущем составе."""
    payload = {
        "report_date": "2025-07-31",
        "snapshot": {"date": "2025-07-31", "total_balance": 850000},
        "positions": [
            {
                "ticker": "TRNFP",
                "name": "Транснефть (п)",
                "asset_type": "stock",
                "quantity": 50,
                "market_value": 72000,
            }
        ],
        "flows": [
            {
                "date": "2025-07-15",
                "type": "dividend",
                "amount": 10200,
                "instrument": "TRNFP",
                "description": "Дивиденды TRNFP",
            },
            {
                "date": "2025-06-01",
                "type": "coupon",
                "amount": 3400,
                "description": 'Купон: ООО "Интерлизинг"',
            },
            {
                "date": "2025-05-10",
                "type": "redemption",
                "amount": 10000,
                "description": "Погашение: Старый бонд",
            },
            {
                "date": "2025-07-20",
                "type": "pif_accrual",
                "amount": 800,
                "description": "Начисления БПИФ Альфа",
            },
        ],
    }
    await import_report(portfolio_api_db, 1, payload)
    await portfolio_api_db.commit()

    response = await client.get("/api/portfolios/1/analytics")
    assert response.status_code == 200
    by_name = {
        row["name"]: row
        for row in response.json()["monthly_cashflow"]["instruments"]
    }
    assert by_name["Дивиденды TRNFP"]["active"] is True
    assert by_name["Дивиденды TRNFP"]["asset_type"] == "stock"
    assert by_name['Купон: ООО "Интерлизинг"']["active"] is False
    assert by_name['Купон: ООО "Интерлизинг"']["asset_type"] == "bond"
    assert by_name["Погашение: Старый бонд"]["active"] is False
    assert by_name["Погашение: Старый бонд"]["asset_type"] == "bond"
    assert by_name["Начисления БПИФ Альфа"]["active"] is False
    assert by_name["Начисления БПИФ Альфа"]["asset_type"] == "pif"


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
