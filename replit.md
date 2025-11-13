# Kraken Trading Bot - Self-Learning AI Edition

## Overview
An intelligent, self-learning cryptocurrency trading bot for the Kraken exchange. This project aims to provide autonomous trading capabilities, continuous improvement through self-learning, and a conversational AI interface for user interaction and insights. The bot offers risk management, bracket orders, and a daily loss kill-switch, with the ambition to monitor markets 24/7 and handle the entire trading workload.

## User Preferences
- User prefers to be called: jimmy
- Zyn's role: Financial servant who does ALL the work FOR jimmy
- Zyn handles the entire trading workload autonomously so jimmy doesn't have to

## System Architecture

### UI/UX Decisions
- Chat interface on port 5000 for real-time interaction.
- Dashboard for displaying accurate trading status, open positions, balances, and P&L, driven directly by Kraken data.

### Technical Implementations
- **Status Service (`status_service.py`)**: A centralized module serving as the single source of truth for all trading data (modes, balances, orders, trades, activity summaries). **CRITICAL FIXES (Nov 12-13, 2025)**: (1) Now fetches trade history DIRECTLY from Kraken API instead of telemetry database, with 60-second caching. (2) Auto-syncs with Kraken every 60 seconds for balances/orders - optimized for fresh data while staying 99% under Kraken's API limits. Dashboard shows 100% accurate trade counts and P&L from real Kraken data.
- **Self-Learning Components**:
    - **Telemetry Database (`telemetry_db.py`)**: An SQLite database (`trading_memory.db`) storing all trades, decisions, performance, insights, errors, and conversations for persistent learning.
    - **Trade Analyzer (`trade_analyzer.py`)**: An intelligence engine that calculates win rates, profit factors, and identifies successful strategies and market patterns.
    - **Time Context (`time_context.py`)**: Provides temporal intelligence, including current date/time awareness, market hours, and time-based feature extraction for pattern recognition.
    - **LLM Agent (`llm_agent.py`)**: Integrates OpenAI GPT-4o with function calling for command execution. Upgraded to: (1) learn from trading history, (2) provide time-aware responses, (3) explain decisions, (4) support natural language conversation with memory, (5) **execute trading commands** (buy, sell, cancel, brackets) via natural language requests, (6) **fetch real-time market prices** directly from Kraken API, (7) **get market info** (min order sizes, trading limits) for any symbol, and (8) **maintain conversation context** across multi-turn dialogues with session-based history (20-turn memory).
- **Trading Components**:
    - **Autopilot (`autopilot.py`)**: The autonomous trading loop that logs all decisions, executed trades, and performance. **CRITICAL UPDATE (Nov 13, 2025)**: Refactored to use **5-minute closed-candle strategy** for Kraken API compliance. Now fetches OHLC candles once per 5-minute interval, evaluates SMA20 crossover signals ONLY when a new candle closes (no mid-candle evaluations), and maintains per-symbol candle tracking in state.json. Implements ATR-based risk management, automatic bracket orders, and a daily loss kill-switch. Loop runs every 300 seconds (5 minutes), staying 99% under Kraken's API rate limits.
    - **Trading Config (`trading_config.py`)**: **NEW (Nov 13, 2025)**: Centralized configuration system with dataclass architecture. Provides: (1) IndicatorConfig for SMA/RSI/ATR settings, (2) MarketFilters for volatility/volume/chop thresholds, (3) RiskConfig for position sizing and limits, (4) Environment variable overrides for all parameters. Clean separation of strategy, filters, and risk management.
    - **Signal Engine (`signal_engine.py`)**: **NEW (Nov 13, 2025)**: Professional multi-signal decision engine with sequential hard filters. Orchestrates RSI, SMA trend, volume, volatility, chop detection, and ATR spike filters. Returns detailed SignalResult with action ('long'/'short'/'hold'), reason, passed filters, and failed filter diagnostics. All filters must pass for trade signal generation.
    - **Candle Strategy (`candle_strategy.py`)**: Pure indicator calculation module for closed-candle analysis. **ENHANCED (Nov 13, 2025)**: Now includes: (1) `calculate_rsi()` for momentum filtering, (2) `is_volume_acceptable()` for percentile-based volume filtering, (3) `is_choppy_market()` for sideways detection via SMA slope and price range, (4) `is_volatility_acceptable()` for ATR/price ratio checks, (5) `detect_atr_spike()` for market shock detection, (6) `check_trend_strength()` for SMA20/SMA50 alignment. All functions are deterministic and work only with historical closed candles.
    - **Paper Trading (`paper_trading.py`)**: **NEW (Nov 13, 2025)**: Complete simulation system with realistic fills, slippage (5 bps bid-ask spread), trading fees (0.16% maker, 0.26% taker), bracket order management, position tracking, P&L calculation, and performance statistics. State persisted to JSON for continuity across restarts.
    - **Exchange Manager (`exchange_manager.py`)**: Singleton exchange wrapper with `fetch_ohlc()` method for retrieving 5-minute candle data from Kraken. Validates timeframe/limit parameters and enforces paper/live mode consistency across all modules.
    - **Commands (`commands.py`)**: Handles manual trading commands for order placement and management.
    - **Run (`run.py`)**: Provides an interactive shell for direct command execution.
- **Advanced Modules (Feature-flagged)**:
    - `crypto_universe.py`: Support for 200+ Kraken pairs with liquidity filtering.
    - `profit_target.py`: Daily profit targeting and session management.
    - `multi_timeframe.py`: Multi-timeframe confirmation (1h/4h/1d).
    - `api_watchdog.py`: Self-healing with circuit breaker and auto-restart.
    - `backtest_mode.py`: Historical backtesting without live orders.

### Feature Specifications
- **Conversational AI**: Users can chat with the bot to inquire about performance, trading decisions, market insights, and manage preferences. Supports power commands for status, learning, memory, and command execution.
- **Autonomous Trading**: The bot executes trades based on learned patterns and predefined strategies (e.g., SMA20), with automated position sizing, bracket orders (take-profit + stop-loss), and risk management.
- **Continuous Learning**: The bot analyzes trade outcomes, market patterns (e.g., time of day, day of week correlations), and strategy performance to improve its decision-making over time.
- **Risk Management**: Features include configurable risk per trade, maximum position size, daily loss kill-switch, ATR-based stop-loss and take-profit levels, and cool-off periods.
- **Safety Features**: Includes validation mode, pre-trade minimum volume checks, auto-adjustment of position sizes, emergency flatten procedures with retry logic, and comprehensive telemetry logging for safety events.
- **SMS Notifications**: Integration for trade execution alerts, daily P&L summaries, and weekly performance reports.

## External Dependencies
- **Kraken API**: For real-time market data, order placement, and account management.
- **ccxt**: Python library for interacting with cryptocurrency exchanges.
- **loguru**: For enhanced logging capabilities.
- **tenacity**: For robust retry logic in API calls.
- **python-dotenv**: For managing environment variables and secrets.
- **OpenAI API**: For the LLM agent (GPT-4o integration).