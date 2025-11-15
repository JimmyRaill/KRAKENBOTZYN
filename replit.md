# Kraken Trading Bot - Self-Learning AI Edition

## Overview
This project is an intelligent, self-learning cryptocurrency trading bot designed for the Kraken exchange. Its core purpose is to provide autonomous trading capabilities, continuously improve through self-learning, and offer a conversational AI interface for user interaction and insights. The bot includes robust risk management features, bracket orders, and a daily loss kill-switch, aiming to monitor markets 24/7 and manage the entire trading workload. The ambition is to automate trading completely, allowing users to focus on higher-level strategy rather than daily execution.

## User Preferences
- User prefers to be called: jimmy
- Zyn's role: Financial servant who does ALL the work FOR jimmy
- Zyn handles the entire trading workload autonomously so jimmy doesn't have to

## System Architecture

### UI/UX Decisions
The bot provides a chat interface on port 5000 for real-time interaction and a dashboard for displaying accurate trading status, open positions, balances, and P&L directly from Kraken data.

### Technical Implementations
The system is designed with a strong emphasis on mode isolation (LIVE vs. PAPER), ensuring no cross-contamination of data.

- **Account State (`account_state.py`)**: Provides canonical, mode-aware account data, including balances, trade history, and portfolio snapshots, ensuring complete isolation between LIVE and PAPER trading. Paper trading state is persisted via `paper_ledger.json`. **CRITICAL ARCHITECTURE (Nov 13, 2025)**: The `PaperLedger` singleton is the single source of truth for ALL paper orders - both execution and query paths use this unified ledger to prevent data disconnection.
- **Status Service (`status_service.py`)**: Acts as a centralized single source of truth for all trading data, rigorously enforcing mode isolation by routing data requests through `account_state.py` and skipping Kraken API calls in PAPER mode where appropriate.
- **Self-Learning Components**:
    - **Telemetry Database (`telemetry_db.py`)**: An SQLite database (`trading_memory.db`) for persistent storage of all trades, decisions, performance, insights, errors, and conversations to facilitate continuous learning.
    - **Trade Analyzer (`trade_analyzer.py`)**: An intelligence engine that calculates performance metrics and identifies successful strategies.
    - **Time Context (`time_context.py`)**: Provides temporal awareness for pattern recognition.
    - **LLM Agent (`llm_agent.py`)**: Integrates OpenAI GPT-4o with function calling for natural language command execution. Includes enhanced parsing for percentage-based bracket orders with symbol-specific precision and robust anti-hallucination safeguards to prevent reporting non-existent trades. Supports natural language conversation with memory, decision explanations, and real-time market data fetching.
    - **Evaluation Log (`evaluation_log.py`)**: An SQLite database (`evaluation_log.db`) providing detailed transparency into every trading evaluation, capturing indicator values, market regimes, decision reasons, and position snapshots. Offers LLM-callable tools for debugging and explaining trading decisions.
- **Trading Components**:
    - **Autopilot (`autopilot.py`)**: The autonomous trading loop executing a 5-minute closed-candle strategy, evaluating signals only upon candle closure. It integrates mandatory risk gatekeepers for daily trade limits, per-trade risk validation (with ATR-based SL), and portfolio-wide risk checks.
    - **Trading Config (`trading_config.py`)**: A centralized configuration system using dataclasses for indicator settings, market filters, risk parameters, and regime classification. Tuned for aggressive trading by lowering various thresholds.
    - **Signal Engine (`signal_engine.py`)**: A multi-signal decision engine that orchestrates various technical filters (RSI, SMA trend, volume, volatility, chop, ATR spike) to generate trade signals.
    - **Strategy Orchestrator (`strategy_orchestrator.py`)**: A regime-aware selector that routes trades to specific strategies based on market conditions, with an enhanced aggressive range trading strategy.
    - **Candle Strategy (`candle_strategy.py`)**: A module for pure indicator calculation using closed-candle data, including RSI, volume, chop, volatility, ATR spike detection, and trend strength analysis.
    - **Paper Trading (`paper_trading.py`)**: A complete simulation system offering realistic fills, slippage, fees, bracket order management, position tracking, and P&L calculation, with state persistence.
    - **Paper Exchange Wrapper (`paper_exchange_wrapper.py`)**: An infrastructure layer that intercepts ccxt calls, routing them to the `PaperTradingSimulator` in PAPER mode and passing them to the real Kraken API in LIVE mode. It manages paper orders and positions with persistence. **CRITICAL FIX (Nov 15, 2025)**: All LIVE mode pass-through methods now guard against passing `params=None` to ccxt, which causes "'NoneType' object is not iterable" crashes. Fixed methods: fetch_balance, fetch_open_orders, create_market_sell_order, create_limit_buy_order, create_limit_sell_order, create_order, and cancel_order.
    - **Exchange Manager (`exchange_manager.py`)**: A singleton wrapper for ccxt instances, ensuring consistent data fetching and mode awareness.
    - **Risk Manager (`risk_manager.py`)**: Provides functions for calculating per-trade risk (ATR-based stop-loss) and aggregating portfolio-wide active risk.
    - **Trading Limits (`trading_limits.py`)**: Enforces daily trade limits (e.g., max 10 trades/symbol, 30 total/day) with JSON state persistence and daily resets.
    - **Commands (`commands.py`)**: Handles manual trading commands for order placement and management.
    - **Run (`run.py`)**: Provides an interactive shell for direct command execution.
- **Advanced Modules (Feature-flagged)**: Includes modules for crypto universe management, profit targeting, multi-timeframe analysis, API watchdog for self-healing, and historical backtesting.

### Feature Specifications
- **Conversational AI**: Enables user interaction for performance inquiries, market insights, and command execution.
- **Autonomous Trading**: Executes trades based on learned patterns and strategies, with automated position sizing and bracket orders.
- **Continuous Learning**: Analyzes trade outcomes and market patterns to improve decision-making.
- **Risk Management**: Configurable risk parameters, daily loss kill-switch, and ATR-based levels.
- **Safety Features**: Validation mode, pre-trade checks, auto-adjustment of position sizes, emergency flatten procedures, and comprehensive telemetry.
- **SMS Notifications**: Alerts for trade executions and performance reports.

## External Dependencies
- **Kraken API**: For real-time market data, order placement, and account management.
- **ccxt**: Python library for interacting with cryptocurrency exchanges.
- **loguru**: For enhanced logging.
- **tenacity**: For robust retry logic in API calls.
- **python-dotenv**: For managing environment variables.
- **OpenAI API**: For the LLM agent (GPT-4o integration).