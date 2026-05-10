"""Unit tests for parse_horizon_to_days and extract_time_horizon (Group B2)."""

import pytest

from tradingagents.agents.utils.horizon import (
    DEFAULT_HORIZON_DAYS,
    extract_time_horizon,
    parse_horizon_to_days,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# parse_horizon_to_days
# ---------------------------------------------------------------------------


class TestParseHorizonFallback:
    """Inputs that should fall back to the framework's prior 5-day default."""

    @pytest.mark.parametrize("value", [None, "", "   ", "garbage", "tomorrow afternoon"])
    def test_returns_default(self, value):
        assert parse_horizon_to_days(value) == DEFAULT_HORIZON_DAYS

    def test_non_string_input_returns_default(self):
        # Defensive: PM output is typed str, but a stray None or int upstream
        # must not raise.
        assert parse_horizon_to_days(123) == DEFAULT_HORIZON_DAYS  # type: ignore[arg-type]


class TestParseHorizonFixedPhrases:
    @pytest.mark.parametrize(
        "phrase,expected_days",
        [
            ("intraday", 1),
            ("Intraday", 1),
            ("day trade", 1),
            ("short term", 21),
            ("near term", 21),
            ("medium term", 63),
            ("mid term", 63),
            ("long term", 252),
            ("long-term", 252),
            ("LONG TERM", 252),
        ],
    )
    def test_phrase_lookup(self, phrase, expected_days):
        assert parse_horizon_to_days(phrase) == expected_days


class TestParseHorizonScalar:
    @pytest.mark.parametrize(
        "value,expected_days",
        [
            ("1 day", 1),
            ("1 week", 5),
            ("2 weeks", 10),
            ("1 month", 21),
            ("3 months", 63),
            ("1 quarter", 63),
            ("1 year", 252),
            ("21 days", 21),
        ],
    )
    def test_scalar(self, value, expected_days):
        assert parse_horizon_to_days(value) == expected_days


class TestParseHorizonRange:
    """Ranges resolve to the midpoint, in trading days."""

    @pytest.mark.parametrize(
        "value,expected_days",
        [
            ("1-2 weeks", round(1.5 * 5)),         # 8
            ("3-6 months", round(4.5 * 21)),       # 95
            ("6-12 months", round(9.0 * 21)),      # 189
            # Range with en-dash / "to" separators should also work.
            ("3 to 6 months", round(4.5 * 21)),
            ("3–6 months", round(4.5 * 21)),
        ],
    )
    def test_range_midpoint(self, value, expected_days):
        assert parse_horizon_to_days(value) == expected_days


class TestParseHorizonSuffix:
    @pytest.mark.parametrize(
        "value,expected_days",
        [
            ("5d", 5),
            ("2w", 10),
            ("3mo", 63),
            ("1q", 63),
            ("1y", 252),
        ],
    )
    def test_suffix(self, value, expected_days):
        assert parse_horizon_to_days(value) == expected_days


class TestParseHorizonNeverNegative:
    def test_minimum_is_one(self):
        # The clamp protects against weird upstream inputs like "0 days".
        assert parse_horizon_to_days("0 days") == 1


# ---------------------------------------------------------------------------
# extract_time_horizon
# ---------------------------------------------------------------------------


class TestExtractTimeHorizon:

    def test_extracts_from_rendered_pm_markdown(self):
        rendered = (
            "**Rating**: Buy\n\n"
            "**Executive Summary**: Build into the position.\n\n"
            "**Investment Thesis**: AI capex cycle intact.\n\n"
            "**Price Target**: 215.0\n\n"
            "**Time Horizon**: 3-6 months"
        )
        assert extract_time_horizon(rendered) == "3-6 months"

    def test_handles_unbolded_label(self):
        rendered = "Time Horizon: 1 month"
        assert extract_time_horizon(rendered) == "1 month"

    def test_returns_none_when_absent(self):
        rendered = "**Rating**: Hold\n\n**Executive Summary**: Hold position."
        assert extract_time_horizon(rendered) is None

    def test_returns_none_for_empty_input(self):
        assert extract_time_horizon("") is None
        assert extract_time_horizon(None) is None

    def test_round_trip_with_parse(self):
        """End-to-end: render-shape value → extract → parse_to_days."""
        rendered = "**Time Horizon**: 1-3 months"
        horizon = extract_time_horizon(rendered)
        assert horizon == "1-3 months"
        assert parse_horizon_to_days(horizon) == round(2.0 * 21)  # 42
