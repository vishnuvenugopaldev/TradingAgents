"""Unit tests for news_dedup: title normalization, exact dedup, Jaccard clustering."""

from datetime import datetime

import pytest

from tradingagents.dataflows.news_dedup import (
    cluster_articles,
    dedup_exact,
    normalize_title,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("NVIDIA Beats Q3 Earnings", "nvidia beats q3 earnings"),
        ("Nvidia: Beats Q3 Earnings!", "nvidia beats q3 earnings"),
        ("  NVIDIA   beats   Q3   earnings  ", "nvidia beats q3 earnings"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


# ---------------------------------------------------------------------------
# dedup_exact
# ---------------------------------------------------------------------------


def test_dedup_exact_collapses_punctuation_and_case_variants():
    articles = [
        {"title": "NVIDIA Beats Q3 Earnings", "publisher": "Reuters", "pub_date": None},
        {"title": "nvidia beats q3 earnings", "publisher": "CNBC", "pub_date": None},
        {"title": "NVIDIA: Beats Q3 Earnings!", "publisher": "Yahoo Finance", "pub_date": None},
        {"title": "Apple Announces New iPhone", "publisher": "Reuters", "pub_date": None},
    ]
    out = dedup_exact(articles)
    assert len(out) == 2
    # First-seen-wins: Reuters NVIDIA + Reuters Apple.
    assert out[0]["publisher"] == "Reuters"
    assert out[1]["title"] == "Apple Announces New iPhone"


def test_dedup_exact_drops_empty_and_missing_titles():
    articles = [
        {"title": "Real story", "publisher": "Reuters", "pub_date": None},
        {"title": "", "publisher": "Reuters", "pub_date": None},
        {"title": None, "publisher": "Reuters", "pub_date": None},
        {"publisher": "Reuters", "pub_date": None},  # title key missing entirely
    ]
    out = dedup_exact(articles)
    assert len(out) == 1
    assert out[0]["title"] == "Real story"


# ---------------------------------------------------------------------------
# cluster_articles — the main contract
# ---------------------------------------------------------------------------


def test_syndicated_wire_collapses_to_highest_tier_exemplar():
    """The same Reuters wire reposted by 5 outlets should collapse to one exemplar.

    Tiebreaking: highest source rank wins → the Reuters copy.
    """
    pub = datetime(2026, 5, 1, 10, 0, 0)
    articles = [
        {"title": "NVIDIA Reports Record Q3 Revenue Driven by AI Demand", "publisher": "Yahoo Finance", "pub_date": pub},
        {"title": "Nvidia reports record Q3 revenue, driven by AI demand", "publisher": "Seeking Alpha", "pub_date": pub},
        {"title": "Nvidia reports record Q3 revenue driven by AI demand!", "publisher": "Reuters", "pub_date": pub},
        {"title": "NVIDIA: Record Q3 revenue, AI demand the driver", "publisher": "MarketWatch", "pub_date": pub},
        {"title": "NVIDIA reports record Q3 revenue — AI demand drives growth", "publisher": "Benzinga", "pub_date": pub},
    ]
    out = cluster_articles(articles)
    assert len(out) == 1
    assert out[0]["publisher"] == "Reuters"


def test_distinct_stories_on_same_ticker_stay_separate():
    pub = datetime(2026, 5, 1, 10, 0, 0)
    articles = [
        {"title": "Nvidia announces Q3 earnings beat", "publisher": "Reuters", "pub_date": pub},
        {"title": "Nvidia CEO Jensen Huang to keynote at Computex", "publisher": "Bloomberg", "pub_date": pub},
        {"title": "Nvidia stock target raised to $200 by Goldman", "publisher": "WSJ", "pub_date": pub},
    ]
    out = cluster_articles(articles)
    assert len(out) == 3


def test_recency_breaks_rank_ties():
    """Two outlets at the same source-rank tier → most recent pub_date wins."""
    older = datetime(2026, 4, 1)
    newer = datetime(2026, 5, 1)
    articles = [
        {"title": "Apple posts strong iPhone sales", "publisher": "Yahoo Finance", "pub_date": older},
        {"title": "Apple posts strong iPhone sales!", "publisher": "Seeking Alpha", "pub_date": newer},
    ]
    out = cluster_articles(articles)
    assert len(out) == 1
    assert out[0]["pub_date"] == newer


def test_output_is_sorted_by_rank_then_recency():
    pub = datetime(2026, 5, 1)
    articles = [
        {"title": "Story A about widgets", "publisher": "Yahoo Finance", "pub_date": pub},
        {"title": "Story B about gadgets", "publisher": "Reuters", "pub_date": pub},
        {"title": "Story C about gizmos", "publisher": "CNBC", "pub_date": pub},
    ]
    out = cluster_articles(articles)
    assert [a["publisher"] for a in out] == ["Reuters", "CNBC", "Yahoo Finance"]


def test_empty_input_returns_empty():
    assert cluster_articles([]) == []


def test_below_threshold_titles_do_not_cluster():
    """Two titles sharing only one rare token should NOT collapse."""
    pub = datetime(2026, 5, 1)
    articles = [
        {"title": "Tesla unveils robotaxi pricing strategy", "publisher": "Reuters", "pub_date": pub},
        {"title": "Tesla recalls Model Y over brake defect", "publisher": "Bloomberg", "pub_date": pub},
    ]
    out = cluster_articles(articles)
    assert len(out) == 2
