"""Portfolio import and analytics helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.goal import FinancialGoal
from app.models.investment import InvestmentFlow, InvestmentSnapshot
from app.models.portfolio import ImportLog, Instrument, Portfolio, PortfolioGoal, Position
from app.services.instrument_normalize import resolve_instrument
from app.services.ofz_calendar import effective_bond_maturity

VALID_FLOW_TYPES = {
    "deposit",
    "withdrawal",
    "coupon",
    "dividend",
    "tax",
    "commission",
    "redemption",
    "pif_accrual",
    "sale",
}


class PortfolioNotFoundError(ValueError):
    """Raised when portfolio_id does not exist."""


class InvalidImportPayloadError(ValueError):
    """Raised when import payload is missing required fields."""


@dataclass
class ImportResult:
    ok: bool
    import_id: int
    positions_upserted: int
    flows_inserted: int
    flows_skipped_duplicate: int
    duplicate: bool = False


def _parse_date(value: str | date, field_name: str) -> date:
    if isinstance(value, date):
        return value
    if not value:
        raise InvalidImportPayloadError(f"{field_name} is required")
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise InvalidImportPayloadError(f"Invalid date for {field_name}: {value}") from exc


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _flow_description(description: str | None) -> str:
    return (description or "").strip()


async def _get_portfolio(db: AsyncSession, portfolio_id: int) -> Portfolio:
    portfolio = (
        await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    ).scalar_one_or_none()
    if portfolio is None:
        raise PortfolioNotFoundError(f"Portfolio {portfolio_id} not found")
    return portfolio


async def _find_duplicate_import(
    db: AsyncSession,
    portfolio_id: int,
    report_date: date,
    payload_hash: str,
) -> ImportLog | None:
    result = await db.execute(
        select(ImportLog).where(
            ImportLog.portfolio_id == portfolio_id,
            ImportLog.report_date == report_date,
            ImportLog.payload_hash == payload_hash,
            ImportLog.status == "ok",
        )
    )
    return result.scalar_one_or_none()


async def _upsert_snapshot(
    db: AsyncSession,
    portfolio: Portfolio,
    snapshot_date: date,
    total_balance: float,
) -> None:
    existing = (
        await db.execute(
            select(InvestmentSnapshot).where(
                InvestmentSnapshot.portfolio_id == portfolio.id,
                InvestmentSnapshot.date == snapshot_date,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.total_balance = total_balance
        existing.goal_id = portfolio.legacy_goal_id
        return

    db.add(
        InvestmentSnapshot(
            portfolio_id=portfolio.id,
            goal_id=portfolio.legacy_goal_id,
            date=snapshot_date,
            total_balance=total_balance,
        )
    )


async def _sync_portfolio_goal_amounts(
    db: AsyncSession,
    portfolio_id: int,
    total_balance: float,
) -> None:
    """Keep portfolio_goals.current_amount aligned with NAV."""
    goals = (
        await db.execute(
            select(PortfolioGoal).where(PortfolioGoal.portfolio_id == portfolio_id)
        )
    ).scalars().all()
    for goal in goals:
        goal.current_amount = total_balance


async def sync_manual_balance_to_portfolio(
    db: AsyncSession,
    goal_id: int,
    new_amount: float,
    *,
    as_of: date | None = None,
) -> Portfolio | None:
    """Write-through from /finance goal edit into portfolio analytics tables.

    Upserts today's (or as_of) InvestmentSnapshot and syncs PortfolioGoal rows.
    No-op when the goal is not linked to a Portfolio via legacy_goal_id.
    """
    portfolio = await get_portfolio_by_legacy_goal_id(db, goal_id)
    if portfolio is None:
        return None

    snapshot_date = as_of or date.today()
    await _upsert_snapshot(db, portfolio, snapshot_date, float(new_amount))
    await _sync_portfolio_goal_amounts(db, portfolio.id, float(new_amount))
    return portfolio


async def sync_snapshot_to_goals(
    db: AsyncSession,
    portfolio: Portfolio,
    total_balance: float,
) -> None:
    """Reverse sync: Hermes snapshot → financial_goals + portfolio_goals.

    Does not write GoalHistory (import is bulk/authoritative; history stays
    for manual edits on /finance).
    """
    amount = float(total_balance)
    if portfolio.legacy_goal_id is not None:
        goal = (
            await db.execute(
                select(FinancialGoal).where(FinancialGoal.id == portfolio.legacy_goal_id)
            )
        ).scalar_one_or_none()
        if goal is not None:
            goal.current_amount = amount

    await _sync_portfolio_goal_amounts(db, portfolio.id, amount)


async def _upsert_position(
    db: AsyncSession,
    portfolio_id: int,
    instrument_id: int,
    snapshot_date: date,
    quantity: float,
    market_value: float,
    avg_price: float | None,
    weight_pct: float | None,
) -> bool:
    existing = (
        await db.execute(
            select(Position).where(
                Position.portfolio_id == portfolio_id,
                Position.instrument_id == instrument_id,
                Position.snapshot_date == snapshot_date,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.quantity = quantity
        existing.market_value = market_value
        existing.avg_price = avg_price
        existing.weight_pct = weight_pct
        return True

    db.add(
        Position(
            portfolio_id=portfolio_id,
            instrument_id=instrument_id,
            snapshot_date=snapshot_date,
            quantity=quantity,
            avg_price=avg_price,
            market_value=market_value,
            weight_pct=weight_pct,
        )
    )
    return True


async def _flow_exists(
    db: AsyncSession,
    portfolio_id: int,
    flow_date: date,
    flow_type: str,
    amount: float,
    description: str,
) -> bool:
    result = await db.execute(
        select(InvestmentFlow.id).where(
            InvestmentFlow.portfolio_id == portfolio_id,
            InvestmentFlow.date == flow_date,
            InvestmentFlow.type == flow_type,
            InvestmentFlow.amount == amount,
            InvestmentFlow.description == description,
        )
    )
    return result.scalar_one_or_none() is not None


async def import_report(
    db: AsyncSession,
    portfolio_id: int,
    payload: dict[str, Any],
    *,
    source: str = "hermes",
) -> ImportResult:
    """Import Hermes broker report JSON into portfolio tables."""
    portfolio = await _get_portfolio(db, portfolio_id)

    report_date = _parse_date(payload.get("report_date"), "report_date")
    snapshot_payload = payload.get("snapshot") or {}
    if not snapshot_payload:
        raise InvalidImportPayloadError("snapshot is required")

    snapshot_date = _parse_date(snapshot_payload.get("date"), "snapshot.date")
    total_balance = float(snapshot_payload["total_balance"])
    digest = _payload_hash(payload)

    duplicate = await _find_duplicate_import(db, portfolio_id, report_date, digest)
    if duplicate:
        return ImportResult(
            ok=True,
            import_id=duplicate.id,
            positions_upserted=0,
            flows_inserted=0,
            flows_skipped_duplicate=0,
            duplicate=True,
        )

    await _upsert_snapshot(db, portfolio, snapshot_date, total_balance)
    await sync_snapshot_to_goals(db, portfolio, total_balance)

    positions_payload = payload.get("positions") or []
    total_market_value = sum(float(item.get("market_value") or 0) for item in positions_payload)
    positions_upserted = 0

    for item in positions_payload:
        name = item.get("name") or item.get("ticker") or item.get("isin")
        if not name:
            continue

        maturity_raw = item.get("maturity_date")
        maturity_date = _parse_date(maturity_raw, "positions.maturity_date") if maturity_raw else None
        instrument = await resolve_instrument(
            db,
            name=str(name),
            ticker=item.get("ticker"),
            isin=item.get("isin"),
            asset_type=item.get("asset_type") or "other",
            maturity_date=maturity_date,
            coupon_rate=item.get("coupon_rate"),
        )
        market_value = float(item["market_value"])
        weight_pct = (
            (market_value / total_market_value) * 100.0 if total_market_value else None
        )
        await _upsert_position(
            db,
            portfolio_id=portfolio.id,
            instrument_id=instrument.id,
            snapshot_date=snapshot_date,
            quantity=float(item["quantity"]),
            market_value=market_value,
            avg_price=item.get("avg_price"),
            weight_pct=weight_pct,
        )
        positions_upserted += 1

    flows_inserted = 0
    flows_skipped_duplicate = 0

    for item in payload.get("flows") or []:
        flow_type = item.get("type")
        if flow_type not in VALID_FLOW_TYPES:
            raise InvalidImportPayloadError(f"Unsupported flow type: {flow_type}")

        flow_date = _parse_date(item.get("date"), "flows.date")
        amount = float(item["amount"])
        description = _flow_description(item.get("description"))

        if await _flow_exists(db, portfolio_id, flow_date, flow_type, amount, description):
            flows_skipped_duplicate += 1
            continue

        instrument_name = item.get("name")
        if not instrument_name and item.get("instrument"):
            instrument_name = str(item["instrument"])
        if not instrument_name and item.get("isin"):
            instrument_name = str(item["isin"])

        if instrument_name or item.get("ticker") or item.get("isin") or item.get("instrument"):
            await resolve_instrument(
                db,
                name=str(instrument_name or item.get("instrument") or item.get("isin") or "Unknown"),
                ticker=item.get("ticker"),
                isin=item.get("isin"),
                instrument_ref=item.get("instrument"),
                asset_type=item.get("asset_type") or "other",
            )

        db.add(
            InvestmentFlow(
                portfolio_id=portfolio.id,
                goal_id=portfolio.legacy_goal_id,
                date=flow_date,
                type=flow_type,
                amount=amount,
                description=description,
            )
        )
        flows_inserted += 1

    import_log = ImportLog(
        portfolio_id=portfolio.id,
        report_date=report_date,
        source=source,
        payload_hash=digest,
        status="ok",
        created_at=datetime.utcnow(),
    )
    db.add(import_log)
    await db.flush()

    return ImportResult(
        ok=True,
        import_id=import_log.id,
        positions_upserted=positions_upserted,
        flows_inserted=flows_inserted,
        flows_skipped_duplicate=flows_skipped_duplicate,
    )


CASHFLOW_FLOW_TYPES = (
    "coupon",
    "dividend",
    "deposit",
    "withdrawal",
    "tax",
    "pif_accrual",
    "redemption",
)

INCOME_FLOW_TYPES = ("coupon", "dividend", "pif_accrual", "redemption")

# Облигации → акции → прочее. Внутри группы — по сумме, затем по имени.
INCOME_TYPE_SORT_ORDER = {
    "coupon": 0,
    "dividend": 1,
    "pif_accrual": 2,
    "redemption": 3,
}

SUMMARY_TYPE_MAP = {
    "deposit": "deposits",
    "withdrawal": "withdrawals",
    "coupon": "coupons",
    "dividend": "dividends",
    "tax": "taxes",
}


def primary_income_type(type_amounts: dict[str, int]) -> str:
    """Тип с наибольшей суммой; при равенстве — более ранний в INCOME_TYPE_SORT_ORDER."""
    if not type_amounts:
        return "other"
    return max(
        type_amounts,
        key=lambda flow_type: (
            type_amounts[flow_type],
            -INCOME_TYPE_SORT_ORDER.get(flow_type, 9),
        ),
    )


def sort_cashflow_instruments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Облигации (купоны), затем акции (дивиденды), затем ПИФ/погашения."""
    return sorted(
        items,
        key=lambda item: (
            INCOME_TYPE_SORT_ORDER.get(item.get("type") or "", 9),
            -(item.get("total") or 0),
            item.get("name") or "",
        ),
    )


COMPOSITION_TYPE_SORT_ORDER = {
    "bond": 0,
    "stock": 1,
    "pif": 2,
}


def effective_composition_type(
    asset_type: str | None,
    *,
    maturity_date: date | str | None = None,
    coupon_rate: float | None = None,
    name: str | None = None,
) -> str:
    """Тип для группировки состава: bond / stock / pif / other."""
    known = (asset_type or "").strip().lower()
    if known in COMPOSITION_TYPE_SORT_ORDER:
        return known
    if maturity_date is not None or coupon_rate is not None:
        return "bond"
    lowered = (name or "").lower()
    if "пиф" in lowered:
        return "pif"
    if "облигац" in lowered or "офз" in lowered:
        return "bond"
    if "акци" in lowered:
        return "stock"
    return known or "other"


def sort_composition_positions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Облигации, затем акции, затем ПИФ. Внутри группы — по доле."""
    return sorted(
        items,
        key=lambda item: (
            COMPOSITION_TYPE_SORT_ORDER.get(item.get("asset_type") or "", 9),
            -(item.get("weight_pct") or 0),
            -(item.get("market_value") or 0),
            item.get("name") or "",
        ),
    )


CLOSED_INCOME_TYPES = ("coupon", "dividend", "pif_accrual", "tax")
CLOSED_EXIT_TYPES = ("redemption", "sale")


def estimate_position_cost(
    quantity: float,
    avg_price: float | None,
    market_value: float | None = None,
) -> float | None:
    """Стоимость покупки. Если avg_price похожа на % от номинала 1000 — переводим в рубли."""
    if not quantity or avg_price is None:
        return None
    naive = avg_price * quantity
    if market_value and naive > 0 and naive * 4 < market_value:
        return round(avg_price / 100.0 * 1000.0 * quantity, 2)
    return round(naive, 2)


def _empty_summary_row() -> dict[str, int]:
    return {"deposits": 0, "withdrawals": 0, "coupons": 0, "dividends": 0, "taxes": 0}


async def get_portfolio_by_legacy_goal_id(
    db: AsyncSession,
    goal_id: int,
) -> Portfolio | None:
    result = await db.execute(
        select(Portfolio).where(Portfolio.legacy_goal_id == goal_id)
    )
    return result.scalar_one_or_none()


async def list_portfolios(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Portfolio)
        .options(selectinload(Portfolio.goals))
        .order_by(Portfolio.sort_order.asc(), Portfolio.id.asc())
    )
    portfolios = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "slug": p.slug,
            "type": p.type,
            "legacy_goal_id": p.legacy_goal_id,
            "broker_contract": p.broker_contract,
            "goals": [
                {
                    "id": g.id,
                    "name": g.name,
                    "target_amount": g.target_amount,
                    "current_amount": g.current_amount,
                }
                for g in p.goals
            ],
        }
        for p in portfolios
    ]


async def build_portfolio_analytics(
    db: AsyncSession,
    portfolio_id: int,
) -> dict[str, Any]:
    portfolio = await _get_portfolio(db, portfolio_id)

    snap_res = await db.execute(
        select(InvestmentSnapshot.date, InvestmentSnapshot.total_balance)
        .where(InvestmentSnapshot.portfolio_id == portfolio_id)
        .order_by(InvestmentSnapshot.date.asc())
    )
    snapshots = [
        {"date": str(row.date), "balance": row.total_balance}
        for row in snap_res
    ]

    flow_res = await db.execute(
        select(
            InvestmentFlow.date,
            InvestmentFlow.type,
            InvestmentFlow.amount,
            InvestmentFlow.description,
        )
        .where(InvestmentFlow.portfolio_id == portfolio_id)
        .order_by(InvestmentFlow.date.asc())
    )
    flows = [
        {
            "date": str(row.date),
            "type": row.type,
            "amount": row.amount,
            "description": row.description,
        }
        for row in flow_res
    ]

    totals: dict[str, float] = {}
    for flow in flows:
        totals[flow["type"]] = totals.get(flow["type"], 0) + flow["amount"]

    cashflow_res = await db.execute(
        select(
            func.strftime("%Y", InvestmentFlow.date).label("year"),
            func.strftime("%m", InvestmentFlow.date).label("month"),
            InvestmentFlow.type,
            InvestmentFlow.description,
            func.sum(InvestmentFlow.amount).label("total"),
        )
        .where(
            InvestmentFlow.portfolio_id == portfolio_id,
            InvestmentFlow.type.in_(CASHFLOW_FLOW_TYPES),
        )
        .group_by("year", "month", InvestmentFlow.type, InvestmentFlow.description)
        .order_by("year", "month")
    )

    instruments: dict[str, dict[str, int]] = {}
    instrument_types: dict[str, dict[str, int]] = {}
    summary: dict[str, dict[str, int]] = {}

    for row in cashflow_res:
        ym = f"{row.year}-{row.month}"
        amt = round(row.total)
        if ym not in summary:
            summary[ym] = _empty_summary_row()
        summary_key = SUMMARY_TYPE_MAP.get(row.type)
        if summary_key:
            summary[ym][summary_key] += amt
        if row.type in INCOME_FLOW_TYPES and row.description:
            name = row.description
            instruments.setdefault(name, {})
            instruments[name][ym] = instruments[name].get(ym, 0) + amt
            instrument_types.setdefault(name, {})
            instrument_types[name][row.type] = (
                instrument_types[name].get(row.type, 0) + amt
            )

    all_db_instruments = list((await db.execute(select(Instrument))).scalars().all())

    sorted_instruments = sort_cashflow_instruments(
        [
            {
                "name": name,
                "type": primary_income_type(instrument_types.get(name, {})),
                "months": months,
                "total": sum(months.values()),
                "maturity_date": _cashflow_maturity(
                    name,
                    primary_income_type(instrument_types.get(name, {})),
                    all_db_instruments,
                ),
            }
            for name, months in instruments.items()
        ]
    )

    return {
        "portfolio_id": portfolio_id,
        "goal_id": portfolio.legacy_goal_id,
        "snapshots": snapshots,
        "flows": flows,
        "totals": {key: round(value, 2) for key, value in totals.items()},
        "monthly_cashflow": {
            "instruments": sorted_instruments,
            "summary": summary,
        },
    }


async def _latest_snapshot_date(db: AsyncSession, portfolio_id: int) -> date | None:
    snap = (
        await db.execute(
            select(func.max(InvestmentSnapshot.date)).where(
                InvestmentSnapshot.portfolio_id == portfolio_id
            )
        )
    ).scalar_one_or_none()
    pos = (
        await db.execute(
            select(func.max(Position.snapshot_date)).where(
                Position.portfolio_id == portfolio_id
            )
        )
    ).scalar_one_or_none()
    if snap and pos:
        return max(snap, pos)
    return snap or pos


async def build_portfolio_composition(
    db: AsyncSession,
    portfolio_id: int,
) -> dict[str, Any]:
    await _get_portfolio(db, portfolio_id)

    latest_date = await _latest_snapshot_date(db, portfolio_id)

    if not latest_date:
        return {
            "portfolio_id": portfolio_id,
            "snapshot_date": None,
            "positions": [],
            "upcoming_maturities": [],
            "closed": [],
        }

    result = await db.execute(
        select(Position, Instrument)
        .join(Instrument, Position.instrument_id == Instrument.id)
        .where(
            Position.portfolio_id == portfolio_id,
            Position.snapshot_date == latest_date,
        )
        .order_by(Position.weight_pct.desc().nullslast(), Position.market_value.desc())
    )

    positions = []
    upcoming_maturities = []
    today = date.today()

    for position, instrument in result:
        maturity = effective_bond_maturity(
            instrument.maturity_date,
            instrument.name,
            instrument.ticker,
            instrument.aliases,
        )
        item = {
            "ticker": instrument.ticker,
            "name": instrument.name,
            "asset_type": effective_composition_type(
                instrument.asset_type,
                maturity_date=maturity or instrument.maturity_date,
                coupon_rate=instrument.coupon_rate,
                name=instrument.name,
            ),
            "quantity": position.quantity,
            "market_value": position.market_value,
            "weight_pct": position.weight_pct,
            "avg_price": position.avg_price,
            "maturity_date": maturity.isoformat() if maturity else None,
            "coupon_rate": instrument.coupon_rate,
        }
        positions.append(item)
        if maturity and maturity >= today:
            upcoming_maturities.append(
                {
                    "ticker": instrument.ticker,
                    "name": instrument.name,
                    "maturity_date": maturity.isoformat(),
                    "market_value": position.market_value,
                }
            )

    upcoming_maturities.sort(key=lambda row: row["maturity_date"])
    positions = sort_composition_positions(positions)
    closed = await build_closed_positions(db, portfolio_id)

    return {
        "portfolio_id": portfolio_id,
        "snapshot_date": latest_date.isoformat(),
        "positions": positions,
        "upcoming_maturities": upcoming_maturities,
        "closed": closed,
    }


async def build_closed_positions(
    db: AsyncSession,
    portfolio_id: int,
) -> list[dict[str, Any]]:
    """Бумаги, которых уже нет в последнем снимке: выплаты + выход − покупка."""
    await _get_portfolio(db, portfolio_id)

    latest_date = await _latest_snapshot_date(db, portfolio_id)
    if not latest_date:
        return []

    rows = (
        await db.execute(
            select(Position, Instrument)
            .join(Instrument, Position.instrument_id == Instrument.id)
            .where(Position.portfolio_id == portfolio_id)
            .order_by(Position.snapshot_date.asc())
        )
    ).all()

    by_id: dict[int, dict[str, Any]] = {}
    for position, instrument in rows:
        entry = by_id.setdefault(
            instrument.id,
            {"instrument": instrument, "snapshots": []},
        )
        entry["snapshots"].append(position)

    held_now = {
        instrument.id
        for position, instrument in rows
        if position.snapshot_date == latest_date and position.quantity > 0
    }

    flows = (
        await db.execute(
            select(InvestmentFlow).where(
                InvestmentFlow.portfolio_id == portfolio_id,
                InvestmentFlow.type.in_(CLOSED_INCOME_TYPES + CLOSED_EXIT_TYPES),
            )
        )
    ).scalars().all()

    closed: list[dict[str, Any]] = []
    for instrument_id, entry in by_id.items():
        if instrument_id in held_now:
            continue
        instrument: Instrument = entry["instrument"]
        holdings = [snap for snap in entry["snapshots"] if snap.quantity > 0]
        if not holdings:
            continue
        last = holdings[-1]
        related = [flow for flow in flows if _flow_matches_instrument(flow, instrument)]
        closed.append(
            _closed_position_row(instrument, last, related, closed_on=latest_date)
        )

    closed.sort(
        key=lambda row: (
            row.get("closed_on") or "",
            -(abs(row.get("result") or 0)),
        ),
        reverse=True,
    )
    return closed


def _closed_position_row(
    instrument: Instrument,
    last_holding: Position,
    flows: list[InvestmentFlow],
    *,
    closed_on: date,
) -> dict[str, Any]:
    income = round(
        sum(flow.amount for flow in flows if flow.type in CLOSED_INCOME_TYPES),
        2,
    )
    exit_flows = [flow for flow in flows if flow.type in CLOSED_EXIT_TYPES]
    if exit_flows:
        exit_amount = round(sum(flow.amount for flow in exit_flows), 2)
        exit_source = "flow"
        if any(flow.type == "redemption" for flow in exit_flows):
            exit_kind = "redemption"
        else:
            exit_kind = "sale"
    else:
        exit_amount = round(last_holding.market_value or 0, 2)
        exit_source = "last_snapshot"
        asset = effective_composition_type(
            instrument.asset_type,
            maturity_date=instrument.maturity_date,
            coupon_rate=instrument.coupon_rate,
            name=instrument.name,
        )
        exit_kind = "redemption" if asset == "bond" else "sale"

    cost = estimate_position_cost(
        last_holding.quantity,
        last_holding.avg_price,
        last_holding.market_value,
    )
    result = None
    if cost is not None:
        result = round(income + exit_amount - cost, 2)

    asset_type = effective_composition_type(
        instrument.asset_type,
        maturity_date=instrument.maturity_date,
        coupon_rate=instrument.coupon_rate,
        name=instrument.name,
    )
    return {
        "instrument_id": instrument.id,
        "name": instrument.name,
        "ticker": instrument.ticker,
        "asset_type": asset_type,
        "closed_on": closed_on.isoformat(),
        "held_until": last_holding.snapshot_date.isoformat(),
        "quantity": last_holding.quantity,
        "cost": cost,
        "income": income,
        "exit": exit_amount,
        "exit_source": exit_source,
        "exit_kind": exit_kind,
        "result": result,
    }


async def _resolve_instrument_filter(
    db: AsyncSession,
    instrument_ref: str,
) -> Instrument | None:
    ref = instrument_ref.strip()
    if not ref:
        return None

    by_ticker = (
        await db.execute(select(Instrument).where(Instrument.ticker == ref))
    ).scalar_one_or_none()
    if by_ticker:
        return by_ticker

    by_name = (
        await db.execute(select(Instrument).where(Instrument.name == ref))
    ).scalar_one_or_none()
    if by_name:
        return by_name

    all_instruments = (await db.execute(select(Instrument))).scalars().all()
    ref_lower = ref.lower()
    for instrument in all_instruments:
        aliases = instrument.aliases or []
        if ref_lower in {alias.lower() for alias in aliases}:
            return instrument
        if ref_lower in instrument.name.lower():
            return instrument
    return None


def _flow_matches_instrument(flow: InvestmentFlow, instrument: Instrument) -> bool:
    description = (flow.description or "").lower()
    needles = {instrument.name.lower()}
    if instrument.ticker:
        needles.add(instrument.ticker.lower())
    for alias in instrument.aliases or []:
        needles.add(str(alias).lower())
    return any(needle in description for needle in needles)


def _match_instrument_by_name(
    name: str,
    instruments: list[Instrument],
) -> Instrument | None:
    flow = SimpleNamespace(description=name)
    hits = [item for item in instruments if _flow_matches_instrument(flow, item)]
    if not hits:
        return None
    bonds = [item for item in hits if item.asset_type == "bond"]
    pool = bonds or hits
    return max(pool, key=lambda item: len(item.name or ""))


def _cashflow_maturity(
    name: str,
    flow_type: str,
    instruments: list[Instrument],
) -> str | None:
    if flow_type not in {"coupon", "redemption"}:
        return None
    matched = _match_instrument_by_name(name, instruments)
    maturity = effective_bond_maturity(
        matched.maturity_date if matched else None,
        name,
        matched.name if matched else None,
        matched.ticker if matched else None,
        matched.aliases if matched else None,
    )
    return maturity.isoformat() if maturity else None


async def build_portfolio_payments(
    db: AsyncSession,
    portfolio_id: int,
    *,
    instrument: str | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    await _get_portfolio(db, portfolio_id)

    query = (
        select(InvestmentFlow)
        .where(
            InvestmentFlow.portfolio_id == portfolio_id,
            InvestmentFlow.type.in_(INCOME_FLOW_TYPES + ("tax", "sale")),
        )
        .order_by(InvestmentFlow.date.desc())
    )

    if year is not None:
        query = query.where(func.strftime("%Y", InvestmentFlow.date) == f"{year:04d}")

    flows = (await db.execute(query)).scalars().all()

    resolved = None
    if instrument:
        resolved = await _resolve_instrument_filter(db, instrument)
        if resolved:
            flows = [flow for flow in flows if _flow_matches_instrument(flow, resolved)]

    payments = [
        {
            "date": flow.date.isoformat(),
            "type": flow.type,
            "amount": flow.amount,
            "description": flow.description,
        }
        for flow in flows
    ]

    return {
        "portfolio_id": portfolio_id,
        "instrument": instrument,
        "instrument_name": resolved.name if resolved else instrument,
        "year": year,
        "payments": payments,
    }
