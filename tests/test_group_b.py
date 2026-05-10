"""Tests specific to Group B — evaluation math.

Three concerns:

- B1 tolerant parser: legacy (`+2.3%`) and new (`+2.3% raw` / `+2.3% vs SPY`)
  tag formats yield identical parsed dicts.
- B2 horizon-matched window: ``_resolve_pending_entries`` reads the stored
  PM ``Time Horizon`` and passes the right holding-day count to ``_fetch_returns``.
- B3 cross-ticker resolver: ``TradingMemoryLog.resolve_all_pendings`` walks
  every pending, calls the supplied fetcher + reflector, writes a single
  atomic batch update.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from tradingagents.agents.utils.memory import TradingMemoryLog


pytestmark = pytest.mark.unit


_SEP = TradingMemoryLog._SEPARATOR


def _make_log(tmp_path):
    return TradingMemoryLog({"memory_log_path": str(tmp_path / "trading_memory.md")})


def _seed(tmp_path, raw_block: str):
    """Write a raw block (incl. trailing separator) directly to the log."""
    path = tmp_path / "trading_memory.md"
    with open(path, "a", encoding="utf-8") as f:
        f.write(raw_block + _SEP)


# ---------------------------------------------------------------------------
# B1 — tolerant parser
# ---------------------------------------------------------------------------


class TestTolerantParserB1:

    def test_legacy_tag_format_still_parses(self, tmp_path):
        """Pre-Group-B logs (`+4.2% | +2.1% | 5d`) keep loading after the rename."""
        legacy = (
            "[2026-01-10 | NVDA | Buy | +4.2% | +2.1% | 5d]\n\n"
            "DECISION:\nRating: Buy.\n\n"
            "REFLECTION:\nLegacy outcome."
        )
        _seed(tmp_path, legacy)
        log = _make_log(tmp_path)
        entries = log.load_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e["pending"] is False
        assert e["raw"] == "+4.2%"
        # Both the canonical ``excess`` key and the legacy ``alpha`` alias
        # must report the same stripped value.
        assert e["excess"] == "+2.1%"
        assert e["alpha"] == "+2.1%"
        assert e["holding"] == "5d"
        assert e["reflection"] == "Legacy outcome."

    def test_new_tag_format_parses_to_same_shape(self, tmp_path):
        """New format yields identical dict structure as legacy."""
        new = (
            "[2026-01-10 | NVDA | Buy | +4.2% raw | +2.1% vs SPY | 5d]\n\n"
            "DECISION:\nRating: Buy.\n\n"
            "REFLECTION:\nNew-format outcome."
        )
        _seed(tmp_path, new)
        log = _make_log(tmp_path)
        e = log.load_entries()[0]
        assert e["raw"] == "+4.2%"
        assert e["excess"] == "+2.1%"
        assert e["alpha"] == "+2.1%"

    def test_legacy_and_new_intermixed(self, tmp_path):
        """A log holding both formats parses cleanly with no surprises."""
        legacy = (
            "[2026-01-05 | NVDA | Buy | +4.2% | +2.1% | 5d]\n\n"
            "DECISION:\nLegacy.\n\nREFLECTION:\nLegacy outcome."
        )
        new = (
            "[2026-01-12 | AAPL | Sell | -3.0% raw | -1.2% vs SPY | 21d]\n\n"
            "DECISION:\nNew.\n\nREFLECTION:\nNew outcome."
        )
        _seed(tmp_path, legacy)
        _seed(tmp_path, new)
        log = _make_log(tmp_path)
        entries = log.load_entries()
        assert len(entries) == 2
        assert entries[0]["excess"] == "+2.1%"
        assert entries[1]["excess"] == "-1.2%"

    def test_new_writes_use_annotated_format(self, tmp_path):
        """update_with_outcome writes the annotated tag shape, not the legacy one."""
        log = _make_log(tmp_path)
        log.store_decision("NVDA", "2026-01-10", "Rating: Buy")
        log.update_with_outcome("NVDA", "2026-01-10", 0.05, 0.02, 5, "Reflection")
        text = (tmp_path / "trading_memory.md").read_text(encoding="utf-8")
        assert "+5.0% raw" in text
        assert "+2.0% vs SPY" in text


# ---------------------------------------------------------------------------
# B2 — horizon-matched window
# ---------------------------------------------------------------------------


class TestHorizonMatchedWindowB2:

    def test_resolve_pending_uses_decision_time_horizon(self, tmp_path):
        """A '1 month' decision should drive a 21-day _fetch_returns call."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        log = _make_log(tmp_path)
        decision = (
            "**Rating**: Buy\n\n"
            "**Executive Summary**: Build position over a quarter.\n\n"
            "**Investment Thesis**: AI capex tailwind.\n\n"
            "**Time Horizon**: 1 month"
        )
        log.store_decision("NVDA", "2026-01-10", decision)

        # Capture the holding_days the resolver passes through to _fetch_returns.
        captured = {}

        def fake_fetch_returns(self, ticker, trade_date, holding_days=5):
            captured["holding_days"] = holding_days
            return 0.05, 0.02, holding_days

        mock_graph = MagicMock(spec=TradingAgentsGraph)
        mock_graph.memory_log = log
        mock_graph.reflector = MagicMock()
        mock_graph.reflector.reflect_on_final_decision.return_value = "Reflection."
        mock_graph._fetch_returns = lambda t, d, hd: fake_fetch_returns(None, t, d, hd)

        TradingAgentsGraph._resolve_pending_entries(mock_graph, "NVDA")

        assert captured["holding_days"] == 21  # 1 month → 21 trading days

    def test_resolve_pending_falls_back_to_5_when_no_horizon(self, tmp_path):
        """A decision with no Time Horizon line keeps the 5-day default."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        log = _make_log(tmp_path)
        decision = "**Rating**: Buy\n\n**Executive Summary**: Move quickly."
        log.store_decision("NVDA", "2026-01-10", decision)

        captured = {}

        def fake_fetch_returns(t, d, hd):
            captured["holding_days"] = hd
            return 0.05, 0.02, hd

        mock_graph = MagicMock(spec=TradingAgentsGraph)
        mock_graph.memory_log = log
        mock_graph.reflector = MagicMock()
        mock_graph.reflector.reflect_on_final_decision.return_value = "Reflection."
        mock_graph._fetch_returns = fake_fetch_returns

        TradingAgentsGraph._resolve_pending_entries(mock_graph, "NVDA")

        assert captured["holding_days"] == 5

    def test_resolved_entry_records_actual_holding_days(self, tmp_path):
        """The persisted tag's `Nd` field reflects the horizon-matched window."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        log = _make_log(tmp_path)
        decision = "**Rating**: Buy\n\n**Time Horizon**: 1-3 months"
        log.store_decision("NVDA", "2026-01-10", decision)

        mock_graph = MagicMock(spec=TradingAgentsGraph)
        mock_graph.memory_log = log
        mock_graph.reflector = MagicMock()
        mock_graph.reflector.reflect_on_final_decision.return_value = "Reflection."
        # Resolve at midpoint of 1-3 months = 2 months = 42 trading days.
        mock_graph._fetch_returns = lambda t, d, hd: (0.10, 0.04, hd)

        TradingAgentsGraph._resolve_pending_entries(mock_graph, "NVDA")

        text = (tmp_path / "trading_memory.md").read_text(encoding="utf-8")
        assert "42d" in text
        assert "+10.0% raw" in text
        assert "+4.0% vs SPY" in text


# ---------------------------------------------------------------------------
# B3 — cross-ticker resolver
# ---------------------------------------------------------------------------


class TestCrossTickerResolverB3:

    def test_resolve_all_pendings_walks_every_ticker(self, tmp_path):
        log = _make_log(tmp_path)
        log.store_decision("NVDA", "2026-01-05", "Rating: Buy")
        log.store_decision("AAPL", "2026-01-06", "Rating: Sell")
        log.store_decision("MSFT", "2026-01-07", "Rating: Hold")

        fetched = []
        def fetch(ticker, trade_date, holding_days):
            fetched.append((ticker, trade_date, holding_days))
            return 0.05, 0.02, holding_days

        def reflect(decision, raw, excess):
            return f"Reflection for raw={raw} excess={excess}."

        result = log.resolve_all_pendings(fetch, reflect)

        assert result == {"resolved": 3, "skipped": 0}
        assert {t for t, _, _ in fetched} == {"NVDA", "AAPL", "MSFT"}
        assert log.get_pending_entries() == []

    def test_resolve_all_pendings_skips_unavailable(self, tmp_path):
        """When fetch returns (None, None, None), the entry stays pending."""
        log = _make_log(tmp_path)
        log.store_decision("NVDA", "2026-01-05", "Rating: Buy")
        log.store_decision("AAPL", "2026-01-06", "Rating: Sell")

        def fetch(ticker, trade_date, holding_days):
            if ticker == "AAPL":
                return None, None, None
            return 0.05, 0.02, holding_days

        result = log.resolve_all_pendings(fetch, lambda d, r, e: "OK.")

        assert result == {"resolved": 1, "skipped": 1}
        pending = log.get_pending_entries()
        assert len(pending) == 1
        assert pending[0]["ticker"] == "AAPL"

    def test_resolve_all_pendings_uses_horizon_for(self, tmp_path):
        """horizon_for callable is invoked per entry to pick the window."""
        log = _make_log(tmp_path)
        log.store_decision("NVDA", "2026-01-05", "Rating: Buy")
        log.store_decision("AAPL", "2026-01-06", "Rating: Sell")

        used_holding = {}
        def fetch(ticker, trade_date, holding_days):
            used_holding[ticker] = holding_days
            return 0.05, 0.02, holding_days

        def horizon_for(entry):
            # NVDA → 21 days, AAPL → 63 days.
            return 21 if entry["ticker"] == "NVDA" else 63

        log.resolve_all_pendings(fetch, lambda d, r, e: "OK.", horizon_for=horizon_for)

        assert used_holding == {"NVDA": 21, "AAPL": 63}

    def test_resolve_all_pendings_atomic_single_write(self, tmp_path):
        """Multiple resolutions land in one batch write — assert no .tmp leftover."""
        log = _make_log(tmp_path)
        for i, ticker in enumerate(["NVDA", "AAPL", "MSFT"]):
            log.store_decision(ticker, f"2026-01-{i+5:02d}", "Rating: Buy")

        log.resolve_all_pendings(
            lambda t, d, h: (0.05, 0.02, h),
            lambda d, r, e: "OK.",
        )

        assert not (tmp_path / "trading_memory.tmp").exists()
        entries = log.load_entries()
        assert len(entries) == 3
        assert all(not e["pending"] for e in entries)

    def test_resolve_all_pendings_noop_when_empty(self, tmp_path):
        log = _make_log(tmp_path)
        result = log.resolve_all_pendings(
            lambda *a: (None, None, None),
            lambda *a: "",
        )
        assert result == {"resolved": 0, "skipped": 0}

    def test_resolve_all_pendings_noop_when_no_log_path(self):
        log = TradingMemoryLog(config=None)
        result = log.resolve_all_pendings(
            lambda *a: (None, None, None),
            lambda *a: "",
        )
        assert result == {"resolved": 0, "skipped": 0}
