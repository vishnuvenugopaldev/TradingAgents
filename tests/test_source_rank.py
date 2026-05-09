"""Unit tests for the static source-credibility rank table."""

import pytest

from tradingagents.dataflows.source_rank import (
    TIER_1_RANK,
    TIER_2_RANK,
    TIER_3_RANK,
    UNKNOWN_RANK,
    get_source_rank,
)


pytestmark = pytest.mark.unit


def test_tier_ordering_is_strict():
    assert TIER_1_RANK > TIER_2_RANK > TIER_3_RANK > UNKNOWN_RANK


@pytest.mark.parametrize(
    "publisher,expected",
    [
        ("Reuters", TIER_1_RANK),
        ("reuters", TIER_1_RANK),
        ("REUTERS.com", TIER_1_RANK),
        ("Bloomberg", TIER_1_RANK),
        ("WSJ", TIER_1_RANK),
        ("Wall Street Journal", TIER_1_RANK),
        ("Financial Times", TIER_1_RANK),
        ("Associated Press", TIER_1_RANK),
    ],
)
def test_tier_1_publishers(publisher, expected):
    assert get_source_rank(publisher) == expected


@pytest.mark.parametrize(
    "publisher,expected",
    [
        ("CNBC", TIER_2_RANK),
        ("Forbes", TIER_2_RANK),
        ("MarketWatch", TIER_2_RANK),
        ("BBC", TIER_2_RANK),
        ("The New York Times", TIER_2_RANK),
    ],
)
def test_tier_2_publishers(publisher, expected):
    assert get_source_rank(publisher) == expected


@pytest.mark.parametrize(
    "publisher,expected",
    [
        ("Yahoo Finance", TIER_3_RANK),
        ("Seeking Alpha", TIER_3_RANK),
        ("The Motley Fool", TIER_3_RANK),
        ("Benzinga", TIER_3_RANK),
        ("Zacks", TIER_3_RANK),
    ],
)
def test_tier_3_publishers(publisher, expected):
    assert get_source_rank(publisher) == expected


@pytest.mark.parametrize("publisher", ["", None, "unknown_blog.com", "RandomSubstack"])
def test_unknown_publishers_get_lowest_rank(publisher):
    assert get_source_rank(publisher) == UNKNOWN_RANK


def test_normalization_ignores_punctuation_and_case():
    # Same logical publisher in three different surface forms.
    assert (
        get_source_rank("Reuters")
        == get_source_rank("reuters.com")
        == get_source_rank("REUTERS")
        == TIER_1_RANK
    )


def test_canonical_ordering_property():
    # The property the README and CLAUDE.md rely on for tiebreaking.
    assert (
        get_source_rank("Reuters")
        > get_source_rank("Yahoo Finance")
        > get_source_rank("unknown_blog.com")
    )
