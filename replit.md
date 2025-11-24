# Kraken Trading Bot - Self-Learning AI Edition

## Overview
This project is an intelligent, self-learning cryptocurrency trading bot designed for the Kraken exchange. Its core purpose is to provide autonomous, fee-aware, market-only trading capabilities, continuously improve through self-learning, and offer a conversational AI interface. The bot features robust risk management, rate limiting, and a daily loss kill-switch, aiming to monitor markets 24/7 and manage the entire trading workload. The ambition is to automate trading completely, allowing users to focus on higher-level strategy. The system has recently pivoted to pure market-only execution due to Kraken API limitations, implementing a professional "mental stop-loss/take-profit" system.

## User Preferences
- User prefers to be called: jimmy
- Zyn's role: Financial servant who does ALL the work FOR jimmy
- Zyn handles the entire trading workload autonomously so jimmy doesn't have to

## System Architecture

### UI/UX Decisions
The bot provides a chat interface on port 5000 for real-time interaction and a dashboard for displaying accurate trading status, open positions, balances, and P&L directly from Kraken data.

### Technical Implementations
The system emphasizes mode isolation (LIVE vs. PAPER). Key architectural components include:

-   **Market-Only Execution System**: Pure market buy/sell orders.
    -   **Execution Manager (`execution_manager.py`)**: Centralized market order execution with rate limiting, fee logging, and telemetry integration. Handles settlement polling with exponential backoff for accurate fill data.
    -   **Position Tracker (`position_tracker.py`)**: Implements "mental stop-loss/take-profit" using ATR-based levels (2x ATR for SL, 3x for TP). Monitors positions and triggers market SELL. Uses `portalocker` for interprocess synchronization.
    -   **Fee Model (`fee_model.py`)**: Tracks real-time Kraken fees with caching, enabling fee-adjusted profitability checks.
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
-   **Trading Components**:
    -   **Autopilot (`autopilot.py`)**: Autonomous trading loop executing a 5-minute closed-candle strategy, monitoring mental SL/TP levels and integrating risk gatekeepers.
    -   **Trading Config (`trading_config.py`)**: Centralized configuration for indicators, market filters, risk parameters, and execution mode, supporting environment variable overrides.
    -   **Signal Engine (`signal_engine.py`)**: Multi-signal decision engine using technical filters (RSI, SMA, volume, volatility, chop, ATR).
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
-   **SMS Notifications**: Alerts for trade executions and performance reports.

## External Dependencies
-   **Kraken API**: For real-time market data, order placement, and account management.
-   **ccxt**: Python library for interacting with cryptocurrency exchanges.
-   **loguru**: For enhanced logging.
-   **tenacity**: For robust retry logic in API calls.
-   **python-dotenv**: For managing environment variables.
-   **OpenAI API**: For the LLM agent (GPT-4o integration).