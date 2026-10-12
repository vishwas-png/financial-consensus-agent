"""Data Checker (Stage 9): checks the analyst's "field = value" data points against the market data.

No LLM and no financial judgement: it only looks a value up and compares numbers. Stage 5
found that the LLM Auditor sometimes flags correct numbers because of its own arithmetic or
unit mistakes; this check gives an independent, deterministic answer for the simple case
"does this number appear in the data?". It runs inside the auditor step and is shown next to
the Auditor's verdict (it is not fed into the Auditor's prompt, so judge calibration is unchanged).

It is lenient about presentation: "3.4T", "$391.0B" and rounding to the digits written all
count as a match, and fraction fields (e.g. profit_margin = 0.27) may be written as "27%".
It is strict about scale where scale is the classic mistake: dividend_yield_pct is already a
percentage, so "32%" for 0.32 is a mismatch. Periods (quarterly vs annual) are the Auditor's job.
"""

import re
from typing import Any

from src.core.schemas import AnalystOutput, DataCheck, DataPointCheck

# Sections of raw_data whose fields are single numbers (see tools/market_data.py INFO_FIELDS)
SCALAR_SECTIONS = ("price", "valuation", "financials")
# annual_financials rows also hold these fields, one value per fiscal year
ANNUAL_FIELDS = ("total_revenue", "net_income")

# "3.4T", "$391.0B", "-1,234.5", "27%", "0.27 billion"
NUMBER = re.compile(
    r"(?P<sign>-)?\$?(?P<number>\d[\d,]*(?:\.(?P<decimals>\d+))?)\s*"
    r"(?P<suffix>trillion|billion|million|thousand|[TBMK](?![A-Za-z]))?\s*(?P<percent>%)?",
    re.IGNORECASE,
)
MULTIPLIERS = {"t": 1e12, "trillion": 1e12, "b": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6,
               "k": 1e3, "thousand": 1e3}
RELATIVE_TOLERANCE = 0.02   # 2%: covers "about 39x" for 39.04 and similar rounding
# Fields stored as fractions (0.27 = 27%); only these may be written as percentages.
# Fields ending in "_pct" are already percentages (0.32 = 0.32%), see market_data.py.
FRACTION_FIELDS = {"revenue_growth", "profit_margin"}


def known_values(raw_data: dict[str, Any]) -> dict[str, list[float]]:
    """field name -> every numeric value the data holds for it."""
    values: dict[str, list[float]] = {}
    for section in SCALAR_SECTIONS:
        for field, value in (raw_data.get(section) or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.setdefault(field, []).append(float(value))
    for row in raw_data.get("annual_financials") or []:
        for field in ANNUAL_FIELDS:
            value = row.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.setdefault(field, []).append(float(value))
    return values


def _readings(match: re.Match[str], field: str) -> list[tuple[float, float]]:
    """Possible meanings of a written number, each with the rounding step its digits imply."""
    base = float(match.group("number").replace(",", ""))
    if match.group("sign"):
        base = -base
    multiplier = MULTIPLIERS.get((match.group("suffix") or "").lower(), 1.0)
    step = 0.5 * 10 ** -len(match.group("decimals") or "") * multiplier
    readings = [(base * multiplier, step)]
    if field in FRACTION_FIELDS and multiplier == 1.0:
        readings.append((base / 100, step / 100))   # "27%" or "27" for a 0.27 fraction
    return readings


def check_data_point(data_point: str, values: dict[str, list[float]]) -> DataPointCheck:
    if "=" not in data_point and ":" not in data_point:
        return DataPointCheck(data_point=data_point, status="not_checkable")
    left, right = re.split(r"[=:]", data_point, maxsplit=1)
    identifiers = re.findall(r"[a-z_][a-z0-9_]*", left.lower())
    field = next((name for name in reversed(identifiers) if name in values), None)
    if field is None:
        return DataPointCheck(data_point=data_point, field=identifiers[-1] if identifiers else None,
                              status="unknown_field")
    number = NUMBER.search(right)
    if number is None:
        return DataPointCheck(data_point=data_point, field=field, status="not_checkable")

    readings = _readings(number, field)
    for reading, step in readings:
        for value in values[field]:
            if abs(reading - value) <= max(abs(value) * RELATIVE_TOLERANCE, step):
                return DataPointCheck(data_point=data_point, field=field, claimed=reading, actual=value,
                                      status="match")
    claimed = readings[0][0]
    actual = min(values[field], key=lambda value: abs(value - claimed))
    return DataPointCheck(data_point=data_point, field=field, claimed=claimed, actual=actual, status="mismatch")


def check_data_points(analysis: AnalystOutput, raw_data: dict[str, Any]) -> DataCheck:
    values = known_values(raw_data)
    checks = [check_data_point(point, values) for point in analysis.supporting_data_points]
    matched = sum(check.status == "match" for check in checks)
    mismatched = sum(check.status == "mismatch" for check in checks)
    return DataCheck(checks=checks, matched=matched, mismatched=mismatched,
                     unchecked=len(checks) - matched - mismatched)
