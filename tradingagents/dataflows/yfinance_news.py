"""yfinance-based news data fetching functions."""

import yfinance as yf
from datetime import datetime
from dateutil.relativedelta import relativedelta

from .news_dedup import cluster_articles
from .stockstats_utils import yf_retry


def _extract_article_data(article: dict) -> dict:
    """Extract article data from yfinance news format (handles nested 'content' structure)."""
    # Handle nested content structure
    if "content" in article:
        content = article["content"]
        title = content.get("title", "No title")
        summary = content.get("summary", "")
        provider = content.get("provider", {})
        publisher = provider.get("displayName", "Unknown")

        # Get URL from canonicalUrl or clickThroughUrl
        url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        link = url_obj.get("url", "")

        # Get publish date
        pub_date_str = content.get("pubDate", "")
        pub_date = None
        if pub_date_str:
            try:
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return {
            "title": title,
            "summary": summary,
            "publisher": publisher,
            "link": link,
            "pub_date": pub_date,
        }
    else:
        # Fallback for flat structure
        return {
            "title": article.get("title", "No title"),
            "summary": article.get("summary", ""),
            "publisher": article.get("publisher", "Unknown"),
            "link": article.get("link", ""),
            "pub_date": None,
        }


def _within_window(pub_date, start_dt: datetime, end_dt: datetime) -> bool:
    """Strict point-in-time guard.

    Articles without a parseable ``pub_date`` are rejected — under the prior
    behaviour they leaked past the as-of cutoff, contaminating backtests
    with retroactively indexed coverage.
    """
    if pub_date is None:
        return False
    naive = pub_date.replace(tzinfo=None) if hasattr(pub_date, "tzinfo") else pub_date
    return start_dt <= naive <= end_dt


def _format_articles(articles: list[dict]) -> str:
    """Render the cluster-exemplar articles to the markdown shape the LLM consumes."""
    parts: list[str] = []
    for data in articles:
        parts.append(f"### {data['title']} (source: {data['publisher']})")
        if data.get("summary"):
            parts.append(data["summary"])
        if data.get("link"):
            parts.append(f"Link: {data['link']}")
        parts.append("")  # blank line between articles
    return "\n".join(parts)


def get_news_yfinance(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    Retrieve news for a specific stock ticker using yfinance.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        Formatted string containing news articles
    """
    try:
        stock = yf.Ticker(ticker)
        news = yf_retry(lambda: stock.get_news(count=20))

        if not news:
            return f"No news found for {ticker}"

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        # End-of-day on end_date is the strict upper bound. Articles with no
        # pub_date are dropped by _within_window, not granted slack.
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + relativedelta(hours=23, minutes=59, seconds=59)

        in_window = [
            data for data in (_extract_article_data(a) for a in news)
            if _within_window(data["pub_date"], start_dt, end_dt)
        ]

        clustered = cluster_articles(in_window)
        if not clustered:
            return f"No news found for {ticker} between {start_date} and {end_date}"

        return f"## {ticker} News, from {start_date} to {end_date}:\n\n{_format_articles(clustered)}"

    except Exception as e:
        return f"Error fetching news for {ticker}: {str(e)}"


def get_global_news_yfinance(
    curr_date: str,
    look_back_days: int = 7,
    limit: int = 10,
) -> str:
    """
    Retrieve global/macro economic news using yfinance Search.

    Args:
        curr_date: Current date in yyyy-mm-dd format
        look_back_days: Number of days to look back
        limit: Maximum number of articles to return

    Returns:
        Formatted string containing global news articles
    """
    # Search queries for macro/global news
    search_queries = [
        "stock market economy",
        "Federal Reserve interest rates",
        "inflation economic outlook",
        "global markets trading",
    ]

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    end_dt = curr_dt + relativedelta(hours=23, minutes=59, seconds=59)
    start_dt = curr_dt - relativedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    collected: list[dict] = []

    try:
        # Pull more aggressively than ``limit`` because clustering will collapse
        # syndicated copies of the same wire — without overshoot the final list
        # is too short.
        per_query = max(limit * 2, 20)
        for query in search_queries:
            search = yf_retry(lambda q=query: yf.Search(
                query=q,
                news_count=per_query,
                enable_fuzzy_query=True,
            ))
            if not search.news:
                continue
            for article in search.news:
                data = _extract_article_data(article)
                if not _within_window(data["pub_date"], start_dt, end_dt):
                    continue
                collected.append(data)

        if not collected:
            return f"No global news found for {curr_date}"

        clustered = cluster_articles(collected)[:limit]
        if not clustered:
            return f"No global news found for {curr_date}"

        return f"## Global Market News, from {start_date} to {curr_date}:\n\n{_format_articles(clustered)}"

    except Exception as e:
        return f"Error fetching global news: {str(e)}"
