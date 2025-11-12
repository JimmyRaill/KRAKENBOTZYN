# Kraken Trading Bot

## Overview
An autonomous cryptocurrency trading bot for the Kraken exchange with both manual command-line interface and automated trading capabilities.

**Current State**: Active trading bot with:
- Manual trading via interactive shell (`run.py`)
- Autonomous trading via autopilot (`autopilot.py`)
- Multiple trading symbols supported (BTC/USD, ETH/USD, ZEC/USD)
- Risk management with ATR-based position sizing
- Bracket orders (take-profit + stop-loss)
- Daily loss kill-switch

## Recent Changes
- **2025-11-12**: Security fix - Removed hardcoded API keys from `config.py`, now using environment variables from `.env`
- **2025-11-12**: Updated `pyproject.toml` with proper dependencies

## Project Architecture

### Core Components
1. **autopilot.py** - Autonomous trading loop
   - Simple moving average (SMA20) strategy
   - ATR-based risk management
   - Automatic bracket orders (TP/SL)
   - Kill-switch for daily losses
   - Writes state to `state.json` for monitoring

2. **commands.py** - Trading command router
   - Manual trading commands (price, bal, buy, sell)
   - Limit orders, stop orders, bracket orders
   - Order management (open, cancel)
   - Uses Kraken API via ccxt library

3. **run.py** - Interactive shell
   - REPL for manual trading commands
   - Type 'help' for available commands

4. **bot.py** - Original bot class (legacy)
   - KrakenBot wrapper for ccxt

5. **config.py** - Configuration loader
   - Loads settings from `.env` file

### Key Files
- `.env` - Environment variables (API keys, trading parameters)
- `state.json` - Real-time bot state (equity, positions, open orders)
- `diagnostic.json` - Trade statistics
- `krakenbot.log` - Trading logs
- `logs/autopilot.log` - Autopilot execution logs

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

### Manual Trading
Run the interactive shell:
```bash
python run.py
```

Available commands:
- `price zec/usd` - Get current price
- `bal` - Show balances
- `buy 25 usd zec/usd` - Market buy
- `sell all zec/usd` - Market sell all
- `limit buy zec/usd 2 @ 29.5` - Limit buy order
- `limit sell zec/usd 1.5 @ 34.2` - Limit sell order
- `bracket zec/usd 2 tp 35 sl 28` - Bracket order (TP+SL)
- `open` - Show open orders
- `cancel <order_id>` - Cancel order

### Autonomous Trading
The autopilot runs automatically when `AUTONOMOUS=1` in `.env`. It will:
1. Monitor specified symbols every 60 seconds
2. Calculate entry/exit signals based on SMA20
3. Size positions using ATR-based risk management
4. Place bracket orders (TP/SL) on entries
5. Flatten positions and pause if daily loss limit hit

Monitor bot state in `state.json` or logs in `logs/autopilot.log`.

## Safety Features
- Validation mode (`KRAKEN_VALIDATE_ONLY=1`) for testing
- Daily loss kill-switch
- Per-symbol cooldown after exits
- Retry logic for API calls
- Position size limits

## User Preferences
None specified yet.
