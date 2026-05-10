"""Parse free-text time-horizon strings (PM output) to integer trading days.

The Portfolio Manager emits ``time_horizon`` as free text on
``PortfolioDecision`` (e.g. "3-6 months", "1 month", "intraday", "long term").
The deferred-reflection path needs an integer holding-day count to slice the
yfinance price history. This module is the single contract that maps one
to the other.

Design notes:

- Range expressions ("3-6 months") map to the **midpoint** in trading days,
  so reflections aren't biased toward "early-resolve and lock in noise."
- Unparseable / missing values fall back to **5 trading days** — the
  framework's prior hardcoded default. That keeps the behaviour for old
  log entries (which never carried a horizon) unchanged.
- Trading-day conventions: 1 week ≈ 5d, 1 month ≈ 21d, 1 quarter ≈ 63d,
  1 year ≈ 252d. We round to integers; nothing downstream needs sub-day
  precision.
"""

from __future__ import annotations

import re
from typing import Optional

DEFAULT_HORIZON_DAYS = 5

# Trading-day equivalents per unit. Calendar-day intent is preserved by
# choosing the unit's standard trading-day count (5/21/63/252) rather than
# 7/30/90/365. The reflection only needs price-bar count, not wall-time.
_UNIT_TO_TRADING_DAYS = {
    "day": 1,
    "week": 5,
    "month": 21,
    "quarter": 63,
    "year": 252,
}

# Recognized fixed phrases. Checked BEFORE numeric parsing so "long term"
# doesn't fall through to "no number found → default."
_PHRASE_TO_DAYS = {
    "intraday": 1,
    "day trade": 1,
    "short term": 21,
    "near term": 21,
    "medium term": 63,
    "mid term": 63,
    "long term": 252,
    "longterm": 252,
    "long-term": 252,
}

# Matches "<low>-<high> <unit>" (e.g. "3-6 months", "1-2 weeks").
_RANGE_RE = re.compile(
    r"(\d+)\s*[-–to]+\s*(\d+)\s*(day|week|month|quarter|year)s?",
    re.IGNORECASE,
)
# Matches "<n> <unit>" (e.g. "1 month", "21 days").
_SCALAR_RE = re.compile(
    r"(\d+)\s*(day|week|month|quarter|year)s?",
    re.IGNORECASE,
)
# Matches a bare unit suffix ("1d", "2w", "3mo"). Useful when the PM emits
# something terse.
_SUFFIX_RE = re.compile(
    r"(\d+)\s*(d|w|mo|m|q|y)\b",
    re.IGNORECASE,
)
_SUFFIX_TO_UNIT = {
    "d": "day",
    "w": "week",
    "mo": "month",
    "m": "month",
    "q": "quarter",
    "y": "year",
}


def parse_horizon_to_days(horizon: Optional[str]) -> int:
    """Map a free-text horizon string to a trading-day integer.

    Returns ``DEFAULT_HORIZON_DAYS`` (5) when the input is None, empty, or
    contains nothing recognizable. Never raises.
    """
    if not horizon or not isinstance(horizon, str):
        return DEFAULT_HORIZON_DAYS

    text = horizon.strip().lower()
    if not text:
        return DEFAULT_HORIZON_DAYS

    # 1. Fixed phrases — checked before numeric forms so "long term" wins
    #    over an embedded digit if any.
    for phrase, days in _PHRASE_TO_DAYS.items():
        if phrase in text:
            return days

    # 2. Range expressions — use the midpoint so a "3-6 months" thesis is
    #    measured at ~4.5 months, not at the early end of the range.
    range_match = _RANGE_RE.search(text)
    if range_match:
        low, high, unit = range_match.groups()
        unit_days = _UNIT_TO_TRADING_DAYS[unit.lower()]
        midpoint = (int(low) + int(high)) / 2
        return max(1, round(midpoint * unit_days))

    # 3. Scalar "<n> <unit>".
    scalar_match = _SCALAR_RE.search(text)
    if scalar_match:
        n, unit = scalar_match.groups()
        unit_days = _UNIT_TO_TRADING_DAYS[unit.lower()]
        return max(1, int(n) * unit_days)

    # 4. Suffix "<n><unit>" without a space.
    suffix_match = _SUFFIX_RE.search(text)
    if suffix_match:
        n, suffix = suffix_match.groups()
        unit = _SUFFIX_TO_UNIT[suffix.lower()]
        unit_days = _UNIT_TO_TRADING_DAYS[unit]
        return max(1, int(n) * unit_days)

    return DEFAULT_HORIZON_DAYS


# Header used in the rendered Portfolio Manager markdown. Centralised so
# the extractor below and the renderer in ``schemas.render_pm_decision``
# don't drift apart.
_TIME_HORIZON_LINE_RE = re.compile(
    r"^\s*\*?\*?\s*Time Horizon\s*\*?\*?\s*[:\-]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_time_horizon(rendered_decision: Optional[str]) -> Optional[str]:
    """Pull the ``**Time Horizon**: ...`` value out of a PM markdown block.

    Returns the captured value (free-text, e.g. "3-6 months") or ``None``
    when the line is absent. Tolerant of bold wrappers and the field being
    on its own line anywhere in the rendered text.
    """
    if not rendered_decision:
        return None
    match = _TIME_HORIZON_LINE_RE.search(rendered_decision)
    if not match:
        return None
    value = match.group(1).strip().strip("*").strip()
    return value or None
