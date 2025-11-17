# Kraken Trading Bot - Self-Learning AI Edition

## Overview
This project is an intelligent, self-learning cryptocurrency trading bot designed for the Kraken exchange. Its core purpose is to provide autonomous trading capabilities, continuously improve through self-learning, and offer a conversational AI interface for user interaction and insights. The bot features **pure market-only execution** (no stop-loss or take-profit orders), fee-aware trading, robust risk management, rate limiting, and a daily loss kill-switch, aiming to monitor markets 24/7 and manage the entire trading workload. The ambition is to automate trading completely, allowing users to focus on higher-level strategy rather than daily execution.

**Recent Major Update (Nov 2025)**: Complete architectural pivot from bracket-based to market-only execution due to Kraken API limitations. System now uses pure market buy/sell orders with fee-awareness, rate limiting, and SL-independent position sizing.

**Critical Bug Fixes (Nov 17, 2025)**:
- Fixed unbound 'config' variable in autopilot buy execution path (blocking all trades)
- Fixed MAX_DAILY_LOSS_USD parsing to handle "$50" format with safe fallbacks
- Wired telemetry_db.log_trade() for complete trade lifecycle logging (entry + exit)
- Added defensive fee model wrappers that never crash on import errors
- System validated and ready for autonomous trading

## User Preferences
- User prefers to be called: jimmy
- Zyn's role: Financial servant who does ALL the work FOR jimmy
- Zyn handles the entire trading workload autonomously so jimmy doesn't have to

## System Architecture

### UI/UX Decisions
The bot provides a chat interface on port 5000 for real-time interaction and a dashboard for displaying accurate trading status, open positions, balances, and P&L directly from Kraken data.

### Technical Implementations
The system is designed with a strong emphasis on mode isolation (LIVE vs. PAPER), ensuring no cross-contamination of data. Key architectural components include:

-   **Market-Only Execution System**: Pure market buy/sell orders with no stop-loss or take-profit orders due to Kraken API limitations. System features:
    -   **Execution Manager (`execution_manager.py`)**: Centralized market order execution with rate limiting, fee logging, and telemetry integration.
    -   **Fee Model (`fee_model.py`)**: Real-time Kraken fee tracking via TradeVolume API with 1-hour caching. Provides fee-adjusted profitability checks before trade execution.
    -   **Rate Limiter (`rate_limiter.py`)**: Rolling 60-second window with configurable limits (default 15 orders/min, 250ms min delay) to prevent API violations.
    -   **Market Position Sizing (`risk_manager.calculate_market_position_size`)**: SL-independent position sizing using fixed-fraction (0.5% equity) or synthetic ATR-based methods with 10% max position cap.
    -   **Fee-Adjusted Edge Check**: Pre-execution validation requiring edge_pct > round-trip fees + safety margin to prevent unprofitable trades.
-   **Legacy Bracket System (Deprecated)**: Original bracket order system preserved for backwards compatibility but disabled by default (USE_BRACKETS=False). Bracket orders could not be reliably placed due to Kraken API settlement delays and limitations.
-   **Account State (`account_state.py`)**: Provides canonical, mode-aware account data, ensuring complete isolation between LIVE and PAPER trading. Paper trading state is persisted via `paper_ledger.json`.
-   **Status Service (`status_service.py`)**: Centralized single source of truth for all trading data, rigorously enforcing mode isolation.
-   **Self-Learning Components**:
    -   **Telemetry Database (`telemetry_db.py`)**: SQLite database (`trading_memory.db`) for persistent storage of all trades, decisions, performance, insights, errors, and conversations to facilitate continuous learning. Includes comprehensive timestamp-based filtering for statistics.
    -   **Trade Analyzer (`trade_analyzer.py`)**: Intelligence engine for performance metrics and strategy identification.
    -   **Time Context (`time_context.py`)**: Provides temporal awareness for pattern recognition.
    -   **LLM Agent (`llm_agent.py`)**: Integrates OpenAI GPT-4o for natural language command execution, including robust anti-hallucination safeguards and real-time market data fetching.
    -   **Trade Result Validator (`trade_result_validator.py`)**: Comprehensive multi-layered anti-hallucination system preventing the LLM from claiming non-existent trade executions, validating against Kraken errors and success patterns.
    -   **Evaluation Log (`evaluation_log.py`)**: SQLite database (`evaluation_log.db`) for forensic-level transparency into trading decisions and executions, capturing executed orders with strict Kraken validation.
-   **Trading Components**:
    -   **Autopilot (`autopilot.py`)**: Autonomous trading loop executing a 5-minute closed-candle strategy, integrating mandatory risk gatekeepers.
    -   **Trading Config (`trading_config.py`)**: Centralized configuration system for indicator settings, market filters, risk parameters, and execution mode (market-only vs bracket). Supports env var overrides.
    -   **Signal Engine (`signal_engine.py`)**: Multi-signal decision engine orchestrating technical filters (RSI, SMA, volume, volatility, chop, ATR) for trade signals.
    -   **Strategy Orchestrator (`strategy_orchestrator.py`)**: Regime-aware selector routing trades to specific strategies based on market conditions.
    -   **Paper Trading (`paper_trading.py`)**: Complete simulation system with realistic fills, slippage, fees, bracket order management, position tracking, and P&L calculation.
    -   **Paper Exchange Wrapper (`paper_exchange_wrapper.py`)**: Infrastructure layer intercepting ccxt calls, routing to `PaperTradingSimulator` or real Kraken API based on mode.
    -   **Exchange Manager (`exchange_manager.py`)**: Singleton wrapper for ccxt instances, ensuring consistent data fetching and mode awareness.
    -   **Risk Manager (`risk_manager.py`)**: Functions for calculating per-trade risk (ATR-based stop-loss) and aggregating portfolio-wide active risk.
    -   **Trading Limits (`trading_limits.py`)**: Enforces daily trade limits with JSON state persistence and daily resets. Configurable via MAX_TRADES_PER_DAY and MAX_TRADES_PER_SYMBOL_PER_DAY environment variables.
    -   **Commands (`commands.py`)**: Handles manual trading commands for order placement and management, with integrated execution logging.

### Feature Specifications
-   **Conversational AI**: User interaction for performance inquiries, market insights, and command execution.
-   **Autonomous Trading**: Executes trades based on learned patterns and strategies with automated position sizing, fee-aware execution, and rate-limited market orders.
-   **Continuous Learning**: Analyzes trade outcomes and market patterns to improve decision-making.
-   **Risk Management**: Configurable risk parameters, daily loss kill-switch, ATR-based position sizing (no real stop-loss orders), and fee-adjusted edge validation.
-   **Safety Features**: Validation mode, pre-trade checks, auto-adjustment of position sizes, emergency flatten procedures, and comprehensive telemetry.
-   **SMS Notifications**: Alerts for trade executions and performance reports.

## External Dependencies
-   **Kraken API**: For real-time market data, order placement, and account management.
-   **ccxt**: Python library for interacting with cryptocurrency exchanges.
-   **loguru**: For enhanced logging.
-   **tenacity**: For robust retry logic in API calls.
-   **python-dotenv**: For managing environment variables.
-   **OpenAI API**: For the LLM agent (GPT-4o integration).