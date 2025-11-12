# Kraken Trading Bot - Self-Learning AI Edition

## Overview
An **intelligent, self-learning** cryptocurrency trading bot for the Kraken exchange with conversational AI, autonomous trading, and continuous improvement capabilities.

**Current State**: Active self-learning trading AI with:
- 🧠 **Self-learning system** - Learns from every trade, improves over time
- 💬 **Smart conversational AI** - Chat with your bot, ask questions, get insights
- 🤖 **Autonomous trading** via autopilot with paper/live modes
- 📅 **Time and calendar awareness** - Understands dates, times, market patterns
- 📊 **Performance tracking** - Win rate, profit factor, pattern discovery
- 🎯 Manual trading via interactive shell
- ⚡ Multiple trading symbols (BTC/USD, ETH/USD, ZEC/USD)
- 🛡️ Risk management with ATR-based position sizing
- 🎪 Bracket orders (take-profit + stop-loss)
- 🛑 Daily loss kill-switch for safety

## Recent Changes
- **2025-11-12**: 🤖 **ZYN'S PERSONALITY UPDATE** - Zyn now fully understands his role as your financial servant!
  - Updated system prompt to emphasize Zyn's purpose: to do ALL the work FOR you
  - Zyn takes pride in being your tireless trading assistant who handles everything
  - He knows he exists to lift the burden of trading from your shoulders
  - His communication now reflects his role as your dedicated financial servant
  - He's proud to monitor markets 24/7 so you never have to worry
- **2025-11-12**: 📱 **SMS NOTIFICATIONS INTEGRATED** - Get text alerts for all trading activity!
  - Trade execution alerts sent instantly to your phone
  - Daily P&L summaries at your chosen time
  - Weekly performance reports on Sundays
  - Configured for +1 (272) 892-4652
  - Setup page at /sms-setup for easy configuration
- **2025-11-12**: 🔧 **MAJOR INTEGRATION & HARDENING** - Advanced modules integrated with safety improvements!
  - **✅ Integrated 5 Advanced Modules** with feature flags (DISABLED by default for safe rollout):
    - `crypto_universe.py` - Full Kraken universe support (200+ pairs) with liquidity filtering
    - `profit_target.py` - Daily profit targeting and session management
    - `multi_timeframe.py` - Multi-timeframe confirmation (1h/4h/1d)
    - `api_watchdog.py` - Self-healing with circuit breaker and auto-restart
    - `backtest_mode.py` - Historical backtesting without live orders
  - **✅ ZERO LSP Errors** - Fixed all 8 LSP errors across codebase
  - **✅ Critical Bug Fixes**:
    - Fixed `statistics.mean()` crashes on empty lists (risk_manager.py, strategies.py)
    - Fixed division by zero in recovery system (profit_target.py)
    - Fixed type safety issues across all new modules
    - Added comprehensive error guards throughout
  - **✅ Production-Grade Bracket Protection**:
    - Pre-trade minimum volume checks (prevents rejected brackets)
    - Auto-adjustment of position size to meet Kraken minimums
    - Emergency flatten with 3-retry polling verification (2.1s window)
    - Global pause (6h) + loop exit on critical safety failures
    - Comprehensive telemetry logging of all safety events
  - **⚠️ Known Limitations**:
    - Emergency flatten uses balance polling (not order status tracking)
    - 2.1-second verification window may not catch all async fills
    - Partial fill scenarios need extended monitoring
    - Advanced edge cases require order-ID reconciliation (future enhancement)
  - **📊 Feature Flags** (in .env):
    - `ENABLE_CRYPTO_UNIVERSE=0` - Universe mode (default: OFF)
    - `ENABLE_PROFIT_TARGET=0` - Profit targeting (default: OFF)
    - `ENABLE_MULTI_TIMEFRAME=0` - MTF confirmation (default: OFF)
    - `ENABLE_API_WATCHDOG=0` - Self-healing (default: OFF)
    - `ENABLE_BACKTEST_MODE=0` - Backtesting (default: OFF)
- **2025-11-12**: 🧠 **Self-Learning AI** - Base intelligence system
  - Created trading telemetry database (SQLite) to remember all trades and decisions
  - Built TradeAnalyzer service for win/loss analysis and pattern recognition
  - Added TimeContext module for date/time awareness and market patterns
  - Upgraded LLM agent with learning insights and enhanced conversational skills
  - Integrated telemetry logging throughout autopilot (logs every decision)
  - Added conversation logging for context learning
  - Deployed chat interface on port 5000 for easy interaction
  - Created trading telemetry database (SQLite) to remember all trades and decisions
  - Built TradeAnalyzer service for win/loss analysis and pattern recognition
  - Added TimeContext module for date/time awareness and market patterns
  - Upgraded LLM agent with learning insights and enhanced conversational skills
  - Integrated telemetry logging throughout autopilot (logs every decision)
  - Added conversation logging for context learning
  - Deployed chat interface on port 5000 for easy interaction
- **2025-11-12**: Security fix - Removed hardcoded API keys from `config.py`
- **2025-11-12**: Updated `pyproject.toml` with proper dependencies

## Project Architecture

### 🧠 Self-Learning Components (NEW!)
1. **telemetry_db.py** - Trading memory database
   - SQLite database storing all trades, decisions, performance
   - Tables: trades, decisions, performance, insights, errors, conversations
   - Persistent learning across sessions

2. **trade_analyzer.py** - Intelligence engine
   - Calculates win rate, profit factor, best/worst trades
   - Analyzes what strategies work in different conditions
   - Discovers patterns (hold times, entry reasons, time-of-day correlations)
   - Provides learning summaries for the AI

3. **time_context.py** - Temporal intelligence
   - Current date/time awareness
   - Market hours detection
   - Time-based feature extraction (day of week, time of day, etc.)
   - Calendar awareness for pattern recognition

4. **llm_agent.py** - Conversational AI brain (UPGRADED!)
   - OpenAI GPT-4o integration
   - Learns from trading history and patterns
   - Time-aware responses
   - Natural language conversation with memory
   - Explains trading decisions clearly
   - Power commands: remember, forget, memory, run, status, learning

5. **api.py** - Chat web interface
   - FastAPI server on port 5000
   - Real-time chat with the AI
   - Logs conversations for learning
   - Reports equity and performance

### 🤖 Trading Components
6. **autopilot.py** - Autonomous trading loop (ENHANCED!)
   - Logs every decision (buy/sell/hold) with context
   - Logs executed trades with prices and reasons
   - Records performance snapshots
   - Tracks errors for learning
   - SMA20 strategy with ATR risk management
   - Automatic bracket orders
   - Kill-switch for safety

7. **commands.py** - Trading command router
   - Manual trading commands
   - Limit orders, stop orders, brackets
   - Order management

8. **run.py** - Interactive shell
   - REPL for manual commands

### 📁 Data Files
- `trading_memory.db` - **NEW!** Learning database with all historical data
- `state.json` - Real-time bot state
- `memory.json` - User preferences and notes
- `diagnostic.json` - Trade statistics
- `.env` - Configuration and secrets

## Configuration (.env)

### Trading Mode
- `AUTONOMOUS=1` - Enable autopilot (0 to disable)
- `KRAKEN_VALIDATE_ONLY=0` - Live trading (1 for paper/dry-run)
- `SYMBOLS=BTC/USD,ETH/USD,ZEC/USD` - Trading pairs
- `TRADE_INTERVAL_SEC=60` - Loop frequency

### Risk Management
- `RISK_PER_TRADE_PCT=0.25` - Risk per trade (% of equity)
- `MAX_POSITION_USD=15` - Maximum position size
- `MAX_DAILY_LOSS_USD=25` - Daily loss kill-switch
- `STOP_LOSS_ATR=0.6` - Stop-loss (ATR multiples)
- `TAKE_PROFIT_ATR=1.2` - Take-profit (ATR multiples)
- `COOL_OFF_MIN=30` - Cooldown after exit

### Strategy Parameters
- Uses SMA20 for trend detection
- Entry: price > SMA20 + edge threshold
- Exit: price < SMA20 - edge threshold
- Position sizing based on ATR volatility

## Dependencies
- **ccxt** - Cryptocurrency exchange API
- **loguru** - Logging
- **tenacity** - Retry logic for API calls
- **python-dotenv** - Environment variable management
- **openai** - LLM integration (optional)

## How to Use

### 💬 Talk to Your AI Bot (NEW!)
Access the chat interface at **http://[your-repl-url]** (port 5000)

The AI can:
- Answer questions about your trading performance
- Explain why it made certain trades
- Provide insights from its learning
- Remember your preferences
- Help you understand market conditions

Example questions:
- "How am I doing today?"
- "What patterns have you learned?"
- "Why did you buy ETH earlier?"
- "What's the current time and market status?"
- "Show me my recent trades"

Power commands:
- `status` or `learning` - Show performance and insights
- `remember: [fact]` - Store something in memory
- `memory` - Show what the bot remembers
- `run: bal` - Execute trading commands

### 🤖 Autonomous Trading
The autopilot runs automatically when `AUTONOMOUS=1` in `.env`. It now:
1. **Learns from every decision** - Records all buy/sell/hold choices
2. **Tracks performance** - Win rate, profit factor, best strategies
3. **Discovers patterns** - What works, when, and why
4. **Improves over time** - Uses insights to make better decisions
5. **Monitors multiple symbols** every 60 seconds
6. **Sizes positions** using ATR-based risk
7. **Places bracket orders** (TP/SL) automatically
8. **Protects capital** with daily loss kill-switch

Monitor in real-time:
- Chat interface for insights
- `state.json` for current status
- `trading_memory.db` for full history

### 📊 View Learning Data
```bash
python3 -c "from trade_analyzer import get_learning_summary; print(get_learning_summary())"
```

### Manual Trading Shell
```bash
python run.py
```

Available commands:
- `price zec/usd` - Current price
- `bal` - Show balances
- `buy 25 usd zec/usd` - Market buy
- `sell all zec/usd` - Market sell
- `limit buy zec/usd 2 @ 29.5` - Limit order
- `bracket zec/usd 2 tp 35 sl 28` - Bracket order
- `open` - Show open orders
- `cancel <order_id>` - Cancel order

## Safety Features
- Validation mode (`KRAKEN_VALIDATE_ONLY=1`) for testing
- Daily loss kill-switch
- Per-symbol cooldown after exits
- Retry logic for API calls
- Position size limits

## Self-Learning Features

### What the Bot Learns
1. **Trade Outcomes**
   - Win/loss for each trade
   - Profit/loss amounts and percentages
   - Hold times for winners vs losers
   - Entry/exit reasons that work best

2. **Market Patterns**
   - Best times of day to trade
   - Day of week correlations
   - Market conditions for different strategies
   - Which symbols perform best when

3. **Strategy Performance**
   - Which entry signals are most profitable
   - Optimal hold times
   - Best stop-loss and take-profit levels
   - Risk/reward ratios

4. **Conversation Context**
   - Your preferences and goals
   - Questions you ask frequently
   - Topics you care about
   - Your trading style

### Data Storage
All learning data is stored in `trading_memory.db`:
- **trades** - Every executed trade with context
- **decisions** - All buy/sell/hold choices (even not executed)
- **performance** - Equity snapshots over time
- **insights** - Discovered patterns and what works
- **errors** - Mistakes to learn from
- **conversations** - Chat history for context

### Intelligence Metrics
The bot tracks and improves based on:
- **Win Rate** - % of profitable trades
- **Profit Factor** - Total wins / total losses
- **Average Win/Loss** - Size of typical wins and losses
- **Max Drawdown** - Worst losing streak
- **Pattern Confidence** - How sure it is about insights

## User Preferences
- User prefers to be called: jimmy (stored in memory.json)
- Zyn's role: Financial servant who does ALL the work FOR jimmy
- Zyn handles the entire trading workload autonomously so jimmy doesn't have to
