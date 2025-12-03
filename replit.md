# Kraken Trading Bot - Self-Learning AI Edition

## Overview
This project is an intelligent, self-learning cryptocurrency trading bot designed for the Kraken exchange. Its core purpose is to provide autonomous, fee-aware, market-only trading capabilities, continuously improve through self-learning, and offer a conversational AI interface. The bot features robust risk management, rate limiting, and a daily loss kill-switch, aiming to monitor markets 24/7 and manage the entire trading workload. The ambition is to automate trading completely, allowing users to focus on higher-level strategy. The system has recently pivoted to pure market-only execution due to Kraken API limitations, implementing a professional "mental stop-loss/take-profit" system.

## User Preferences
- User prefers to be called: jimmy
- Zin's role: Financial servant who does ALL the work FOR jimmy
- Zin handles the entire trading workload autonomously so jimmy doesn't have to

## System Architecture

### UI/UX Decisions
The bot provides a chat interface on port 5000 for real-time interaction and a dashboard for displaying accurate trading status, open positions, balances, and P&L directly from Kraken data.

### Technical Implementations
The system emphasizes mode isolation (LIVE vs. PAPER). Key architectural components include:

-   **Execution Mode System**: Supports multiple execution strategies via EXECUTION_MODE env var.
    -   **Execution Manager (`execution_manager.py`)**: Centralized order execution with rate limiting, fee logging, and telemetry. Features `execute_entry_with_mode()` router that dispatches to appropriate execution strategy:
        - `MARKET_ONLY` (default): Pure market buy/sell via `execute_market_entry()`
        - `LIMIT_BRACKET`: Limit-maker entries with bracket TP/SL via `execute_limit_bracket_entry()` - **PHASE 2A+2B COMPLETE (Nov 2025)**:
          - Maker-friendly pricing: BUY orders 0.2% below market, SELL orders 0.2% above (configurable via LIMIT_OFFSET_PCT)
          - Timeout/retry loop: 120s timeout per attempt (LIMIT_TIMEOUT_SECONDS), up to 3 retries (LIMIT_MAX_RETRIES)
          - Safe order cancellation with Kraken eventual consistency handling
          - Optional market fallback (LIMIT_FALLBACK_TO_MARKET=1) when all limit retries exhaust
          - Fee reduction target: 0.16% maker fee vs 0.26% taker fee
          - **Phase 2B-1**: Position tracker integration - bracket entries now tracked in `position_tracker.py` with real SL/TP prices (not recalculated mental levels)
          - **Phase 2B-2**: OCO monitor cleans up position_tracker when TP/SL orders fill - dual tracking synchronized (pending_child_orders in SQLite AND open_positions.json for dashboard)
          - Fill data extraction improved to handle nested `fill_data` structure from `bracket_order_manager.py`
          - **Phase 2B PARTIAL FILL HOTFIX (Nov 2025)**: Fixed critical bug where partial fills created multiple SL orders and missing TP:
            - Schema: Added `filled_qty`, `total_qty`, `bracket_initialized` to pending_child_orders table
            - Cumulative fill tracking: Uses Kraken's cumulative `filled` value (not incremental) with 99% threshold
            - One-time TP placement: `bracket_initialized=1` flag prevents duplicate TP orders
            - Multi-SL handling: OCO monitor now cancels ALL SL orders (not just one) when TP fills
            - Diagnostic logging: Warning when multiple SLs detected (expected with partial fills)
        - `BRACKET`: Alias for LIMIT_BRACKET mode
    -   **OCO Monitor (`oco_monitor.py`)**: Synthetic OCO (One-Cancels-Other) for LIMIT_BRACKET mode - when TP fills, cancels SL and cleans up position_tracker; when SL fills, cancels TP and cleans up position_tracker. Runs in reconciliation cycle.
    -   Handles settlement polling with exponential backoff for accurate fill data.
    -   **Position Tracker (`position_tracker.py`)**: Implements "mental stop-loss/take-profit" using ATR-based levels (3x ATR for SL, 4.5x for TP - widened Nov 2025 to reduce stop-outs). Monitors positions and triggers market SELL. Uses `portalocker` for interprocess synchronization. Includes stop validation warning if ATR compression creates unexpectedly tight stops.
    -   **Fee Model (`fee_model.py`)**: Tracks real-time Kraken fees with caching, enabling fee-adjusted profitability checks. **PHASE 3A (Nov 2025)**: Added `compute_required_edge_pct()` - calculates execution-mode-aware minimum edge requirements:
          - MARKET_ONLY: round_trip = taker_fee * 2 (both entry and exit are market orders)
          - LIMIT_BRACKET: round_trip = maker_fee + max(maker_fee, taker_fee) (entry is maker, exit could be either)
          - Safety multiplier applied (default 1.5x) for profitability buffer
    -   **Rate Limiter (`rate_limiter.py`)**: Enforces API call limits.
    -   **Market Position Sizing**: SL-independent position sizing (fixed-fraction or synthetic ATR-based) with a 10% max position cap.
    -   **Fee-Adjusted Edge Check**: Pre-trade validation requiring sufficient edge after round-trip fees and safety margin.
    -   **Dust Prevention**: Comprehensive system to prevent trading below Kraken's minimum order sizes, including pre-flight validation and warnings.
-   **Account State (`account_state.py`)**: Provides mode-aware account data, ensuring isolation between LIVE and PAPER trading, with paper trading state persisted in `paper_ledger.json`.
-   **Self-Learning Components**:
    -   **Telemetry Database (`telemetry_db.py`)**: SQLite for persistent storage of all trade data, decisions, errors, and conversations for continuous learning.
    -   **LLM Agent (`llm_agent.py`)**: Integrates OpenAI GPT-4o for natural language command execution with anti-hallucination safeguards and real-time market data.
    -   **Trade Result Validator (`trade_result_validator.py`)**: Multi-layered anti-hallucination system to validate LLM claims against actual Kraken execution.
    -   **Evaluation Log (`evaluation_log.db`)**: SQLite database for transparent logging of trading decisions and executed orders.
    -   **Data Vault (`data_logger.py`)**: Centralized JSONL-based logging system for long-term analysis and self-iteration. **IMPLEMENTED (Dec 2025)**:
          - Directory structure: `/data/{trades, decisions, daily, meta, anomalies, snapshots}` (gitignored)
          - `log_trade()`: Complete trade lifecycle (entry/exit with P&L, fees, regime, decision_id reference)
          - `log_decision()`: Every market evaluation with indicators, regime, filters, and outcome
          - `log_daily_summary()`: Daily performance stats (trades, win rate, P&L, drawdown)
          - `log_version()`: Version history with config snapshots for A/B testing
          - `log_anomaly()`: Error events and unusual conditions
          - `compute_daily_stats()`: Aggregates trades for daily summary generation
          - Integrated with: autopilot startup, strategy_orchestrator (decision logging), execution_manager (trade logging), discord_notifications (daily summary logging)
          - Version tracking via `ZIN_VERSION` constant in trading_config.py
    -   **Snapshot System (`snapshot_builder.py`)**: Periodic state snapshots for self-analysis and strategy evolution. **IMPLEMENTED (Dec 2025)**:
          - Location: `data/snapshots/YYYYMMDDTHH-MM-SSZ_snapshot.json` (individual JSON files)
          - Frequency: ~3 snapshots per day in LIVE mode only (8-hour minimum interval)
          - Scheduling: `maybe_take_snapshot()` called from autopilot main loop
          - Snapshot contents:
            - **Metadata**: logged_at, zin_version, mode, snapshot_id, date
            - **Account**: total_equity_usd, cash_balance_usd, balances dict, unrealized_pnl_usd/pct
            - **Risk Config**: regime_min_atr_pct, min_confidence, regime_override_confidence, breakout_boost, symbol_whitelist, execution_mode, enable_shorts, validate_only
            - **Open Positions**: Array of positions with symbol, side, size_base/quote, entry_price, current_price, mental_stop_loss, mental_take_profit, unrealized_pnl
            - **Performance Summary**: total_trades_today, win_rate_today, realized_pnl_today_usd, max_drawdown_today_pct
            - **System Health**: last_decision_timestamp, open_positions_count, has_recent_anomaly
          - Purpose: Future self-analysis, strategy evolution, and understanding historical bot behavior
          - Testing: `force_snapshot()` bypasses scheduling for manual snapshots
-   **Trading Components**:
    -   **Autopilot (`autopilot.py`)**: Autonomous trading loop executing a 5-minute closed-candle strategy, monitoring mental SL/TP levels and integrating risk gatekeepers.
    -   **Trading Config (`trading_config.py`)**: Centralized configuration for indicators, market filters, risk parameters, and execution mode, supporting environment variable overrides.
    -   **Signal Engine (`signal_engine.py`)**: Multi-signal decision engine using technical filters (RSI, SMA, volume, volatility, chop, ATR).
    -   **Strategy Orchestrator (`strategy_orchestrator.py`)**: Regime-aware strategy selection with IMPROVED pullback detection (Nov 2025) - requires 0.75 ATR retrace from swing high, price at/below SMA20, RSI < 65 for entries. **PHASE 3A**: Fee gate filter integrated - all actionable signals pass through `_apply_fee_gate()` which computes expected edge vs required edge (fees + safety multiplier) and logs decisions to evaluation_log. Set `FEE_GATE_ENABLED=1` to block low-edge trades. **PHASE 3B (Nov 2025)**: Pre-trade filtering pipeline with decision statistics:
          - Symbol filter: `SYMBOL_WHITELIST` (comma-separated, e.g., "BTC,ETH") restricts trading to listed symbols; `SYMBOL_BLACKLIST` blocks specific symbols
          - Regime filter (set `REGIME_FILTER_ENABLED=1`): `REGIME_MIN_ATR_PCT` (default 0.2%), `REGIME_MIN_VOLUME_USD` (default $10k), `REGIME_TREND_REQUIRED` (requires ADX>20)
          - ATR soft warning: Logs warning for marginal volatility (0.2-0.3% ATR) while still allowing trades
          - Decision stats tracking: Logs filter block counts every 50 evaluations (`get_decision_stats()`, `log_decision_stats()`)
          - Filter order: Symbol → Regime → Fee Gate → Confidence Gate → Strategy logic
          - All filters disabled by default for unchanged behavior
          - **PHASE 3C (Nov 2025)**: Consistency fixes from architecture audit:
            - Volume passthrough: autopilot now fetches 24h quoteVolume via ticker and passes to regime filter
            - Decision stats fix: Removed duplicate hold_signals increments from filter helpers (single increment in generate_signal only)
            - Lockfile hardening: Added `_ensure_lockfile_exists()` helper to position_tracker for race condition prevention
          - **PHASE 3D - Confidence-Based Decision Engine (Dec 2025)**: Intelligent trade gating based on signal confidence:
            - Min confidence threshold: 0.65 (trades below this blocked via `_apply_confidence_gate()`)
            - Regime override: Confidence >= 0.75 allows trades despite unfavorable HTF regime
            - Regime penalty: -0.15 applied when HTF trend opposes trade direction
            - Breakout boost: +0.1 when RSI > 70, ADX > 25, price > SMA20
            - Symbol whitelist expanded to 11 assets: BTC,ETH,SOL,UNI,ATOM,LTC,ARB,OP,AAVE,FIL,NEAR (Dec 2025)
            - Configurable via: MIN_CONFIDENCE_THRESHOLD, REGIME_OVERRIDE_CONFIDENCE, REGIME_PENALTY, BREAKOUT_BOOST env vars
    -   **Paper Trading (`paper_trading.py`)**: Complete simulation system with realistic fills, slippage, fees, and P&L calculation.
    -   **Exchange Manager (`exchange_manager.py`)**: Singleton wrapper for `ccxt` instances, ensuring consistent data fetching.
    -   **Risk Manager (`risk_manager.py`)**: Calculates per-trade and portfolio-wide risk.
    -   **Trading Limits (`trading_limits.py`)**: Enforces daily trade limits with state persistence.
    -   **Commands (`commands.py`)**: Handles manual trading commands with integrated logging and testing utilities.
-   **SHORT Selling (DISABLED)**: Infrastructure for bidirectional trading (LONG + SHORT) via Kraken margin API is implemented but disabled pending margin account activation. Includes signal generation, execution, inverted SL/TP logic, and fee awareness for rollover costs.

### Feature Specifications
-   **Conversational AI**: User interaction for performance, insights, and commands.
-   **Autonomous Trading**: Executes trades based on learned patterns with automated position sizing, fee-aware execution, and rate-limited market orders.
-   **Continuous Learning**: Analyzes trade outcomes and market patterns.
-   **Risk Management**: Configurable risk parameters, daily loss kill-switch, ATR-based position sizing, and fee-adjusted edge validation.
-   **Safety Features**: Validation mode, pre-trade checks, auto-adjustment of position sizes, emergency flatten procedures, and comprehensive telemetry.
-   **Discord Notifications**: Alerts via Discord webhook for trade executions, position exits, errors, and daily/weekly performance summaries. Configured via DISCORD_WEBHOOK_URL secret. **Enhanced (Dec 2025)**: Trade notifications now include mental stop-loss price, take-profit price, and risk:reward ratio when trades execute.

## External Dependencies
-   **Kraken API**: For real-time market data, order placement, and account management.
-   **ccxt**: Python library for interacting with cryptocurrency exchanges.
-   **loguru**: For enhanced logging.
-   **tenacity**: For robust retry logic in API calls.
-   **python-dotenv**: For managing environment variables.
-   **OpenAI API**: For the LLM agent (GPT-4o integration).