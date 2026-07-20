from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.db.database import async_session
from app.services.portfolio_service import (
    InvalidImportPayloadError,
    PortfolioNotFoundError,
    build_portfolio_analytics,
    build_portfolio_composition,
    build_portfolio_payments,
    import_report,
    list_portfolios,
)
from app.web.deps import templates

router = APIRouter()


@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, tab: str = "iis"):
    """SSR shell for portfolio analyzer (full UI in T4)."""
    async with async_session() as db:
        portfolios = await list_portfolios(db)
        active = next((p for p in portfolios if p["slug"] == tab), None)
        if active is None and portfolios:
            active = portfolios[0]
            tab = active["slug"]

    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "portfolios": portfolios,
            "active_tab": tab,
            "active_portfolio": active,
        },
    )


@router.get("/api/portfolios")
async def get_portfolios():
    async with async_session() as db:
        return JSONResponse(await list_portfolios(db))


@router.get("/api/portfolios/{portfolio_id}/analytics")
async def get_portfolio_analytics(portfolio_id: int):
    async with async_session() as db:
        try:
            data = await build_portfolio_analytics(db, portfolio_id)
        except PortfolioNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(data)


@router.get("/api/portfolios/{portfolio_id}/composition")
async def get_portfolio_composition(portfolio_id: int):
    async with async_session() as db:
        try:
            data = await build_portfolio_composition(db, portfolio_id)
        except PortfolioNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(data)


@router.get("/api/portfolios/{portfolio_id}/payments")
async def get_portfolio_payments(
    portfolio_id: int,
    instrument: str | None = None,
    year: int | None = None,
):
    async with async_session() as db:
        try:
            data = await build_portfolio_payments(
                db,
                portfolio_id,
                instrument=instrument,
                year=year,
            )
        except PortfolioNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(data)


@router.post("/api/portfolios/{portfolio_id}/import")
async def post_portfolio_import(portfolio_id: int, payload: dict[str, Any]):
    async with async_session() as db:
        try:
            result = await import_report(db, portfolio_id, payload)
            await db.commit()
        except PortfolioNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except InvalidImportPayloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if result.duplicate:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": True,
                    "import_id": result.import_id,
                    "positions_upserted": result.positions_upserted,
                    "flows_inserted": result.flows_inserted,
                    "flows_skipped_duplicate": result.flows_skipped_duplicate,
                    "duplicate": True,
                },
            )

        return JSONResponse(
            {
                "ok": True,
                "import_id": result.import_id,
                "positions_upserted": result.positions_upserted,
                "flows_inserted": result.flows_inserted,
                "flows_skipped_duplicate": result.flows_skipped_duplicate,
            }
        )
