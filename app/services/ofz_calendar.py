"""Календарь погашений ОФЗ (MOEX TQOB). Нужен, потому что брокерский отчёт дату не даёт."""

from __future__ import annotations

import re
from datetime import date

# Выпуск → дата погашения. Источник: MOEX ISS board TQOB.
OFZ_SERIES_MATURITY: dict[str, date] = {
    "26207": date(2027, 2, 3),
    "26212": date(2028, 1, 19),
    "26218": date(2031, 9, 17),
    "26219": date(2026, 9, 16),
    "26221": date(2033, 3, 23),
    "26224": date(2029, 5, 23),
    "26225": date(2034, 5, 10),
    "26226": date(2026, 10, 7),
    "26228": date(2030, 4, 10),
    "26230": date(2039, 3, 16),
    "26231": date(2044, 7, 20),
    "26232": date(2027, 10, 6),
    "26233": date(2035, 7, 18),
    "26235": date(2031, 3, 12),
    "26236": date(2028, 5, 17),
    "26237": date(2029, 3, 14),
    "26238": date(2041, 5, 15),
    "26239": date(2031, 7, 23),
    "26240": date(2036, 7, 30),
    "26241": date(2032, 11, 17),
    "26242": date(2029, 8, 29),
    "26243": date(2038, 5, 19),
    "26244": date(2034, 3, 15),
    "26245": date(2035, 9, 26),
    "26246": date(2036, 3, 12),
    "26247": date(2039, 5, 11),
    "26248": date(2040, 5, 16),
    "26249": date(2032, 6, 16),
    "26250": date(2037, 6, 10),
    "26251": date(2030, 8, 28),
    "26252": date(2033, 10, 12),
    "26253": date(2038, 10, 6),
    "26254": date(2040, 10, 3),
    "29007": date(2027, 3, 3),
    "29008": date(2029, 10, 3),
    "29009": date(2032, 5, 5),
    "29010": date(2034, 12, 6),
    "29013": date(2030, 9, 18),
    "29015": date(2028, 10, 18),
    "29016": date(2026, 12, 23),
    "29017": date(2032, 8, 25),
    "29018": date(2031, 11, 26),
    "29019": date(2029, 7, 18),
    "29020": date(2027, 9, 22),
    "29021": date(2030, 11, 27),
    "29022": date(2033, 7, 20),
    "29023": date(2034, 8, 23),
    "29024": date(2035, 4, 18),
    "29025": date(2037, 8, 12),
    "29026": date(2038, 9, 4),
    "29027": date(2036, 9, 11),
    "29028": date(2039, 10, 22),
    "29029": date(2041, 10, 22),
    "46012": date(2029, 9, 5),
    "46020": date(2036, 2, 6),
    "52002": date(2028, 2, 2),
    "52003": date(2030, 7, 17),
    "52004": date(2032, 3, 17),
    "52005": date(2033, 5, 11),
}

OFZ_ISIN_MATURITY: dict[str, date] = {
    "RU000A0JS3W6": date(2027, 2, 3),
    "RU000A0JTK38": date(2028, 1, 19),
    "RU000A0JVW48": date(2031, 9, 17),
    "RU000A0JWM07": date(2026, 9, 16),
    "RU000A0JXFM1": date(2033, 3, 23),
    "RU000A0ZYUA9": date(2029, 5, 23),
    "RU000A0ZYUB7": date(2034, 5, 10),
    "RU000A0ZZYW2": date(2026, 10, 7),
    "RU000A100A82": date(2030, 4, 10),
    "RU000A100EF5": date(2039, 3, 16),
    "RU000A100MY9": date(2044, 7, 20),
    "RU000A1014N4": date(2027, 10, 6),
    "RU000A101F94": date(2035, 7, 18),
    "RU000A1028E3": date(2031, 3, 12),
    "RU000A102BT8": date(2028, 5, 17),
    "RU000A1038Z7": date(2029, 3, 14),
    "RU000A1038V6": date(2041, 5, 15),
    "RU000A103901": date(2031, 7, 23),
    "RU000A103BR0": date(2036, 7, 30),
    "RU000A105FZ9": date(2032, 11, 17),
    "RU000A105RV3": date(2029, 8, 29),
    "RU000A106E90": date(2038, 5, 19),
    "RU000A1074G2": date(2034, 3, 15),
    "RU000A108EG6": date(2035, 9, 26),
    "RU000A108EE1": date(2036, 3, 12),
    "RU000A108EF8": date(2039, 5, 11),
    "RU000A108EH4": date(2040, 5, 16),
    "RU000A10BVC8": date(2032, 6, 16),
    "RU000A10BVH7": date(2037, 6, 10),
    "RU000A10CKT3": date(2030, 8, 28),
    "RU000A10D4Y2": date(2033, 10, 12),
    "RU000A10D517": date(2038, 10, 6),
    "RU000A10D533": date(2040, 10, 3),
    "RU000A0JV4M0": date(2027, 3, 3),
    "RU000A0JV4P3": date(2029, 10, 3),
    "RU000A0JV4N8": date(2032, 5, 5),
    "RU000A0JV4Q1": date(2034, 12, 6),
    "RU000A101KT1": date(2030, 9, 18),
    "RU000A1025A7": date(2028, 10, 18),
    "RU000A1025B5": date(2026, 12, 23),
    "RU000A1028D5": date(2032, 8, 25),
    "RU000A102A31": date(2031, 11, 26),
    "RU000A102A49": date(2029, 7, 18),
    "RU000A102BV4": date(2027, 9, 22),
    "RU000A105B11": date(2030, 11, 27),
    "RU000A105G16": date(2033, 7, 20),
    "RU000A105L19": date(2034, 8, 23),
    "RU000A1066D5": date(2035, 4, 18),
    "RU000A106Z61": date(2037, 8, 12),
    "RU000A10A7D2": date(2038, 9, 4),
    "RU000A10AA93": date(2036, 9, 11),
    "RU000A10D4Z9": date(2039, 10, 22),
    "RU000A10D525": date(2041, 10, 22),
    "RU0002868001": date(2029, 9, 5),
    "RU000A0GN9A7": date(2036, 2, 6),
    "RU000A0ZYZ26": date(2028, 2, 2),
    "RU000A102069": date(2030, 7, 17),
    "RU000A103MX5": date(2032, 3, 17),
    "RU000A105XV1": date(2033, 5, 11),
    "RU000A10DQA8": date(2033, 6, 1),
    "RU000A10DQB6": date(2029, 2, 28),
    "RU000A10FAK6": date(2036, 5, 21),
}

# Юаневые ОФЗ: в коротком имени «ОФЗ 29» — год погашения, не серия 290xx.
OFZ_CNY_MATURITY: dict[str, date] = {
    "29": date(2029, 2, 28),
    "33": date(2033, 6, 1),
    "36": date(2036, 5, 21),
}

_ISIN_RE = re.compile(r"\bRU000[A-Z0-9]{7}\b", re.I)
_SERIES_RE = re.compile(r"(?:SU)?(26\d{3}|29\d{3}|46\d{3}|52\d{3})(?:RMFS)?", re.I)
_CNY_RE = re.compile(r"офз\s*(\d{2})\s*cny", re.I)
_SHORT_CNY_RE = re.compile(r"^офз\s*(\d{2})$", re.I)


def _join_parts(*parts: object) -> str:
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple)):
            chunks.extend(str(item) for item in part if item)
        else:
            chunks.append(str(part))
    return " ".join(chunks)


def lookup_ofz_maturity(*parts: object) -> date | None:
    """Дата погашения ОФЗ из ISIN, номера выпуска или имени вроде «ОФЗ 29 CNY»."""
    blob = _join_parts(*parts)
    if not blob:
        return None

    for isin in _ISIN_RE.findall(blob):
        found = OFZ_ISIN_MATURITY.get(isin.upper())
        if found:
            return found

    series_match = _SERIES_RE.search(blob)
    if series_match:
        found = OFZ_SERIES_MATURITY.get(series_match.group(1))
        if found:
            return found

    cny_match = _CNY_RE.search(blob)
    if cny_match:
        found = OFZ_CNY_MATURITY.get(cny_match.group(1))
        if found:
            return found

    stripped = re.sub(
        r"^(купон|дивиденды|дивиденд)\s*[:—–-]?\s*",
        "",
        blob.strip(),
        flags=re.I,
    )
    short_match = _SHORT_CNY_RE.match(stripped)
    if short_match:
        return OFZ_CNY_MATURITY.get(short_match.group(1))
    return None


def effective_bond_maturity(
    stored: date | str | None,
    *parts: object,
) -> date | None:
    """Сначала дата из импорта, иначе календарь ОФЗ по имени/ISIN/тикеру."""
    if isinstance(stored, date):
        return stored
    if isinstance(stored, str) and stored:
        try:
            return date.fromisoformat(stored[:10])
        except ValueError:
            pass
    return lookup_ofz_maturity(*parts)
