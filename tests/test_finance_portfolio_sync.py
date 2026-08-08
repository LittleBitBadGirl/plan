"""Finance ↔ portfolio balance write-through."""

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.seed_portfolios import seed_portfolios
from app.models.goal import FinancialGoal
from app.models.goal_history import GoalHistory
from app.models.investment import InvestmentFlow, InvestmentSnapshot
from app.models.portfolio import ImportLog, Instrument, Portfolio, PortfolioGoal, Position
from app.services.portfolio_service import import_report


async def _reset_portfolio_tables(db) -> None:
    await db.execute(delete(InvestmentFlow))
    await db.execute(delete(InvestmentSnapshot))
    await db.execute(delete(Position))
    await db.execute(delete(ImportLog))
    await db.execute(delete(Instrument))
    await db.execute(delete(PortfolioGoal))
    await db.execute(delete(Portfolio))
    await db.execute(delete(GoalHistory))
    await db.execute(delete(FinancialGoal))
    await db.commit()


@pytest_asyncio.fixture
async def synced_portfolio_db(db):
    await _reset_portfolio_tables(db)
    db.add_all(
        [
            FinancialGoal(
                id=1,
                name="ИИС",
                target_amount=10_000_000,
                current_amount=500_000,
            ),
            FinancialGoal(
                id=6,
                name="Брокерский 1",
                target_amount=1_500_000,
                current_amount=500_000,
            ),
            FinancialGoal(
                id=99,
                name="Зимовка",
                target_amount=200_000,
                current_amount=20_000,
            ),
        ]
    )
    await db.flush()
    await seed_portfolios(db)
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_goal_update_writes_snapshot_and_portfolio_goal(client, synced_portfolio_db):
    response = await client.post(
        "/api/goals/6/update",
        data={"new_amount": 547100},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["new_amount"] == 547100

    synced_portfolio_db.expire_all()
    today = date.today()
    snapshot = (
        await synced_portfolio_db.execute(
            select(InvestmentSnapshot).where(
                InvestmentSnapshot.portfolio_id == 3,
                InvestmentSnapshot.date == today,
            )
        )
    ).scalar_one()
    assert snapshot.total_balance == 547100
    assert snapshot.goal_id == 6

    pg = (
        await synced_portfolio_db.execute(
            select(PortfolioGoal).where(PortfolioGoal.portfolio_id == 3)
        )
    ).scalar_one()
    assert pg.current_amount == 547100

    goal = (
        await synced_portfolio_db.execute(
            select(FinancialGoal).where(FinancialGoal.id == 6)
        )
    ).scalar_one()
    assert goal.current_amount == 547100

    hist = (
        await synced_portfolio_db.execute(
            select(GoalHistory).where(GoalHistory.goal_id == 6)
        )
    ).scalar_one()
    assert hist.new_amount == 547100
    assert hist.delta == 47100


@pytest.mark.asyncio
async def test_goal_update_without_portfolio_keeps_history_only(client, synced_portfolio_db):
    response = await client.post(
        "/api/goals/99/update",
        data={"new_amount": 25_000},
    )
    assert response.status_code == 200

    synced_portfolio_db.expire_all()
    snap_count = (
        await synced_portfolio_db.execute(select(InvestmentSnapshot))
    ).scalars().all()
    assert snap_count == []

    goal = (
        await synced_portfolio_db.execute(
            select(FinancialGoal).where(FinancialGoal.id == 99)
        )
    ).scalar_one()
    assert goal.current_amount == 25_000

    hist = (
        await synced_portfolio_db.execute(
            select(GoalHistory).where(GoalHistory.goal_id == 99)
        )
    ).scalar_one()
    assert hist.delta == 5_000


@pytest.mark.asyncio
async def test_goal_update_upserts_same_day_snapshot(client, synced_portfolio_db):
    await client.post("/api/goals/1/update", data={"new_amount": 590_000})
    await client.post("/api/goals/1/update", data={"new_amount": 594_000})

    synced_portfolio_db.expire_all()
    snaps = (
        await synced_portfolio_db.execute(
            select(InvestmentSnapshot).where(InvestmentSnapshot.portfolio_id == 1)
        )
    ).scalars().all()
    assert len(snaps) == 1
    assert snaps[0].total_balance == 594_000
    assert snaps[0].date == date.today()


@pytest.mark.asyncio
async def test_import_report_updates_financial_goal(synced_portfolio_db):
    payload = {
        "report_date": "2025-07-31",
        "snapshot": {"date": "2025-07-31", "total_balance": 850_000},
        "positions": [],
        "flows": [],
    }
    result = await import_report(synced_portfolio_db, 1, payload)
    await synced_portfolio_db.commit()

    assert result.ok is True
    goal = (
        await synced_portfolio_db.execute(
            select(FinancialGoal).where(FinancialGoal.id == 1)
        )
    ).scalar_one()
    assert goal.current_amount == 850_000

    # Import must not spam goal_history
    hist = (
        await synced_portfolio_db.execute(
            select(GoalHistory).where(GoalHistory.goal_id == 1)
        )
    ).scalars().all()
    assert hist == []
