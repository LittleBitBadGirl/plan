"""Instrument lookup and normalization for portfolio imports."""

from __future__ import annotations

import difflib
import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import Instrument

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def normalize_name(value: str) -> str:
    lowered = value.lower().replace("ё", "е")
    cleaned = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def is_isin(value: str | None) -> bool:
    if not value:
        return False
    return bool(_ISIN_RE.match(value.strip().upper()))


def _alias_values(aliases: Any) -> list[str]:
    if not aliases:
        return []
    if isinstance(aliases, list):
        return [str(item) for item in aliases if item]
    return [str(aliases)]


def _names_match(left: str, right: str) -> bool:
    left_norm = normalize_name(left)
    right_norm = normalize_name(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    if left_norm in right_norm or right_norm in left_norm:
        return True
    return difflib.SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.85


def _instrument_matches(
    instrument: Instrument,
    *,
    name: str | None,
    ticker: str | None,
    isin: str | None,
) -> bool:
    if ticker and instrument.ticker and instrument.ticker.upper() == ticker.upper():
        return True
    if isin:
        isin_upper = isin.upper()
        if instrument.ticker and instrument.ticker.upper() == isin_upper:
            return True
        if any(alias.upper() == isin_upper for alias in _alias_values(instrument.aliases)):
            return True
    if name:
        if _names_match(instrument.name, name):
            return True
        for alias in _alias_values(instrument.aliases):
            if alias.upper() == name.upper() or _names_match(alias, name):
                return True
    if ticker:
        for alias in _alias_values(instrument.aliases):
            if alias.upper() == ticker.upper():
                return True
    return False


def _build_aliases(
    *,
    name: str,
    ticker: str | None,
    isin: str | None,
    extra: str | None = None,
) -> list[str]:
    values: list[str] = []
    for raw in (ticker, isin, name, extra):
        if not raw:
            continue
        cleaned = raw.strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values


def _merge_aliases(existing: Any, new_values: list[str]) -> list[str]:
    merged = _alias_values(existing)
    for value in new_values:
        if value not in merged:
            merged.append(value)
    return merged


async def resolve_instrument(
    db: AsyncSession,
    *,
    name: str,
    ticker: str | None = None,
    isin: str | None = None,
    asset_type: str = "other",
    maturity_date: date | None = None,
    coupon_rate: float | None = None,
    instrument_ref: str | None = None,
) -> Instrument:
    """Find or create an instrument by ticker, ISIN, aliases, or fuzzy name."""
    ref = (instrument_ref or "").strip()
    if ref and not ticker and not isin:
        if is_isin(ref):
            isin = ref
        elif ref.isupper() and len(ref) <= 12 and " " not in ref:
            ticker = ref
        elif not name:
            name = ref

    if not name and not ticker and not isin:
        raise ValueError("Instrument requires at least one of name, ticker, or isin")

    display_name = name or ticker or isin or "Unknown"
    instruments = list((await db.execute(select(Instrument))).scalars().all())

    for instrument in instruments:
        if _instrument_matches(
            instrument,
            name=name or display_name,
            ticker=ticker,
            isin=isin,
        ):
            aliases = _merge_aliases(
                instrument.aliases,
                _build_aliases(
                    name=display_name,
                    ticker=ticker,
                    isin=isin,
                    extra=name if name and name != display_name else None,
                ),
            )
            instrument.aliases = aliases
            if ticker and not instrument.ticker:
                instrument.ticker = ticker.upper()
            if name and instrument.name != name:
                if instrument.name.lower() in {"unknown", display_name.lower()}:
                    instrument.name = name
            if asset_type and instrument.asset_type == "other":
                instrument.asset_type = asset_type
            if maturity_date and not instrument.maturity_date:
                instrument.maturity_date = maturity_date
            if coupon_rate is not None and instrument.coupon_rate is None:
                instrument.coupon_rate = coupon_rate
            return instrument

    instrument = Instrument(
        ticker=ticker.upper() if ticker else None,
        name=display_name,
        asset_type=asset_type or "other",
        maturity_date=maturity_date,
        coupon_rate=coupon_rate,
        aliases=_build_aliases(name=display_name, ticker=ticker, isin=isin),
    )
    db.add(instrument)
    await db.flush()
    return instrument
