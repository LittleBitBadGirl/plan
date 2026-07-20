"""Idempotent seed for portfolio analyzer accounts."""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goal import FinancialGoal
from app.models.portfolio import Portfolio, PortfolioGoal

PORTFOLIO_SEED = [
    {
        "id": 1,
        "name": "ИИС",
        "slug": "iis",
        "type": "iis",
        "legacy_goal_id": 1,
        "sort_order": 1,
        "broker_contract": "9248208",
    },
    {
        "id": 2,
        "name": "Подушка",
        "slug": "podushka",
        "type": "reserve",
        "legacy_goal_id": 3,
        "sort_order": 2,
        "broker_contract": "1226101/21-л",
    },
    {
        "id": 3,
        "name": "Брокерский 1",
        "slug": "broker-1",
        "type": "broker",
        "legacy_goal_id": 6,
        "sort_order": 3,
        "broker_contract": "1149213",
    },
    {
        "id": 4,
        "name": "Брокерский 2",
        "slug": "broker-2",
        "type": "broker",
        "legacy_goal_id": 7,
        "sort_order": 4,
        "broker_contract": None,
    },
]


async def seed_portfolios(db: AsyncSession) -> None:
    """Create 4 portfolios, rename legacy goal 6, add «Автомобиль» portfolio goal."""
    existing = (await db.execute(select(Portfolio).limit(1))).scalar_one_or_none()
    if existing:
        return

    for row in PORTFOLIO_SEED:
        db.add(Portfolio(**row))
    await db.flush()

    legacy_goal = (
        await db.execute(select(FinancialGoal).where(FinancialGoal.id == 6))
    ).scalar_one_or_none()
    if legacy_goal and legacy_goal.name != "Брокерский 1":
        legacy_goal.name = "Брокерский 1"

    if legacy_goal:
        db.add(
            PortfolioGoal(
                portfolio_id=3,
                name="Автомобиль",
                target_amount=legacy_goal.target_amount,
                current_amount=legacy_goal.current_amount,
            )
        )

    await db.execute(
        text(
            """
            UPDATE investment_snapshots
            SET portfolio_id = (
                SELECT p.id FROM portfolios p
                WHERE p.legacy_goal_id = investment_snapshots.goal_id
            )
            WHERE portfolio_id IS NULL AND goal_id IS NOT NULL
            """
        )
    )
    await db.execute(
        text(
            """
            UPDATE investment_flows
            SET portfolio_id = (
                SELECT p.id FROM portfolios p
                WHERE p.legacy_goal_id = investment_flows.goal_id
            )
            WHERE portfolio_id IS NULL AND goal_id IS NOT NULL
            """
        )
    )
