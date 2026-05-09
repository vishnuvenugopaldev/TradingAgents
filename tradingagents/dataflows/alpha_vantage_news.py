import json
from datetime import datetime
from typing import Any

from .alpha_vantage_common import _make_api_request, format_datetime_for_api
from .news_dedup import cluster_articles


# Alpha Vantage NEWS_SENTIMENT timestamps look like ``YYYYMMDDTHHMMSS``.
_AV_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S"


def _parse_av_timestamp(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _AV_TIMESTAMP_FORMAT)
    except ValueError:
        return None


def _normalize_av_article(item: dict[str, Any]) -> dict[str, Any]:
    """Map Alpha Vantage's NEWS_SENTIMENT row onto the news_dedup contract."""
    return {
        "title": item.get("title"),
        "publisher": item.get("source") or item.get("source_domain"),
        "pub_date": _parse_av_timestamp(item.get("time_published")),
        # Pass-through fields preserved so downstream consumers (or callers
        # that re-serialize to JSON) keep the AV payload they expect.
        "url": item.get("url"),
        "summary": item.get("summary"),
        "topics": item.get("topics"),
        "overall_sentiment_score": item.get("overall_sentiment_score"),
        "overall_sentiment_label": item.get("overall_sentiment_label"),
        "ticker_sentiment": item.get("ticker_sentiment"),
        "time_published": item.get("time_published"),
        "source": item.get("source"),
    }


def _filter_and_cluster(
    response: dict | str,
    start_dt: datetime,
    end_dt: datetime,
) -> dict | str:
    """Apply point-in-time filter + dedup/clustering to a NEWS_SENTIMENT response.

    Falls back to the raw response on any parse problem — Alpha Vantage's
    error responses come back as JSON dicts with no ``feed`` key, and we
    must not swallow them.
    """
    payload: Any
    if isinstance(response, str):
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            return response
    else:
        payload = response

    if not isinstance(payload, dict) or "feed" not in payload:
        return response

    feed = payload.get("feed") or []
    if not isinstance(feed, list):
        return response

    in_window = []
    for item in feed:
        if not isinstance(item, dict):
            continue
        norm = _normalize_av_article(item)
        if norm["pub_date"] is None:
            # No parseable timestamp → drop. Same point-in-time rule as the
            # yfinance path: missing ``time_published`` would otherwise leak
            # past the as-of cutoff.
            continue
        if not (start_dt <= norm["pub_date"] <= end_dt):
            continue
        in_window.append(norm)

    clustered = cluster_articles(in_window)
    payload = dict(payload)
    payload["feed"] = clustered
    payload["items"] = str(len(clustered))
    return payload


def get_news(ticker, start_date, end_date) -> dict[str, Any] | str:
    """Returns live and historical market news & sentiment data from premier news outlets worldwide.

    Covers stocks, cryptocurrencies, forex, and topics like fiscal policy, mergers & acquisitions, IPOs.

    Args:
        ticker: Stock symbol for news articles.
        start_date: Start date for news search.
        end_date: End date for news search.

    Returns:
        Dictionary containing news sentiment data or JSON string.
    """

    params = {
        "tickers": ticker,
        "time_from": format_datetime_for_api(start_date),
        "time_to": format_datetime_for_api(end_date),
    }

    response = _make_api_request("NEWS_SENTIMENT", params)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    return _filter_and_cluster(response, start_dt, end_dt)


def get_global_news(curr_date, look_back_days: int = 7, limit: int = 50) -> dict[str, Any] | str:
    """Returns global market news & sentiment data without ticker-specific filtering.

    Covers broad market topics like financial markets, economy, and more.

    Args:
        curr_date: Current date in yyyy-mm-dd format.
        look_back_days: Number of days to look back (default 7).
        limit: Maximum number of articles (default 50).

    Returns:
        Dictionary containing global news sentiment data or JSON string.
    """
    from datetime import timedelta

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - timedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    params = {
        "topics": "financial_markets,economy_macro,economy_monetary",
        "time_from": format_datetime_for_api(start_date),
        "time_to": format_datetime_for_api(curr_date),
        "limit": str(limit),
    }

    response = _make_api_request("NEWS_SENTIMENT", params)

    end_dt = curr_dt.replace(hour=23, minute=59, second=59)
    filtered = _filter_and_cluster(response, start_dt, end_dt)
    if isinstance(filtered, dict) and isinstance(filtered.get("feed"), list):
        # ``limit`` is the post-clustering cap so the LLM sees that many
        # distinct stories, not raw rows.
        filtered = dict(filtered)
        filtered["feed"] = filtered["feed"][:limit]
        filtered["items"] = str(len(filtered["feed"]))
    return filtered


def get_insider_transactions(symbol: str) -> dict[str, str] | str:
    """Returns latest and historical insider transactions by key stakeholders.

    Covers transactions by founders, executives, board members, etc.

    Args:
        symbol: Ticker symbol. Example: "IBM".

    Returns:
        Dictionary containing insider transaction data or JSON string.
    """

    params = {
        "symbol": symbol,
    }

    return _make_api_request("INSIDER_TRANSACTIONS", params)
