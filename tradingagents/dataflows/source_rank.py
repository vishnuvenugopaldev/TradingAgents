"""Static source-credibility ranking for news articles.

Used to (a) sort news lists before downstream truncation and (b) break ties
when ``news_dedup`` collapses near-duplicate stories from multiple outlets.

The map is deliberately small and static: tier-1 wires + flagship business
press, tier-2 major networks and broad business outlets, tier-3 aggregators,
and a default for unknown publishers. No fuzzy matching is attempted — the
publisher string from the news vendor is normalized and looked up directly.
"""

from __future__ import annotations

import re
from typing import Iterable

# Normalize publisher strings before lookup. We try a few surface forms so
# that "Reuters", "Reuters.com", "REUTERS-NEWS", and "The New York Times"
# all hit their canonical entry without us having to enumerate every variant.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Common URL/domain suffixes that creep into vendor publisher fields.
_DOMAIN_SUFFIXES = ("com", "net", "org", "co", "io", "news")


def _normalize_publisher(name: str) -> str:
    if not name:
        return ""
    cleaned = _NON_ALNUM_RE.sub(" ", name.lower()).strip()
    if not cleaned:
        return ""
    # Drop leading article ("the new york times" → "new york times").
    if cleaned.startswith("the "):
        cleaned = cleaned[4:]
    tokens = cleaned.split()
    # Strip a trailing TLD-ish token so "reuters com" → "reuters".
    if len(tokens) > 1 and tokens[-1] in _DOMAIN_SUFFIXES:
        tokens = tokens[:-1]
    return "".join(tokens)


# Tier 1 — primary wires and flagship financial press.
_TIER_1 = (
    "reuters",
    "bloomberg",
    "wsj",
    "wallstreetjournal",
    "ft",
    "financialtimes",
    "ap",
    "associatedpress",
    "dowjones",
)

# Tier 2 — major networks and broad business outlets.
_TIER_2 = (
    "cnbc",
    "barrons",
    "marketwatch",
    "theeconomist",
    "economist",
    "forbes",
    "businessinsider",
    "bbc",
    "nytimes",
    "newyorktimes",
    "washingtonpost",
    "guardian",
    "theguardian",
    "axios",
)

# Tier 3 — aggregators and general retail finance sites.
_TIER_3 = (
    "yahoofinance",
    "yahoo",
    "googlefinance",
    "msnmoney",
    "msn",
    "investing",
    "investingcom",
    "seekingalpha",
    "motleyfool",
    "thefool",
    "benzinga",
    "thestreet",
    "zacks",
    "fool",
)

TIER_1_RANK = 100
TIER_2_RANK = 60
TIER_3_RANK = 30
UNKNOWN_RANK = 10


def _build_rank_map(items: Iterable[str], rank: int) -> dict[str, int]:
    return {key: rank for key in items}


SOURCE_RANK: dict[str, int] = {
    **_build_rank_map(_TIER_1, TIER_1_RANK),
    **_build_rank_map(_TIER_2, TIER_2_RANK),
    **_build_rank_map(_TIER_3, TIER_3_RANK),
}


def get_source_rank(publisher: str | None) -> int:
    """Return the credibility rank for a publisher; ``UNKNOWN_RANK`` if unmapped.

    The lookup is normalized: case and punctuation are ignored, so
    ``"Reuters"``, ``"reuters.com"``, and ``"REUTERS"`` all map to the same
    tier. Empty / None publishers fall through to the unknown rank.
    """
    if not publisher:
        return UNKNOWN_RANK
    return SOURCE_RANK.get(_normalize_publisher(publisher), UNKNOWN_RANK)
