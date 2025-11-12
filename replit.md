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
- **Status Service (`status_service.py`)**: A centralized module serving as the single source of truth for all trading data (modes, balances, orders, trades, activity summaries). **CRITICAL FIX (Nov 12, 2025)**: Now fetches trade history DIRECTLY from Kraken API instead of telemetry database, with 30-second caching to prevent API timeouts. This ensures dashboard shows 100% accurate trade counts and P&L from real Kraken data. Auto-syncs with Kraken every 60 seconds for balances/orders.
- **Self-Learning Components**:
    - **Telemetry Database (`telemetry_db.py`)**: An SQLite database (`trading_memory.db`) storing all trades, decisions, performance, insights, errors, and conversations for persistent learning.
    - **Trade Analyzer (`trade_analyzer.py`)**: An intelligence engine that calculates win rates, profit factors, and identifies successful strategies and market patterns.
    - **Time Context (`time_context.py`)**: Provides temporal intelligence, including current date/time awareness, market hours, and time-based feature extraction for pattern recognition.
    - **LLM Agent (`llm_agent.py`)**: Integrates OpenAI GPT-4o, upgraded to learn from trading history, provide time-aware responses, explain decisions, and support natural language conversation with memory.
- **Trading Components**:
    - **Autopilot (`autopilot.py`)**: The autonomous trading loop that logs all decisions, executed trades, and performance. It implements an SMA20 strategy with ATR-based risk management, automatic bracket orders, and a daily loss kill-switch.
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