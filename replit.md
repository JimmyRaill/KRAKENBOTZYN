# Kraken Trading Bot - Self-Learning AI Edition

## Overview
This project is an intelligent, self-learning cryptocurrency trading bot designed for the Kraken exchange. Its purpose is to provide autonomous, fee-aware, market-only trading capabilities, continuously improve through self-learning, and offer a conversational AI interface. The bot features robust risk management, rate limiting, and a daily loss kill-switch, aiming to monitor markets 24/7 and manage the entire trading workload. The ambition is to automate trading completely, allowing users to focus on higher-level strategy. The system implements a professional "mental stop-loss/take-profit" system.

## User Preferences
- User prefers to be called: jimmy
- Zin's role: Financial servant who does ALL the work FOR jimmy
- Zin handles the entire trading workload autonomously so jimmy doesn't to have to

## System Architecture

### UI/UX Decisions
The bot provides a chat interface on port 5000 for real-time interaction and a dashboard for displaying accurate trading status, open positions, balances, and P&L directly from Kraken data.

### Technical Implementations
The system emphasizes mode isolation (LIVE vs. PAPER). Key architectural components include:

-   **Execution Mode System**: Supports multiple execution strategies (`MARKET_ONLY`, `LIMIT_BRACKET`) with a centralized `Execution Manager` for order execution, rate limiting, fee logging, and telemetry. It includes advanced features like maker-friendly pricing, timeout/retry loops for limit orders, safe order cancellation, and optional market fallback.
-   **OCO Monitor**: Implements synthetic OCO for LIMIT_BRACKET mode, managing Take-Profit and Stop-Loss orders.
-   **Position Tracker**: Manages "mental stop-loss/take-profit" using ATR-based levels and `portalocker` for interprocess synchronization.
-   **Fee Model**: Tracks real-time Kraken fees and calculates `required_edge_pct` based on execution mode for profitability checks.
-   **Rate Limiter**: Enforces API call limits.
-   **Market Position Sizing**: Implements SL-independent position sizing with a 10% max position cap.
-   **Fee-Adjusted Edge Check**: Pre-trade validation for sufficient edge after fees and safety margins.
-   **Dust Prevention**: Prevents trading below Kraken's minimum order sizes.
-   **Account State**: Provides mode-aware account data, isolating LIVE and PAPER trading, with paper trading state persisted in `paper_ledger.json`.
-   **Self-Learning Components**:
    -   **Telemetry Database**: SQLite for persistent storage of trade data, decisions, errors, and conversations.
    -   **LLM Agent**: Integrates OpenAI GPT-4o for natural language command execution with anti-hallucination safeguards.
    -   **Trade Result Validator**: Validates LLM claims against actual Kraken execution.
    -   **Evaluation Log**: Logs trading decisions and executed orders.
    -   **Data Vault**: Centralized JSONL-based logging system for long-term analysis and self-iteration, including trade, decision, daily summary, version, and anomaly logging.
    -   **Snapshot System**: Periodic state snapshots for self-analysis and strategy evolution, capturing metadata, account status, risk configuration, open positions, performance summary, and system health.
-   **Trading Components**:
    -   **Autopilot**: Autonomous trading loop executing a 5-minute closed-candle strategy, monitoring mental SL/TP and integrating risk gatekeepers.
    -   **Trading Config**: Centralized configuration for indicators, market filters, risk parameters, and execution mode, supporting environment variable overrides.
    -   **Signal Engine**: Multi-signal decision engine using technical filters (RSI, SMA, volume, volatility, chop, ATR).
    -   **Strategy Orchestrator**: Regime-aware strategy selection with improved pullback detection and a pre-trade filtering pipeline including symbol, regime, fee gate, and confidence gate filters. A confidence-based decision engine intelligently gates trades based on signal confidence, allowing regime overrides and applying penalties/boosts.
    -   **Paper Trading**: Complete simulation system with realistic fills, slippage, fees, and P&L calculation.
    -   **Exchange Manager**: Singleton wrapper for `ccxt` instances, ensuring consistent data fetching and handling different execution environments (dev workspace, reserved VM) with safety checks.
    -   **Risk Manager**: Calculates per-trade and portfolio-wide risk.
    -   **Trading Limits**: Enforces daily trade limits with state persistence.
    -   **Commands**: Handles manual trading commands with logging and testing utilities.

### Feature Specifications
-   **Conversational AI**: User interaction for performance, insights, and commands.
-   **Autonomous Trading**: Executes trades based on learned patterns with automated position sizing, fee-aware execution, and rate-limited market orders.
-   **Continuous Learning**: Analyzes trade outcomes and market patterns.
-   **Risk Management**: Configurable risk parameters, daily loss kill-switch, ATR-based position sizing, and fee-adjusted edge validation.
-   **Safety Features**: Validation mode, pre-trade checks, auto-adjustment of position sizes, emergency flatten procedures, and comprehensive telemetry.
-   **Discord Notifications**: Alerts via Discord webhook for trade executions, position exits, errors, and daily/weekly performance summaries, with enhanced details for entry and exit events.

## External Dependencies
-   **Kraken API**: For real-time market data, order placement, and account management.
-   **ccxt**: Python library for interacting with cryptocurrency exchanges.
-   **loguru**: For enhanced logging.
-   **tenacity**: For robust retry logic in API calls.
-   **python-dotenv**: For managing environment variables.
-   **OpenAI API**: For the LLM agent (GPT-4o integration).