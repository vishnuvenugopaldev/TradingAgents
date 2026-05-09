"""Integration tests for the point-in-time guard (A1) on both news vendors.

Each vendor's news fetcher is exercised against three articles: one inside
the [start, end] window, one after, and one with no parseable timestamp.
All three should be reduced to the single in-window article.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# yfinance path
# ---------------------------------------------------------------------------


def _yf_article(title: str, publisher: str, pub_date_iso: str | None) -> dict:
    """Build a yfinance NEWS API row in the nested-content shape the parser expects."""
    content = {
        "title": title,
        "summary": f"Summary for {title}",
        "provider": {"displayName": publisher},
        "canonicalUrl": {"url": f"https://example.com/{title.replace(' ', '-').lower()}"},
    }
    if pub_date_iso is not None:
        content["pubDate"] = pub_date_iso
    return {"content": content}


def test_yfinance_news_drops_out_of_window_and_missing_pubdate():
    from tradingagents.dataflows import yfinance_news

    # 2026-05-01 to 2026-05-07 window. One in, one after, one with no date.
    fake_news = [
        _yf_article(
            "In-window NVDA earnings beat",
            "Reuters",
            "2026-05-03T14:00:00Z",
        ),
        _yf_article(
            "After-window NVDA roadmap",
            "Bloomberg",
            "2026-05-20T14:00:00Z",
        ),
        _yf_article(
            "Untimestamped NVDA rumour",
            "Yahoo Finance",
            None,
        ),
    ]

    fake_ticker = MagicMock()
    fake_ticker.get_news.return_value = fake_news

    with patch.object(yfinance_news.yf, "Ticker", return_value=fake_ticker), \
         patch.object(yfinance_news, "yf_retry", lambda fn: fn()):
        result = yfinance_news.get_news_yfinance("NVDA", "2026-05-01", "2026-05-07")

    assert "In-window NVDA earnings beat" in result
    assert "After-window NVDA roadmap" not in result
    assert "Untimestamped NVDA rumour" not in result


def test_yfinance_news_returns_no_news_message_when_all_filtered_out():
    from tradingagents.dataflows import yfinance_news

    fake_news = [
        _yf_article("Untimestamped story", "Reuters", None),
        _yf_article("After-window story", "Reuters", "2026-12-01T00:00:00Z"),
    ]
    fake_ticker = MagicMock()
    fake_ticker.get_news.return_value = fake_news

    with patch.object(yfinance_news.yf, "Ticker", return_value=fake_ticker), \
         patch.object(yfinance_news, "yf_retry", lambda fn: fn()):
        result = yfinance_news.get_news_yfinance("NVDA", "2026-05-01", "2026-05-07")

    assert "No news found for NVDA between 2026-05-01 and 2026-05-07" in result


def test_yfinance_news_clusters_syndicated_copies():
    """Five outlets carry the same wire → one exemplar in the rendered output."""
    from tradingagents.dataflows import yfinance_news

    pub = "2026-05-03T10:00:00Z"
    fake_news = [
        _yf_article("Nvidia reports record Q3 revenue driven by AI demand", "Yahoo Finance", pub),
        _yf_article("Nvidia reports record Q3 revenue, driven by AI demand", "Seeking Alpha", pub),
        _yf_article("Nvidia reports record Q3 revenue driven by AI demand!", "Reuters", pub),
        _yf_article("NVIDIA: Record Q3 revenue, AI demand the driver", "MarketWatch", pub),
        _yf_article("Nvidia reports record Q3 revenue — AI demand drives growth", "Benzinga", pub),
    ]

    fake_ticker = MagicMock()
    fake_ticker.get_news.return_value = fake_news

    with patch.object(yfinance_news.yf, "Ticker", return_value=fake_ticker), \
         patch.object(yfinance_news, "yf_retry", lambda fn: fn()):
        result = yfinance_news.get_news_yfinance("NVDA", "2026-05-01", "2026-05-07")

    # Reuters wins on rank → it's the exemplar.
    assert "source: Reuters" in result
    # And the lower-tier syndications should be gone.
    assert "Yahoo Finance" not in result
    assert "Seeking Alpha" not in result
    assert "MarketWatch" not in result
    assert "Benzinga" not in result


# ---------------------------------------------------------------------------
# Alpha Vantage path
# ---------------------------------------------------------------------------


def _av_article(title: str, source: str, time_published: str | None) -> dict:
    return {
        "title": title,
        "url": f"https://example.com/{title.replace(' ', '-').lower()}",
        "summary": f"Summary for {title}",
        "source": source,
        "time_published": time_published,
    }


def test_alpha_vantage_get_news_drops_out_of_window_and_missing_timestamps():
    from tradingagents.dataflows import alpha_vantage_news

    payload = {
        "feed": [
            _av_article("In-window AAPL beat", "Reuters", "20260503T140000"),
            _av_article("After-window AAPL roadmap", "Bloomberg", "20260620T140000"),
            _av_article("Untimestamped AAPL rumour", "Yahoo Finance", None),
            _av_article("Malformed-timestamp AAPL story", "Reuters", "not-a-date"),
        ],
        "items": "4",
    }

    with patch.object(alpha_vantage_news, "_make_api_request", return_value=payload):
        result = alpha_vantage_news.get_news("AAPL", "2026-05-01", "2026-05-07")

    assert isinstance(result, dict)
    feed = result["feed"]
    titles = [a["title"] for a in feed]
    assert titles == ["In-window AAPL beat"]
    assert result["items"] == "1"


def test_alpha_vantage_passes_through_non_dict_error_responses():
    """If Alpha Vantage returns a non-feed payload, leave it alone."""
    from tradingagents.dataflows import alpha_vantage_news

    error_payload = {"Information": "Daily limit reached"}
    with patch.object(alpha_vantage_news, "_make_api_request", return_value=error_payload):
        result = alpha_vantage_news.get_news("AAPL", "2026-05-01", "2026-05-07")

    assert result == error_payload


def test_alpha_vantage_passes_through_unparseable_string_responses():
    from tradingagents.dataflows import alpha_vantage_news

    raw = "this is not json"
    with patch.object(alpha_vantage_news, "_make_api_request", return_value=raw):
        result = alpha_vantage_news.get_news("AAPL", "2026-05-01", "2026-05-07")

    assert result == raw


def test_alpha_vantage_global_news_caps_at_limit_after_clustering():
    from tradingagents.dataflows import alpha_vantage_news

    # 8 distinct stories in-window; ask for 3.
    feed = [
        _av_article(f"Macro story number {i} on rates and policy", "Reuters", "20260503T100000")
        for i in range(8)
    ]
    payload = {"feed": feed, "items": "8"}

    with patch.object(alpha_vantage_news, "_make_api_request", return_value=payload):
        result = alpha_vantage_news.get_global_news("2026-05-07", look_back_days=7, limit=3)

    assert isinstance(result, dict)
    assert len(result["feed"]) <= 3
    assert result["items"] == str(len(result["feed"]))


def test_alpha_vantage_string_json_response_is_parsed_and_filtered():
    from tradingagents.dataflows import alpha_vantage_news

    payload = {
        "feed": [
            _av_article("In-window MSFT beat", "Reuters", "20260503T140000"),
            _av_article("Untimestamped MSFT rumour", "Yahoo Finance", None),
        ],
        "items": "2",
    }
    raw_json = json.dumps(payload)

    with patch.object(alpha_vantage_news, "_make_api_request", return_value=raw_json):
        result = alpha_vantage_news.get_news("MSFT", "2026-05-01", "2026-05-07")

    assert isinstance(result, dict)
    assert [a["title"] for a in result["feed"]] == ["In-window MSFT beat"]
