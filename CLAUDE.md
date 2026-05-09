# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install (editable):** `pip install -e .` — registers the `tradingagents` console script defined in `pyproject.toml`.

**Run the CLI:** `tradingagents` (or `python -m cli.main`). Subcommand: `tradingagents analyze` with optional `--checkpoint` and `--clear-checkpoints` flags. The plain `tradingagents` entry walks the user through ticker, date, provider, and analyst selection interactively.

**Run a single propagation from Python:** edit and run `python main.py` (single-ticker example with `TradingAgentsGraph(...).propagate("NVDA", "YYYY-MM-DD")`).

**Tests:** `pytest tests/` runs the full suite. Strict markers are configured in `pyproject.toml`: `unit`, `integration`, `smoke`. Filter with `pytest -m unit` or run a single file with `pytest tests/test_memory_log.py -k name`. The `conftest.py` autouse fixture sets placeholder values for every provider API key, so tests run cleanly without real credentials — never assume API keys are present in test code.

**Structured-output smoke check:** `python scripts/smoke_structured_output.py` exercises the three structured agents (Research Manager, Trader, Portfolio Manager) against the configured provider — the right diagnostic when a provider integration breaks.

**Docker:** `docker compose run --rm tradingagents` (uses the `Dockerfile` two-stage build). The image's entrypoint is the `tradingagents` CLI directly.

## Architecture

### The pipeline is a LangGraph state graph, not a procedural script

`TradingAgentsGraph.propagate(ticker, date)` in `tradingagents/graph/trading_graph.py` is the only public entry point. It:

1. Resolves any pending memory-log entries for the same ticker (deferred reflection from prior runs).
2. Optionally compiles the workflow with a per-ticker SqliteSaver checkpointer.
3. Streams or invokes the compiled `StateGraph` defined in `tradingagents/graph/setup.py`.
4. Persists state JSON, appends a `pending` entry to the decision log, clears the checkpoint on success.

The graph itself is wired in `GraphSetup.setup_graph` (`tradingagents/graph/setup.py`): analysts run sequentially in a tools-loop (each may call its `ToolNode` then re-enter itself until no tool calls remain), then Bull/Bear researchers debate, then Research Manager → Trader → 3-way risk debate (Aggressive ↔ Conservative ↔ Neutral) → Portfolio Manager → END. **Conditional edges in `tradingagents/graph/conditional_logic.py` exit on round count only — no convergence check.** `should_continue_debate` exits when `count >= 2 * max_debate_rounds`; `should_continue_risk_analysis` exits when `count >= 3 * max_risk_discuss_rounds`.

### Three layers of indirection that look duplicative but aren't

- **LLM provider abstraction** — `tradingagents/llm_clients/factory.py` dispatches by provider name. OpenAI-compatible providers (`openai`, `xai`, `deepseek`, `qwen`, `glm`, `ollama`, `openrouter`) all share `OpenAIClient`. Anthropic, Google, and Azure each have their own client. Provider-specific knobs (`google_thinking_level`, `openai_reasoning_effort`, `anthropic_effort`) are funnelled through `TradingAgentsGraph._get_provider_kwargs`. **`backend_url` defaults to `None` on purpose** — each client falls back to its native default; setting it to OpenAI's URL leaks into Gemini/Anthropic and breaks them.

- **Data vendor abstraction** — `tradingagents/dataflows/interface.py` defines `VENDOR_METHODS`, a method-name → vendor → callable map. `route_to_vendor()` resolves a method's vendor via `data_vendors` (category-level) or `tool_vendors` (per-tool override) from config, with **automatic fallback only on `AlphaVantageRateLimitError`** — other exceptions propagate. Adding a vendor means adding implementations to each `VENDOR_METHODS` row, not subclassing.

- **Structured agent outputs** — `tradingagents/agents/schemas.py` defines `ResearchPlan`, `TraderProposal`, `PortfolioDecision` as the contract between the three decision agents. Each provider's native structured-output mode is used (json_schema for OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic). The `render_*` helpers in the same file emit markdown with **specific section headers (`**Rating**`, `**Executive Summary**`, `**Investment Thesis**`, the trailing `FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`)** that the signal processor, memory log, CLI display, and any downstream grep depend on. Don't change those headers without auditing every reader.

### Persistent decision log conventions (`tradingagents/agents/utils/memory.py`)

Path: `~/.tradingagents/memory/trading_memory.md` (override with `TRADINGAGENTS_MEMORY_LOG_PATH`). Append-only markdown. **Entries are separated by the literal string `\n\n<!-- ENTRY_END -->\n\n`** — chosen because LLMs cannot emit HTML comments, making it a safe hard delimiter. Tag-line format is one of:

- pending: `[YYYY-MM-DD | TICKER | Rating | pending]`
- resolved: `[YYYY-MM-DD | TICKER | Rating | +N.N% | +N.N% | Nd]` (raw, alpha-vs-SPY, holding days)

Updates use a temp-file + `os.replace()` atomic write — never write to the log file in place. Reflection on a decision is **deferred**: the same-ticker next run fetches realised return + alpha and writes a 2-4 sentence reflection. Cross-ticker pendings only resolve when *that* ticker is run again. `_fetch_returns` in `tradingagents/graph/trading_graph.py` hardcodes a **5 trading-day** window and computes `alpha = raw - spy_ret` (excess return, not true alpha — beta-unadjusted). The PM's `time_horizon` field is not currently consulted by the reflection window.

### Path-traversal guard

Any value that becomes a filesystem component (ticker symbols, anywhere) **must** pass through `safe_ticker_component()` in `tradingagents/dataflows/utils.py` first. It allows `[A-Za-z0-9._\-\^]+` up to 32 chars and rejects all-dots strings. Tickers come from CLI input *and* LLM tool calls, so this is a security boundary, not a UX nicety. Already enforced in `_log_state` and `_db_path` (checkpointer); preserve that whenever you introduce a new on-disk artefact keyed by ticker.

### Checkpointing

Opt-in via `config["checkpoint_enabled"] = True` or `--checkpoint`. **Per-ticker SQLite databases** at `~/.tradingagents/cache/checkpoints/<TICKER>.db` (override base with `TRADINGAGENTS_CACHE_DIR`). Thread ID is `sha256("{TICKER}:{date}")[:16]` — same ticker+date resumes, different date starts fresh. Successful completion calls `clear_checkpoint`; do not skip that on new pipeline branches or stale state will be resumed into a different code path.

### Things that look removable but aren't

- `from tradingagents.agents import *` in `trading_graph.py` and `setup.py` works only because `tradingagents/agents/__init__.py` curates `__all__`. Adding a new agent factory requires adding it to that `__all__`.
- `build/lib/` is a stale duplicate of the package (committed by accident). Don't import from it; don't update it when modifying `tradingagents/`. Treat it as gitignore-eligible cruft, not a second source of truth.
- The CLI in `cli/main.py` calls `load_dotenv(".env.enterprise", override=False)` after the default `load_dotenv()` — `.env.enterprise.example` is the template for Azure / Bedrock-style enterprise credentials, intentionally separate from the regular `.env`.

### Configuration entry points

- `tradingagents/default_config.py` — runtime defaults; copy with `DEFAULT_CONFIG.copy()` before mutating.
- `tradingagents/dataflows/config.py` — `set_config()` is called once during `TradingAgentsGraph.__init__` so the dataflow layer can read `data_vendors` / `tool_vendors` without being passed config explicitly. If you instantiate `TradingAgentsGraph` more than once with different configs in the same process, the last one wins for the dataflow layer.

### State shape

`AgentState` (TypedDict) in `tradingagents/agents/utils/agent_states.py` defines the keys every node reads/writes. Reports flow as strings: `market_report`, `sentiment_report`, `news_report`, `fundamentals_report`, `investment_plan` (Research Manager), `trader_investment_plan` (Trader), `final_trade_decision` (Portfolio Manager). The two debate sub-states (`investment_debate_state`, `risk_debate_state`) carry their own `count`, `history`, and per-speaker history strings.

## Cross-platform notes

- All file I/O passes explicit `encoding="utf-8"` — Windows defaults to cp1252 and silently corrupts non-ASCII content otherwise. Match that pattern in any new I/O.
- Cache, log, and memory directories all live under `~/.tradingagents/` so the Docker image's non-root user can write to them.

## Subagents (Claude Code)

Project-scoped subagents live in [.claude/agents/](.claude/agents/) and are invoked via the `Agent` tool. Use them to keep large read-only audits out of the main session's context window.

- **`docs-sync`** — read-only auditor that verifies every concrete claim in this file (paths, line numbers, function names, format strings, conventions) against the current code and reports drift. **Invoke before** merging any PR that touches a file mentioned here, **after** any rename or signature change to a function named here, or when starting a session that will make architectural changes. It does not edit this file — it produces a drift report; the main session decides what to update.
- **`news-pipeline-tester`** — read-only diagnostic for the Group A news pipeline (`tradingagents/dataflows/news_dedup.py` + `source_rank.py`). Given a fixture, it runs `cluster_articles` and explains the cluster assignments, exemplar choices, and rejected articles with concrete Jaccard scores and `(rank, recency)` tuples. **Invoke when** tuning `DEFAULT_JACCARD_THRESHOLD`, expanding `SOURCE_RANK`, debugging "why did this article win/lose" reports, or sanity-checking a behaviour change before opening a PR.

More subagents land alongside their respective code groups: a state tracer (Group C), data-vendor auditor (Group B), memory-log validator (Group B1), provider smoke (when schemas change), and backtest grid runner (Group D).
