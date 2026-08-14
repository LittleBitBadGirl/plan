"""SSR smoke tests for /portfolio page (T4 UI)."""

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db.seed_portfolios import seed_portfolios
from app.models.goal import FinancialGoal
from app.models.investment import InvestmentFlow, InvestmentSnapshot
from app.models.portfolio import ImportLog, Instrument, Portfolio, PortfolioGoal, Position

pytestmark = pytest.mark.asyncio


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
async def portfolio_page_db(db):
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


async def test_portfolio_page_returns_200(client, portfolio_page_db):
    response = await client.get("/portfolio")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Портфель" in response.text


async def test_portfolio_page_has_four_tabs(client, portfolio_page_db):
    response = await client.get("/portfolio")
    html = response.text
    for slug, name in [
        ("iis", "ИИС"),
        ("podushka", "Подушка"),
        ("broker-1", "Брокерский 1"),
        ("broker-2", "Брокерский 2"),
    ]:
        assert f'/portfolio?tab={slug}' in html
        assert name in html


async def test_portfolio_page_active_tab_highlight(client, portfolio_page_db):
    response = await client.get("/portfolio?tab=broker-1")
    assert response.status_code == 200
    assert "bg-yellow-600/20" in response.text
    assert "Брокерский 1" in response.text


async def test_portfolio_page_goal_progress(client, portfolio_page_db):
    response = await client.get("/portfolio?tab=broker-1")
    assert "Цель: Автомобиль" in response.text
    assert 'id="portfolioGoals"' in response.text


async def test_portfolio_page_ui_shell_elements(client, portfolio_page_db):
    response = await client.get("/portfolio?tab=iis")
    html = response.text
    assert 'id="portfolioRoot"' in html
    assert 'data-portfolio-id="' in html
    assert 'id="paKpi"' in html
    assert 'id="paCashflow"' in html
    assert 'id="paComposition"' in html
    assert 'id="paClosed"' in html
    assert 'id="paDrilldown"' in html
    assert "/web/static/js/portfolio-analytics.js" in html


async def test_sidebar_portfolio_link(client, portfolio_page_db):
    response = await client.get("/portfolio")
    html = response.text
    assert 'href="/portfolio"' in html
    assert ">Портфель</span>" in html or ">Портфель<" in html
