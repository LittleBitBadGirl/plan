import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.db.seed_portfolios import seed_portfolios
from app.models.goal import FinancialGoal
from app.models.investment import InvestmentFlow, InvestmentSnapshot
from app.models.portfolio import ImportLog, Instrument, Portfolio, PortfolioGoal, Position
from app.services.instrument_normalize import resolve_instrument
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
async def portfolio_db(db):
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
async def test_import_sample_json(portfolio_db):
    payload = _load_fixture("sample_import.json")
    result = await import_report(portfolio_db, 1, payload)
    await portfolio_db.commit()

    assert result.ok is True
    assert result.duplicate is False
    assert result.positions_upserted == 1
    assert result.flows_inserted == 1

    snapshot = (
        await portfolio_db.execute(
            select(InvestmentSnapshot).where(
                InvestmentSnapshot.portfolio_id == 1,
                InvestmentSnapshot.date == payload["snapshot"]["date"],
            )
        )
    ).scalar_one()
    assert snapshot.total_balance == payload["snapshot"]["total_balance"]
    assert snapshot.goal_id == 1

    flow = (
        await portfolio_db.execute(
            select(InvestmentFlow).where(InvestmentFlow.portfolio_id == 1)
        )
    ).scalar_one()
    assert flow.type == "dividend"
    assert flow.amount == 10200
    assert flow.description == "Дивиденды TRNFP"

    position = (
        await portfolio_db.execute(select(Position).where(Position.portfolio_id == 1))
    ).scalar_one()
    assert position.quantity == 50
    assert position.market_value == 72000


@pytest.mark.asyncio
async def test_duplicate_import_skips_flows(portfolio_db):
    payload = _load_fixture("sample_import.json")

    first = await import_report(portfolio_db, 1, payload)
    await portfolio_db.commit()
    assert first.flows_inserted == 1

    second = await import_report(portfolio_db, 1, payload)
    await portfolio_db.commit()
    assert second.duplicate is True
    assert second.flows_inserted == 0
    assert second.import_id == first.import_id

    flow_count = (
        await portfolio_db.execute(
            select(func.count()).select_from(InvestmentFlow).where(
                InvestmentFlow.portfolio_id == 1
            )
        )
    ).scalar_one()
    assert flow_count == 1

    import_count = (
        await portfolio_db.execute(
            select(func.count()).select_from(ImportLog).where(ImportLog.portfolio_id == 1)
        )
    ).scalar_one()
    assert import_count == 1


@pytest.mark.asyncio
async def test_duplicate_flows_skipped_on_changed_payload(portfolio_db):
    payload = _load_fixture("sample_import.json")
    await import_report(portfolio_db, 1, payload)
    await portfolio_db.commit()

    changed = dict(payload)
    changed["report_date"] = "2025-08-31"
    changed["snapshot"] = {"date": "2025-08-31", "total_balance": 860000}
    result = await import_report(portfolio_db, 1, changed)
    await portfolio_db.commit()

    assert result.duplicate is False
    assert result.flows_inserted == 0
    assert result.flows_skipped_duplicate == 1

    flow_count = (
        await portfolio_db.execute(
            select(func.count()).select_from(InvestmentFlow).where(
                InvestmentFlow.portfolio_id == 1
            )
        )
    ).scalar_one()
    assert flow_count == 1


@pytest.mark.asyncio
async def test_trnfp_and_transneft_name_same_instrument(portfolio_db):
    payload = _load_fixture("sample_import.json")
    await import_report(portfolio_db, 1, payload)
    await portfolio_db.commit()

    matched = await resolve_instrument(
        portfolio_db,
        name="Транснефть (п)",
    )
    await portfolio_db.commit()

    instrument = (
        await portfolio_db.execute(select(Instrument).where(Instrument.ticker == "TRNFP"))
    ).scalar_one()
    assert matched.id == instrument.id

    count = (
        await portfolio_db.execute(select(func.count()).select_from(Instrument))
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_pif_accrual_flow_type(portfolio_db):
    payload = {
        "report_date": "2025-07-31",
        "snapshot": {"date": "2025-07-31", "total_balance": 500000},
        "positions": [
            {
                "ticker": None,
                "name": "ПИФ Денежный рынок",
                "asset_type": "pif",
                "quantity": 1234.56,
                "market_value": 500000,
            }
        ],
        "flows": [
            {
                "date": "2025-06-28",
                "type": "pif_accrual",
                "amount": 3200,
                "instrument": None,
                "description": "ПИФ Денежный рынок — начисление",
            }
        ],
    }

    portfolio = (
        await portfolio_db.execute(select(Portfolio).where(Portfolio.slug == "podushka"))
    ).scalar_one()

    result = await import_report(portfolio_db, portfolio.id, payload)
    await portfolio_db.commit()

    assert result.flows_inserted == 1

    flow = (
        await portfolio_db.execute(
            select(InvestmentFlow).where(
                InvestmentFlow.portfolio_id == portfolio.id,
                InvestmentFlow.type == "pif_accrual",
            )
        )
    ).scalar_one()
    assert flow.amount == 3200
    assert flow.description == "ПИФ Денежный рынок — начисление"


@pytest.mark.asyncio
async def test_sovcombank_fixture_import(portfolio_db):
    payload = _load_fixture("sample_sovcombank_import.json")
    result = await import_report(portfolio_db, 1, payload)
    await portfolio_db.commit()

    assert result.positions_upserted == 3
    assert result.flows_inserted == 5

    instruments = (
        await portfolio_db.execute(select(func.count()).select_from(Instrument))
    ).scalar_one()
    # 3 from positions + 2 flow-only ISINs (MD Medical, INGRAD redemption)
    assert instruments == 5
