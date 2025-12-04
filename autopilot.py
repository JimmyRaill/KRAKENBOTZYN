# autopilot.py — autonomous loop + telemetry + diagnostics (clean full file)

import json
import math
import os
import sys
import time
from pathlib import Path
from statistics import mean
import statistics
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import ccxt

# NEW: Candle-based strategy imports for 5-minute closed-candle system
from candle_strategy import (
    calculate_sma, calculate_atr, calculate_rsi, calculate_adx, calculate_bollinger_bands,
    detect_sma_crossover, is_new_candle_closed, validate_candle_data,
    extract_closes, get_latest_candle_timestamp, calculate_volume_percentile
)
from exchange_manager import get_manager

# REGIME-AWARE SYSTEM: Strategy orchestrator coordinates regime detection + strategy selection
from strategy_orchestrator import get_orchestrator

# RISK MANAGEMENT: Portfolio-wide risk controls and daily trade limits
from risk_manager import calculate_trade_risk, get_max_active_risk, PositionSnapshot, calculate_market_position_size
from trading_limits import can_open_new_trade, record_trade_opened, get_daily_limits

# TRADING CONFIG: Centralized config with execution mode flags
from trading_config import get_config, get_zin_version, get_config_for_logging

# FEE MODEL: Real-time Kraken fee tracking for fee-aware trading
from fee_model import get_minimum_edge_pct, get_taker_fee

# MARKET EXECUTION: Simplified market-only order execution with mode routing
from execution_manager import execute_market_entry, execute_market_exit, has_open_position, execute_entry_with_mode

# POSITION TRACKING: Mental SL/TP for market-only mode
from position_tracker import add_position, check_all_positions_for_exits, get_position_summary, check_if_dust_position

# Bracket order manager - OPTIONAL (only used if USE_BRACKETS=True)
# Type hints for LSP
get_bracket_manager = None
BracketOrder = None
BracketConfig = None
BRACKET_MANAGER_ENABLED = False

try:
    from bracket_order_manager import get_bracket_manager, BracketOrder, BracketConfig
    BRACKET_MANAGER_ENABLED = True
    print("[INIT] ✅ Bracket Order Manager enabled (available but may be disabled via config)")
except ImportError as e:
    BRACKET_MANAGER_ENABLED = False
    print(f"[INIT] Bracket Order Manager not available: {e}")

# Self-learning imports
TELEMETRY_ENABLED = False
log_trade = log_decision = log_performance = log_error = None
notify_trade = check_summaries = notify_position_exit = None
try:
    from telemetry_db import log_trade, log_decision, log_performance, log_error
    from discord_notifications import notify_trade, check_summaries, notify_position_exit
    from time_context import get_time_info, get_prompt_context
    TELEMETRY_ENABLED = True
except ImportError:
    print("[WARNING] Telemetry modules not found - learning features disabled")

# EVALUATION LOGGING - Full transparency layer
from evaluation_log import log_evaluation

# DATA VAULT - Centralized structured logging for analysis and self-learning
DATA_VAULT_ENABLED = False
data_vault_log_version = data_vault_log_decision = data_vault_log_trade = data_vault_log_anomaly = None
try:
    from data_logger import log_version as data_vault_log_version
    from data_logger import log_decision as data_vault_log_decision
    from data_logger import log_trade as data_vault_log_trade
    from data_logger import log_anomaly_event as data_vault_log_anomaly
    DATA_VAULT_ENABLED = True
    print("[INIT] ✅ Data Vault logging enabled (trades, decisions, anomalies)")
except ImportError as e:
    print(f"[WARNING] Data Vault not available: {e}")

# RECONCILIATION SERVICE - TP/SL fill monitoring
from reconciliation_service import run_reconciliation_cycle
from loguru import logger as reconciliation_logger

# SNAPSHOT SYSTEM - Periodic state snapshots (~3 per day)
SNAPSHOT_ENABLED = False
maybe_take_snapshot = None
try:
    from snapshot_builder import maybe_take_snapshot
    SNAPSHOT_ENABLED = True
    print("[INIT] ✅ Snapshot system enabled (~3 snapshots/day in live mode)")
except ImportError as e:
    print(f"[WARNING] Snapshot system not available: {e}")

# HEARTBEAT SYSTEM - For Reserved VM health monitoring
HEARTBEAT_FILE = Path("data/heartbeat.json")

def _write_heartbeat(loop_count: int, symbols_count: int, interval_sec: int):
    """Write heartbeat file for health monitoring. Called after each trading loop."""
    from datetime import datetime, timezone
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        heartbeat_data = {
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "mode": get_mode_str(),
            "status": "running",
            "pid": os.getpid(),
            "loop_count": loop_count,
            "symbols_count": symbols_count,
            "interval_sec": interval_sec
        }
        
        # Atomic write using temp file
        temp_file = HEARTBEAT_FILE.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump(heartbeat_data, f, indent=2)
        temp_file.replace(HEARTBEAT_FILE)
        
    except Exception as e:
        print(f"[HEARTBEAT] Failed to write: {e}")

# Advanced feature imports with individual toggles
MULTI_STRATEGY_ENABLED = False
PATTERN_RECOGNITION_ENABLED = False
TRAILING_STOPS_ENABLED = False
LOSS_RECOVERY_ENABLED = False
NOTIFICATIONS_ENABLED = False

detect_market_regime = select_best_strategy = execute_strategy = None
get_multi_strategy_consensus = MarketRegime = StrategyType = None
create_trailing_stop = PortfolioMetrics = None
send_alert_sync = trade_executed_alert = daily_summary_alert = None
strategy_switch_alert = PatternDetector = None
LossRecoverySystem = ProfitReinvestmentSystem = None

# Try importing advanced modules with feature flags
try:
    from strategies import (
        detect_market_regime, select_best_strategy, execute_strategy,
        get_multi_strategy_consensus, MarketRegime, StrategyType
    )
    # Verify all callables loaded
    if all([detect_market_regime, select_best_strategy, execute_strategy,
            get_multi_strategy_consensus, MarketRegime, StrategyType]):
        MULTI_STRATEGY_ENABLED = os.getenv("ENABLE_MULTI_STRATEGY", "0") == "1"
        if MULTI_STRATEGY_ENABLED:
            print("[INIT] ✅ Multi-Strategy System enabled")
except ImportError as e:
    print(f"[WARNING] Multi-Strategy not available: {e}")

try:
    from pattern_recognition import PatternDetector
    if PatternDetector:
        PATTERN_RECOGNITION_ENABLED = os.getenv("ENABLE_PATTERN_RECOGNITION", "0") == "1"
        if PATTERN_RECOGNITION_ENABLED:
            print("[INIT] ✅ Pattern Recognition enabled")
except ImportError as e:
    print(f"[WARNING] Pattern Recognition not available: {e}")

try:
    from risk_manager import create_trailing_stop, PortfolioMetrics
    if create_trailing_stop and PortfolioMetrics:
        TRAILING_STOPS_ENABLED = os.getenv("ENABLE_TRAILING_STOPS", "0") == "1"
        if TRAILING_STOPS_ENABLED:
            print("[INIT] ✅ Trailing Stop-Loss enabled")
except ImportError as e:
    print(f"[WARNING] Trailing Stops not available: {e}")

try:
    from recovery_system import LossRecoverySystem, ProfitReinvestmentSystem
    if LossRecoverySystem and ProfitReinvestmentSystem:
        LOSS_RECOVERY_ENABLED = os.getenv("ENABLE_LOSS_RECOVERY", "0") == "1"
        if LOSS_RECOVERY_ENABLED:
            print("[INIT] ✅ Loss Recovery & Profit Reinvestment enabled")
except ImportError as e:
    print(f"[WARNING] Recovery System not available: {e}")

try:
    from notifications import (
        send_alert_sync, trade_executed_alert, daily_summary_alert,
        strategy_switch_alert
    )
    if all([send_alert_sync, trade_executed_alert, daily_summary_alert, strategy_switch_alert]):
        NOTIFICATIONS_ENABLED = os.getenv("ENABLE_NOTIFICATIONS", "0") == "1"
        if NOTIFICATIONS_ENABLED:
            print("[INIT] ✅ Notification System enabled")
except ImportError as e:
    print(f"[WARNING] Notifications not available: {e}")

# NEW ADVANCED FEATURES - Full bot upgrade
# Initialize feature flags (will be set based on imports and env vars)
CRYPTO_UNIVERSE_ENABLED = False
PROFIT_TARGET_ENABLED = False
MULTI_TIMEFRAME_ENABLED = False
API_WATCHDOG_ENABLED = False
BACKTEST_MODE_ENABLED = False

CryptoUniverseScanner = get_target_system = MultiTimeframeAnalyzer = None
get_watchdog = get_backtest = fetch_multi_timeframe_data = None

# Crypto Universe Scanner - Dynamic symbol selection
try:
    from crypto_universe import CryptoUniverseScanner, get_dynamic_universe
    if CryptoUniverseScanner:
        # Enable based on ENABLE_CRYPTO_UNIVERSE env var
        CRYPTO_UNIVERSE_ENABLED = os.getenv("ENABLE_CRYPTO_UNIVERSE", "0").lower() in ("1", "true", "yes", "on")
        if CRYPTO_UNIVERSE_ENABLED:
            print("[INIT] ✅ Crypto Universe Scanner enabled - will scan 200+ Kraken pairs for liquid markets")
except ImportError as e:
    print(f"[WARNING] Crypto Universe not available: {e}")

try:
    from profit_target import ProfitTargetSystem, get_target_system
    if ProfitTargetSystem:
        PROFIT_TARGET_ENABLED = os.getenv("ENABLE_PROFIT_TARGET", "0") == "1"
        if PROFIT_TARGET_ENABLED:
            print("[INIT] ✅ Daily Profit Target System enabled (0.035-0.038%)")
except ImportError as e:
    print(f"[WARNING] Profit Target not available: {e}")

try:
    from multi_timeframe import MultiTimeframeAnalyzer, fetch_multi_timeframe_data
    if MultiTimeframeAnalyzer:
        MULTI_TIMEFRAME_ENABLED = os.getenv("ENABLE_MULTI_TIMEFRAME", "0") == "1"
        if MULTI_TIMEFRAME_ENABLED:
            print("[INIT] ✅ Multi-Timeframe Confirmation enabled (1m/15m/1h)")
except ImportError as e:
    print(f"[WARNING] Multi-Timeframe not available: {e}")

try:
    from api_watchdog import APIWatchdog, get_watchdog
    if APIWatchdog:
        API_WATCHDOG_ENABLED = os.getenv("ENABLE_API_WATCHDOG", "0") == "1"
        if API_WATCHDOG_ENABLED:
            print("[INIT] ✅ API Watchdog enabled (self-healing)")
except ImportError as e:
    print(f"[WARNING] API Watchdog not available: {e}")

try:
    from backtest_mode import BacktestMode, get_backtest
    if BacktestMode:
        BACKTEST_MODE_ENABLED = os.getenv("BACKTEST_MODE", "0") == "1"
        if BACKTEST_MODE_ENABLED:
            print("[INIT] ✅ Backtest Mode enabled (no real orders)")
except ImportError as e:
    print(f"[WARNING] Backtest Mode not available: {e}")

# -------------------------------------------------------------------
# .env + constants
# -------------------------------------------------------------------
ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=str(ENV_PATH), override=True)

# Single source of truth for state path (chat + autopilot must match)
DEFAULT_STATE_PATH = Path(__file__).with_name("state.json")
STATE_PATH = Path(os.environ.get("STATE_PATH", str(DEFAULT_STATE_PATH)))

print(
    "[BOOT] autopilot starting",
    "pid=", os.getpid(),
    "cwd=", os.getcwd(),
    "AUTONOMOUS=", os.getenv("AUTONOMOUS"),
    "KRAKEN_VALIDATE_ONLY=", os.getenv("KRAKEN_VALIDATE_ONLY"),
    "SYMBOLS=", os.getenv("SYMBOLS"),
    flush=True,
)

# Import exchange manager early to show trading mode
from exchange_manager import get_exchange, get_mode_str, is_paper_mode
print(f"[BOOT] Trading mode: {get_mode_str().upper()} (paper={is_paper_mode()})", flush=True)

# Log execution mode at startup for debugging
_boot_config = get_config()
print(f"[EXECUTION] Mode: {_boot_config.execution_mode}", flush=True)

# -------------------------------------------------------------------
# env helpers (typed)
# -------------------------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    val = os.getenv(name)
    return default if val is None else str(val)

def env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    try:
        return int(val) if val is not None else default
    except Exception:
        return default

def env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    try:
        return float(val) if val is not None else default
    except Exception:
        return default

# -------------------------------------------------------------------
# exchange + alerts (CENTRALIZED VIA EXCHANGE MANAGER)
# -------------------------------------------------------------------
def mk_ex():
    """
    DEPRECATED: Use get_exchange() directly instead.
    Kept for backward compatibility.
    """
    return get_exchange()

def alert(msg: str) -> None:
    url = env_str("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        print("[ALERT]", msg)
        return
    import urllib.request
    try:
        data = json.dumps({"content": msg}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print("[ALERT_FAIL]", e)

# -------------------------------------------------------------------
# market helpers
# -------------------------------------------------------------------
def ohlcv_1m(ex, symbol: str, limit: int = 60):
    data = ex.fetch_ohlcv(symbol, timeframe="1m", limit=limit)
    closes = [row[4] for row in data if row and row[4] is not None]
    price: Optional[float] = closes[-1] if closes else None
    return data, closes, price

def compute_atr(ohlcv, period: int = 14) -> Optional[float]:
    if not ohlcv or len(ohlcv) < period + 1:
        return None
    tr_values: List[float] = []
    prev_close: Optional[float] = None
    for _, o, h, lo, c, _ in ohlcv[-(period + 1):]:
        if prev_close is None:
            tr = h - lo
        else:
            tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        tr_values.append(float(tr))
        prev_close = float(c)
    n = min(period, len(tr_values))
    return sum(tr_values[-n:]) / max(1, n)

def position_qty(ex, symbol: str):
    base = symbol.split("/")[0].replace("-", "")
    bal: Dict[str, Any] = ex.fetch_balance()
    qty = float((bal.get(base) or {}).get("total", 0.0) or 0.0)
    usd = float((bal.get("USD") or {}).get("total", 0.0) or 0.0)
    return qty, usd

def account_equity_usd(bal: Dict[str, Any]) -> float:
    """
    Calculate TOTAL account equity in USD (USD balance + value of all coin positions).
    
    CRITICAL: Must include coin positions to prevent kill-switch false triggers when USD→coins.
    """
    try:
        # CRITICAL FIX: Handle None or empty balance responses
        if bal is None:
            return 0.0
        if not isinstance(bal, dict):
            return 0.0
        
        # Start with USD balance
        usd_balance = float((bal.get("USD") or {}).get("total", 0.0) or 0.0)
        
        # Add value of all coin positions
        # bal structure: {'USD': {'total': 400.0, ...}, 'ASTER': {'total': 42.22, ...}, ...}
        total_equity = usd_balance
        
        from exchange_manager import get_exchange
        ex = get_exchange()
        
        for currency, balance_info in bal.items():
            if currency == "USD" or not isinstance(balance_info, dict):
                continue
            
            coin_qty = float((balance_info.get("total") or 0.0) or 0.0)
            if coin_qty <= 0.001:  # Skip dust
                continue
            
            try:
                # Fetch current USD price for this coin
                symbol = f"{currency}/USD"
                ticker = ex.fetch_ticker(symbol)
                price_usd = ticker.get('last') or ticker.get('close') or 0.0
                
                if price_usd > 0:
                    coin_value_usd = coin_qty * price_usd
                    total_equity += coin_value_usd
            except Exception:
                # Skip coins without USD pairs or fetch failures
                pass
        
        return total_equity
        
    except Exception:
        return 0.0

def risk_per_trade_usd(eq_usd: float) -> float:
    pct = env_float("RISK_PER_TRADE_PCT", 0.25) / 100.0
    cap = env_float("MAX_POSITION_USD", 10.0)
    return max(1.0, min(eq_usd * pct, cap))

def qty_from_atr(eq_usd: float, atr: Optional[float], price: Optional[float]) -> float:
    if not price or not atr:
        return 0.0
    stop_atr = env_float("STOP_LOSS_ATR", 0.6)
    risk_usd = risk_per_trade_usd(eq_usd)
    risk_per_coin = max(1e-9, stop_atr * atr)
    qty = risk_usd / risk_per_coin
    max_pos = env_float("MAX_POSITION_USD", 10.0)
    if qty * price > max_pos:
        qty = max_pos / price
    return max(0.0, qty)

# -------------------------------------------------------------------
# “pro metrics” (optional)
# -------------------------------------------------------------------
def _dd_curve(equity_series: List[float]) -> float:
    peak = -1e18
    dd = 0.0
    for x in equity_series:
        if x > peak:
            peak = x
        cur = (x / peak - 1.0) if peak > 0 else 0.0
        if cur < dd:
            dd = cur
    return dd  # negative

def pro_metrics(ex, sym: str) -> Dict[str, Optional[float]]:
    try:
        ohlcv = ex.fetch_ohlcv(sym, timeframe="1h", limit=720)  # ~30d
        closes = [c[4] for c in ohlcv if c and c[4]]
        if len(closes) < 50:
            return {"error": None}

        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        if len(rets) > 2:
            mu = statistics.mean(rets)
            sd = statistics.pstdev(rets)
            vol_annual = sd * math.sqrt(8760)  # 24*365
            sharpe = (mu * 8760) / (sd + 1e-12)
        else:
            vol_annual, sharpe = 0.0, 0.0

        ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
        ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
        trend = (ma50 / ma200 - 1.0) if (ma50 and ma200) else None

        eq: List[float] = []
        base = closes[0]
        for px in closes:
            eq.append(px / base)
        max_dd = _dd_curve(eq)

        return {
            "vol_annual_pct": round(vol_annual * 100, 2),
            "sharpe_1h_30d": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "trend_strength": round(trend, 4) if trend is not None else None,
            "price": float(closes[-1]),
        }
    except Exception:
        return {"error": None}

# -------------------------------------------------------------------
# state I/O (shared with chat)
# -------------------------------------------------------------------
def write_state(payload: Dict[str, Any]) -> None:
    try:
        payload = dict(payload or {})
        payload["ts"] = time.time()
        STATE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        print("[STATE-WRITE-ERR]", e)

def read_state() -> Dict[str, Any]:
    """
    Read current state from state.json, return empty dict if missing/corrupt.
    This enables safe state persistence across autopilot restarts.
    """
    try:
        if not STATE_PATH.exists():
            return {}
        content = STATE_PATH.read_text(encoding="utf-8")
        return json.loads(content) or {}
    except Exception as e:
        print(f"[STATE-READ-ERR] {e}, using empty state")
        return {}

def get_candle_tracking_for_symbol(state: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """
    Safely retrieve candle tracking data for a symbol.
    
    Returns:
        Dict with keys: last_closed_ts, last_sma20, last_close
        Returns empty dict for first run (no previous data)
    """
    candle_tracking = state.setdefault('candle_tracking', {})
    return candle_tracking.get(symbol, {})

def update_candle_tracking(state: Dict[str, Any], symbol: str, timestamp: int, sma20: float, close: float) -> None:
    """
    Update candle tracking data for a symbol in state dict.
    
    Args:
        state: State dict (modified in-place)
        symbol: Trading pair (e.g., 'BTC/USD')
        timestamp: Latest candle timestamp (ms)
        sma20: Calculated SMA20 value
        close: Latest close price
    """
    candle_tracking = state.setdefault('candle_tracking', {})
    candle_tracking[symbol] = {
        'last_closed_ts': timestamp,
        'last_sma20': sma20,
        'last_close': close
    }

# -------------------------------------------------------------------
# guardrails
# -------------------------------------------------------------------
_DAY_START_EQUITY: Optional[float] = None
_PAUSED_UNTIL: float = 0.0
_COOLDOWN_UNTIL: Dict[str, float] = {}

def paused() -> bool:
    return time.time() < _PAUSED_UNTIL

def cooldown_active(symbol: str) -> bool:
    return time.time() < _COOLDOWN_UNTIL.get(symbol, 0.0)

def set_cooldown(symbol: str) -> None:
    mins = env_int("COOL_OFF_MIN", 30)
    _COOLDOWN_UNTIL[symbol] = time.time() + mins * 60

def trigger_kill(ex) -> None:
    global _PAUSED_UNTIL
    alert("🛑 Kill-switch: daily loss limit hit. Flattening & pausing 6h.")
    from commands import handle as run_command  # local import
    for sym in [s.strip().upper() for s in env_str("SYMBOLS", "ZEC/USD").split(",") if s.strip()]:
        try:
            print(run_command(f"sell all {sym}"))
        except Exception as e:
            print("[KILL-ERR]", sym, e)
    try:
        print(run_command("open"))
    except Exception:
        pass
    _PAUSED_UNTIL = time.time() + 6 * 60 * 60

# -------------------------------------------------------------------
# strategy core
# -------------------------------------------------------------------
def decide_action(price: Optional[float], closes: List[float], pos_qty: float):
    if not price or len(closes) < 20:
        return "hold", "insufficient data"
    sma20 = mean(closes[-20:])
    edge_pct = (price - sma20) / sma20 * 100.0 if sma20 else 0.0
    entry_edge = env_float("EDGE_ENTRY_PCT", 0.25)
    exit_edge  = env_float("EDGE_EXIT_PCT", -0.25)
    if edge_pct > entry_edge and pos_qty <= 0:
        return "buy", f"edge {edge_pct:.2f}% > {entry_edge}%"
    if pos_qty > 0 and edge_pct < exit_edge:
        return "sell_all", f"edge {edge_pct:.2f}% < {exit_edge}%"
    return "hold", f"edge {edge_pct:.2f}%"

def place_brackets(symbol: str, avg_fill: float, qty: float, atr: Optional[float], ex) -> bool:
    """
    Place bracket orders for a position using BracketOrderManager.
    
    CRITICAL SAFETY: Brackets are MANDATORY - NEVER skipped.
    - If ATR is missing, uses fallback percentage-based stops
    - If brackets cannot be placed, returns False (caller must flatten position)
    - NO NAKED POSITIONS - EVER
    
    Returns:
        True if brackets placed successfully, False otherwise
    """
    if not (avg_fill and qty):
        print(f"[BRACKET-ERR] {symbol} - missing required parameters (price or qty)")
        return False
    
    # CRITICAL: ALWAYS use bracket manager - never skip brackets
    if not BRACKET_MANAGER_ENABLED or get_bracket_manager is None:
        print(f"🚨 [BRACKET-ERR] {symbol} - Bracket manager not available, CANNOT TRADE SAFELY")
        return False
    
    try:
        manager = get_bracket_manager()
        
        # Calculate bracket prices (ALWAYS - even without ATR)
        bracket = manager.calculate_bracket_prices(
            symbol=symbol,
            side="buy",  # Assuming long positions for now
            entry_price=avg_fill,
            atr=atr  # Will use fallback if None
        )
        
        if not bracket:
            print(f"🚨 [BRACKET-ERR] {symbol} - Failed to calculate bracket prices")
            return False
        
        # Update quantity from caller
        bracket.quantity = qty
        
        # Recalculate metrics with actual quantity
        bracket.risk_usd = abs(bracket.entry_price - bracket.stop_price) * qty
        bracket.reward_usd = abs(bracket.take_profit_price - bracket.entry_price) * qty
        bracket.rr_ratio = bracket.reward_usd / bracket.risk_usd if bracket.risk_usd > 0 else 0
        
        # Validate bracket can be placed
        can_place, reason, adjusted_qty = manager.validate_bracket_can_be_placed(
            bracket, ex, allow_adjust=True
        )
        
        if not can_place:
            print(f"🚨 [BRACKET-ERR] {symbol} - Cannot place brackets: {reason}")
            return False
        
        # Use adjusted quantity if provided
        if adjusted_qty:
            print(f"[BRACKET-ADJUST] {symbol} - {reason}")
            bracket.quantity = adjusted_qty
        
        # Place brackets using commands.py
        from commands import handle as run_command
        success, message = manager.place_bracket_orders(bracket, ex, run_command)
        
        if success:
            print(f"✅ [BRACKET-OK] {symbol} - TP@{bracket.take_profit_price} SL@{bracket.stop_price} R:R={bracket.rr_ratio:.2f}")
            return True
        else:
            print(f"🚨 [BRACKET-FAILED] {symbol} - {message}")
            return False
            
    except Exception as e:
        print(f"🚨 [BRACKET-EXCEPTION] {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return False

# -------------------------------------------------------------------
# diagnostics
# -------------------------------------------------------------------
def collect_diagnostics(trade_log: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(trade_log)
    return {
        "total_trades": total,
        "buys": sum(1 for t in trade_log if t.get("action") == "buy"),
        "sells": sum(1 for t in trade_log if t.get("action") == "sell_all"),
        "win_rate_pct": None,
        "avg_profit_usd": None,
    }

# -------------------------------------------------------------------
# one loop pass
# -------------------------------------------------------------------
def loop_once(ex, symbols: List[str]) -> None:
    from commands import handle as run_command  # local import
    global _DAY_START_EQUITY

    # 1. API WATCHDOG - Check exchange health first
    if API_WATCHDOG_ENABLED and get_watchdog:
        try:
            watchdog = get_watchdog()
            health_check = watchdog.check_health(ex)
            if watchdog.should_restart():
                watchdog.restart_bot()
        except Exception as e:
            print(f"[WATCHDOG-ERR] {e}")

    # 2. equity & day start
    try:
        bal: Dict[str, Any] = ex.fetch_balance()
        eq_now = account_equity_usd(bal)
        if _DAY_START_EQUITY is None:
            _DAY_START_EQUITY = eq_now
    except Exception as e:
        print("[BAL-ERR]", e)
        eq_now = 0.0

    # 2b. MENTAL SL/TP MONITORING - Check all open positions for exit triggers
    try:
        print(f"\n{get_position_summary()}")
        
        # Define price fetcher for position monitoring
        def fetch_current_price(symbol: str) -> float:
            try:
                ticker = ex.fetch_ticker(symbol)
                return ticker.get('last') or ticker.get('close') or ticker.get('bid', 0)
            except Exception as e:
                print(f"[PRICE-FETCH-ERR] {symbol}: {e}")
                return 0.0
        
        # Check all positions for SL/TP triggers
        exit_signals = check_all_positions_for_exits(fetch_current_price)
        
        # Execute market exits for triggered positions
        for signal in exit_signals:
            sym = signal['symbol']
            trigger = signal['trigger']
            current_price = signal['current_price']
            position = signal['position']
            
            print(f"\n{'='*60}")
            print(f"🚨 [EXIT-TRIGGER-DETECTED] {sym} - {trigger.upper()} HIT")
            print(f"   Entry: ${position.entry_price:.4f} → Current: ${current_price:.4f}")
            print(f"   Target: ${position.stop_loss_price if trigger == 'stop_loss' else position.take_profit_price:.4f}")
            print(f"{'='*60}\n")
            
            # DUST PREVENTION: Check if position is dust before attempting exit
            if check_if_dust_position(sym, current_price):
                print(f"⚠️  [DUST-SKIP] {sym} - Position is DUST (below Kraken minimum), skipping exit attempt")
                print(f"   Manual action required: Consolidate via Kraken 'Buy Crypto' button")
                continue
            
            # Execute market SELL
            reason = f"{trigger}_trigger_{current_price:.4f}"
            result = execute_market_exit(
                symbol=sym,
                quantity=position.quantity,
                full_position=True,
                source="autopilot_sl_tp",
                reason=reason
            )
            
            if result.success:
                pnl_usd = (result.fill_price - position.entry_price) * result.filled_qty
                pnl_pct = ((result.fill_price - position.entry_price) / position.entry_price) * 100
                
                print(f"✅ [EXIT-EXECUTED] {sym} - {trigger.upper()}")
                print(f"   Fill: {result.filled_qty:.6f} @ ${result.fill_price:.4f}")
                print(f"   P&L: ${pnl_usd:.2f} ({pnl_pct:+.2f}%)")
                print(f"   Fee: ${result.fee:.4f}")
                
                if notify_position_exit:
                    exit_type = "🛑 Stop-Loss" if trigger == "stop_loss" else "🎯 Take-Profit"
                    notify_position_exit(
                        symbol=sym,
                        entry_price=position.entry_price,
                        exit_price=result.fill_price,
                        quantity=result.filled_qty,
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                        exit_type=exit_type
                    )
            else:
                print(f"❌ [EXIT-FAILED] {sym} - {result.error}")
                print(f"   WARNING: Position may still be tracked - manual intervention may be required")
    
    except Exception as monitor_err:
        print(f"[POSITION-MONITOR-ERR] {monitor_err}")

    # 3. PROFIT TARGET - Check if we should trade
    if PROFIT_TARGET_ENABLED and get_target_system:
        try:
            target_sys = get_target_system()
            target_sys.update_equity(eq_now)
            allowed, reason = target_sys.should_trade(eq_now)
            if not allowed:
                print(target_sys.get_status_message())
                return  # Exit early if paused after hitting target
            progress = target_sys.get_progress()
            if progress.get("initialized"):
                print(f"[TARGET] {progress['progress_pct']:.1f}% to goal "
                      f"(${progress['profit_today']:.2f}/${progress['target_usd']:.2f})")
        except Exception as e:
            print(f"[TARGET-ERR] {e}")

    # 4. kill-switch
    try:
        day_pnl = eq_now - (_DAY_START_EQUITY or eq_now)
        max_daily_loss = env_float("MAX_DAILY_LOSS_USD", 999999.0)
        if day_pnl <= -abs(max_daily_loss):
            trigger_kill(ex)
    except Exception as e:
        print("[KILL-CHECK-ERR]", e)

    # Read state once at start of loop for candle tracking
    state = read_state()
    
    # per-symbol logic + trade log
    trade_log: List[Dict[str, Any]] = []
    for sym in symbols:
        action = "unknown"  # Initialize to avoid unbound variable in error handler
        trading_mode = get_mode_str()  # Get trading mode for logging
        try:
            if paused():
                print(f"[PAUSED] global pause active; skip {sym}")
                log_evaluation(
                    symbol=sym,
                    decision="SKIP",
                    reason="Global pause active",
                    trading_mode=trading_mode
                )
                continue
            if cooldown_active(sym):
                print(f"[COOLDOWN] {sym} — skipping")
                log_evaluation(
                    symbol=sym,
                    decision="SKIP",
                    reason="Cooldown active",
                    trading_mode=trading_mode
                )
                continue

            # NEW: Fetch 5-minute OHLC candles for closed-candle strategy
            try:
                manager = get_manager()
                ohlcv = manager.fetch_ohlc(sym, timeframe='5m', limit=100)
            except Exception as fetch_err:
                print(f"[OHLC-ERR] {sym} - Failed to fetch 5m candles: {fetch_err}")
                log_evaluation(
                    symbol=sym,
                    decision="ERROR",
                    reason=f"Failed to fetch OHLC data: {str(fetch_err)[:100]}",
                    trading_mode=trading_mode,
                    error_message=str(fetch_err)
                )
                continue
            
            # Validate candle data (need at least 20 for SMA20)
            is_valid, validation_reason = validate_candle_data(ohlcv, min_candles=20)
            if not is_valid:
                print(f"[SKIP] {sym} - {validation_reason}")
                log_evaluation(
                    symbol=sym,
                    decision="SKIP",
                    reason=f"Candle validation failed: {validation_reason}",
                    trading_mode=trading_mode
                )
                continue
            
            # Get candle tracking from state
            symbol_tracking = get_candle_tracking_for_symbol(state, sym)
            last_known_ts = symbol_tracking.get('last_closed_ts')
            
            # Check if new candle closed
            latest_ts = get_latest_candle_timestamp(ohlcv)
            if latest_ts is None:
                print(f"[SKIP] {sym} - Cannot get candle timestamp")
                log_evaluation(
                    symbol=sym,
                    decision="SKIP",
                    reason="Cannot get candle timestamp",
                    trading_mode=trading_mode
                )
                continue
            
            new_candle = is_new_candle_closed(last_known_ts, latest_ts, timeframe_seconds=300)
            
            # Extract data and get current position
            closes = extract_closes(ohlcv)
            current_close = closes[-1]
            pos_qty, usd_cash = position_qty(ex, sym)
            
            # If no new candle closed, skip signal evaluation BUT STILL LOG
            if not new_candle:
                print(f"[WAIT] {sym} - No new 5m candle closed yet (last={last_known_ts}, latest={latest_ts})")
                log_evaluation(
                    symbol=sym,
                    decision="SKIP",
                    reason="Waiting for new 5-minute candle to close",
                    trading_mode=trading_mode,
                    price=current_close,
                    candle_timestamp=str(latest_ts) if latest_ts else None,
                    current_position_qty=pos_qty,
                    current_position_value=pos_qty * current_close if pos_qty and current_close else 0
                )
                continue
            
            # NEW CANDLE CLOSED - Calculate ALL indicators for regime detection
            current_sma20 = calculate_sma(closes, period=20)
            current_sma50 = calculate_sma(closes, period=50)
            rsi = calculate_rsi(closes, period=14)
            atr = calculate_atr(ohlcv, period=14)
            adx = calculate_adx(ohlcv, period=14)
            bb_result = calculate_bollinger_bands(closes, period=20, std_dev=2.0)
            
            # Calculate BB position (0-1 where price is within the bands)
            bb_position = None
            if bb_result and len(bb_result) == 3:
                bb_middle, bb_upper, bb_lower = bb_result
                if bb_upper and bb_lower and bb_upper != bb_lower:
                    bb_position = (current_close - bb_lower) / (bb_upper - bb_lower)
            
            # Calculate volume percentile
            volumes = [candle[5] for candle in ohlcv if len(candle) > 5]
            current_volume = volumes[-1] if volumes else 0
            volume_percentile = calculate_volume_percentile(current_volume, volumes[-20:]) if len(volumes) >= 20 else None
            
            # Calculate 24h volume in USD for regime filter
            # Fetch ticker to get quoteVolume (already in USD)
            volume_usd_24h = None
            try:
                ticker = ex.fetch_ticker(sym)
                volume_usd_24h = ticker.get('quoteVolume', 0) or 0
            except Exception as vol_err:
                pass  # Volume data not critical - regime filter handles None gracefully
            
            # Validate minimum required indicators
            if not current_sma20 or not atr:
                print(f"[SKIP] {sym} - Missing critical indicators (SMA20={current_sma20}, ATR={atr})")
                continue
            
            # Build indicators dict for strategy orchestrator
            indicators_5m = {
                'sma20': current_sma20,
                'sma50': current_sma50 or 0.0,
                'rsi': rsi or 50.0,
                'atr': atr,
                'adx': adx or 0.0,
                'bb_middle': bb_result[0] if bb_result else current_sma20,
                'bb_upper': bb_result[1] if bb_result else current_sma20 * 1.02,
                'bb_lower': bb_result[2] if bb_result else current_sma20 * 0.98,
                'volume_percentile': volume_percentile or 50.0
            }
            
            # REGIME-AWARE SIGNAL GENERATION
            trade_signal = None
            regime = None
            
            try:
                orchestrator = get_orchestrator()
                trade_signal = orchestrator.generate_signal(
                    symbol=sym,
                    ohlcv_5m=ohlcv,
                    indicators_5m=indicators_5m,
                    volume_usd_24h=volume_usd_24h
                )
                
                # Extract action and reasoning from trade signal
                action = trade_signal.action  # 'long', 'short', 'hold', 'sell_all'
                why = trade_signal.reason
                price = current_close
                regime = trade_signal.regime
                
                # CRITICAL FIX: Normalize strategy actions to execution actions
                # Strategy returns 'long'/'short', but execution needs 'buy'/'sell'
                if action == "long":
                    exec_action = "buy"
                elif action == "short":
                    exec_action = "sell"
                else:
                    exec_action = action  # 'hold', 'sell_all' pass through
                
                # Log regime and strategy details
                print(f"[REGIME] {sym} - {regime.value} (confidence={trade_signal.confidence:.2f})")
                print(f"[SIGNAL] {sym} - {action.upper()}: {why}")
                print(f"[ACTION-DEBUG] {sym} - action={action}, exec_action={exec_action}")
                
                if trade_signal.htf_aligned:
                    print(f"[HTF] {sym} - Aligned {trade_signal.dominant_trend} trend on 15m/1h")
                elif trade_signal.dominant_trend:
                    print(f"[HTF] {sym} - Dominant {trade_signal.dominant_trend} trend (not fully aligned)")
                
                # Log the signal decision with full indicator context
                log_evaluation(
                    symbol=sym,
                    decision=action.upper(),  # 'long' -> 'LONG', 'hold' -> 'HOLD'
                    reason=why,
                    trading_mode=trading_mode,
                    price=price,
                    rsi=rsi,
                    atr=atr,
                    volume=volume_percentile if volume_percentile else None,
                    regime=regime.value if regime else None,
                    adx=adx,
                    bb_position=bb_position,
                    sma20=current_sma20,
                    sma50=current_sma50,
                    candle_timestamp=str(latest_ts) if latest_ts else None,
                    current_position_qty=pos_qty,
                    current_position_value=pos_qty * price if pos_qty and price else 0
                )
                
            except Exception as e:
                print(f"[ORCHESTRATOR-ERR] {sym}: {e}")
                import traceback
                traceback.print_exc()
                # Fallback to hold on orchestrator error and LOG the error
                action = "hold"
                exec_action = "hold"  # Set exec_action for execution path
                why = f"Orchestrator error: {e}"
                price = current_close
                regime = None
                
                log_evaluation(
                    symbol=sym,
                    decision="ERROR",
                    reason=f"Orchestrator error: {str(e)[:100]}",
                    trading_mode=trading_mode,
                    price=price,
                    error_message=str(e),
                    candle_timestamp=str(latest_ts) if latest_ts else None,
                    current_position_qty=pos_qty,
                    current_position_value=pos_qty * price if pos_qty and price else 0
                )
            
            # CRITICAL: Exit positions in bearish regimes
            # TREND_DOWN regime with open position → Force exit
            # Use 0.001 threshold to ignore dust balances
            if regime and regime.value == 'TREND_DOWN' and pos_qty > 0.001:
                action = "sell_all"
                exec_action = "sell_all"  # Update exec_action too
                confidence_str = f"(confidence={trade_signal.confidence:.2f})" if trade_signal else ""
                why = f"TREND_DOWN regime - exit long position {confidence_str}"
                print(f"[REGIME-EXIT] {sym} - Forcing exit due to bearish regime")
            
            # Adjust action based on position
            # NOTE: Check the ORIGINAL action (before normalization) because we normalize long→buy on line 763
            # Use 0.001 threshold to ignore dust balances
            if action == 'long' and pos_qty > 0.001:
                action = "hold"
                exec_action = "hold"  # Update exec_action too
                why = f"LONG signal but already in position ({pos_qty:.6f})"
                print(f"[POSITION-BLOCK] {sym} - Already holding {pos_qty:.6f}, skipping LONG signal")
            
            # Calculate edge for logging
            edge_pct = ((price - current_sma20) / current_sma20 * 100.0) if current_sma20 else None
            
            # Update candle tracking AFTER signal evaluation
            update_candle_tracking(state, sym, latest_ts, current_sma20, current_close)

            # EXECUTION ROUTING: Use exec_action (not raw action from strategy)
            # DIAGNOSTIC: Log when we reach buy execution path
            if exec_action == "buy" and price:
                print(f"🎯 [EXEC-PATH] {sym} - ENTERING BUY EXECUTION (action={action}, exec_action={exec_action}, pos_qty={pos_qty})")
                
                # CRITICAL FIX: Load config BEFORE using it
                try:
                    config = get_config()
                except Exception as cfg_err:
                    print(f"[CONFIG-ERR] {sym} - Failed to load config: {cfg_err} - BLOCKING trade")
                    log_evaluation(
                        symbol=sym,
                        decision="ERROR",
                        reason=f"Config load failed: {str(cfg_err)[:100]}",
                        trading_mode=trading_mode,
                        error_message=str(cfg_err)
                    )
                    continue
                
                eq_full: Dict[str, Any] = ex.fetch_balance()
                eq_usd = account_equity_usd(eq_full)
                
                # ═══════════════════════════════════════════════════════════════
                # POSITION SIZING: Market-only vs. Bracket mode
                # ═══════════════════════════════════════════════════════════════
                
                if not config.use_brackets:
                    # MARKET-ONLY MODE: Use SL-independent position sizing
                    print(f"[MARKET-SIZING] {sym} - Using calculate_market_position_size() (no SL dependency)")
                    
                    sizing_result = calculate_market_position_size(
                        equity=eq_usd,
                        entry_price=price,
                        risk_per_trade_pct=0.005,  # 0.5% risk per trade
                        atr=atr,
                        use_synthetic_sl=True,  # Use ATR-based sizing if ATR available
                        synthetic_sl_multiplier=2.0,
                        max_position_pct=0.10  # Max 10% of equity
                    )
                    
                    usd_to_spend = sizing_result["position_size_usd"]
                    approx_qty = sizing_result["quantity"]
                    sizing_method = sizing_result["method"]
                    
                    print(
                        f"[MARKET-SIZING] {sym} - Method: {sizing_method}, "
                        f"Position: ${usd_to_spend:.2f}, Qty: {approx_qty:.6f}, "
                        f"Risk: ${sizing_result['risk_usd']:.2f} ({sizing_result['risk_pct']:.2f}%)"
                    )
                    
                else:
                    # BRACKET MODE: Use traditional ATR-based sizing (SL-dependent)
                    print(f"[BRACKET-SIZING] {sym} - Using qty_from_atr() (SL-dependent)")
                    qty = qty_from_atr(eq_usd, atr, price)
                    usd_to_spend = qty * price
                    approx_qty = qty

                # Validate position size
                if usd_to_spend <= 0.0:
                    print(f"[SKIP] {sym} qty=0 ({why})")
                    continue

                # Apply global position cap (backup safety check)
                max_position_env = env_float("MAX_POSITION_USD", 10.0)
                if usd_to_spend > max_position_env:
                    print(f"[POSITION-CAP] {sym} - Capping ${usd_to_spend:.2f} → ${max_position_env:.2f}")
                    usd_to_spend = max_position_env
                    approx_qty = usd_to_spend / price if price else 0.0
                
                # Check available cash
                if usd_to_spend > usd_cash + 1e-6:
                    print(f"[CASH-LIMIT] {sym} - Reducing ${usd_to_spend:.2f} → ${usd_cash:.2f}")
                    usd_to_spend = max(0.0, usd_cash)
                    approx_qty = usd_to_spend / price if price else 0.0
                
                if usd_to_spend <= 0.0:
                    print(f"[SKIP] {sym} no cash ({why})")
                    continue
                
                # ═══════════════════════════════════════════════════════════════
                # RISK MANAGEMENT CHECKS (MANDATORY - ALL MUST PASS)
                # ═══════════════════════════════════════════════════════════════
                
                # 1. DAILY TRADE LIMITS: Check global daily limit (applies to both paper/live)
                try:
                    mode_str = get_mode_str()
                    allowed, limit_reason = can_open_new_trade(sym, mode_str)
                    if not allowed:
                        print(f"🚫 [DAILY-LIMIT-BLOCK] {sym} - {limit_reason}")
                        log_evaluation(
                            symbol=sym,
                            decision="NO_TRADE",
                            reason=f"Daily limit reached: {limit_reason}",
                            trading_mode=trading_mode,
                            price=price,
                            rsi=rsi,
                            atr=atr,
                            regime=regime.value if regime else None,
                            adx=adx,
                            sma20=current_sma20,
                            sma50=current_sma50,
                            candle_timestamp=str(latest_ts) if latest_ts else None,
                            current_position_qty=pos_qty,
                            current_position_value=pos_qty * price if pos_qty and price else 0
                        )
                        continue
                except Exception as limit_err:
                    print(f"[DAILY-LIMIT-ERR] {sym}: {limit_err} - BLOCKING trade for safety")
                    log_evaluation(
                        symbol=sym,
                        decision="ERROR",
                        reason=f"Daily limit check error: {str(limit_err)[:100]}",
                        trading_mode=trading_mode,
                        error_message=str(limit_err)
                    )
                    continue
                
                # 2. PER-TRADE RISK: Calculate and validate stop-loss based risk using risk_manager
                # Note: Brackets calculate SL using ATR, so we estimate risk here
                # Actual SL will be set by bracket manager after entry
                new_position = None  # Initialize to avoid unbound variable
                trade_risk = None
                
                try:
                    if atr and atr > 0:
                        # Estimate SL based on 2x ATR (same as bracket manager)
                        estimated_sl = price - (2.0 * atr)  # Long position SL
                        
                        # Create PositionSnapshot for the new trade
                        # NOTE: Must match PositionSnapshot protocol with Optional[float] for stop_loss
                        class NewTradeSnapshot:
                            def __init__(self, side, entry_price, stop_loss, quantity):
                                self.side = side
                                self.entry_price = entry_price
                                self.stop_loss: Optional[float] = stop_loss  # Type hint for protocol
                                self.quantity = quantity
                        
                        new_position = NewTradeSnapshot(
                            side='long',
                            entry_price=price,
                            stop_loss=estimated_sl,
                            quantity=approx_qty
                        )
                        
                        # Use risk_manager to calculate risk
                        trade_risk = calculate_trade_risk(new_position)
                        
                        print(f"[RISK-CHECK] {sym} - Estimated risk: ${trade_risk:.2f} (SL: ${estimated_sl:.2f}, qty: {approx_qty:.6f})")
                    else:
                        print(f"[RISK-CHECK] {sym} - No ATR available, will use bracket defaults")
                except ValueError as val_err:
                    # calculate_trade_risk raises ValueError for invalid SL placement
                    print(f"🚫 [RISK-CALC-BLOCK] {sym} - {val_err}")
                    log_evaluation(
                        symbol=sym,
                        decision="NO_TRADE",
                        reason=f"Risk validation failed: {str(val_err)[:100]}",
                        trading_mode=trading_mode,
                        price=price,
                        rsi=rsi,
                        atr=atr,
                        regime=regime.value if regime else None,
                        adx=adx,
                        sma20=current_sma20,
                        sma50=current_sma50,
                        candle_timestamp=str(latest_ts) if latest_ts else None,
                        current_position_qty=pos_qty,
                        current_position_value=pos_qty * price if pos_qty and price else 0
                    )
                    continue
                except Exception as risk_err:
                    print(f"[RISK-CALC-ERR] {sym}: {risk_err} - proceeding with caution")
                    log_evaluation(
                        symbol=sym,
                        decision="ERROR",
                        reason=f"Risk calculation error: {str(risk_err)[:100]}",
                        trading_mode=trading_mode,
                        error_message=str(risk_err)
                    )
                
                # 3. PORTFOLIO-WIDE RISK: Check max active risk (2% of equity)
                # LIMITATION: Currently only checks the NEW trade's risk
                # Full portfolio aggregation requires tracking SL for all open positions (future enhancement)
                try:
                    # Build list of open positions for risk aggregation
                    # NOTE: We can only include the NEW trade since we don't persist SL values for existing positions
                    # This is a known limitation - bracket SL values are not tracked in state
                    positions_for_risk = []
                    
                    # Add the NEW trade if it has ATR (and thus estimated SL)
                    if new_position is not None:
                        positions_for_risk.append(new_position)  # from previous block
                    
                    # Call get_max_active_risk() to check portfolio-wide limit
                    risk_check = get_max_active_risk(
                        open_positions=positions_for_risk,
                        equity=eq_usd,
                        max_active_risk_pct=0.02  # 2% max portfolio risk
                    )
                    
                    # Log risk status
                    print(f"[PORTFOLIO-RISK] {sym} - Active risk: ${risk_check['total_active_risk']:.2f} / ${risk_check['max_allowed_risk']:.2f} ({risk_check['risk_pct']:.2%})")
                    
                    # NOTE: We don't enforce portfolio risk yet because we can't track existing position SLs
                    # Once bracket state tracking is implemented, uncomment this block:
                    # if not risk_check['within_limits']:
                    #     print(f"🚫 [PORTFOLIO-RISK-BLOCK] {sym} - Would exceed max active risk (2% of equity)")
                    #     continue
                    
                    print(f"[PORTFOLIO-RISK] {sym} - Check passed (enforcement deferred pending SL tracking)")
                    
                except NameError:
                    # new_position not defined (no ATR) - skip portfolio check
                    print(f"[PORTFOLIO-RISK] {sym} - Skipping check (no ATR for risk calculation)")
                except Exception as portfolio_err:
                    print(f"[PORTFOLIO-RISK-ERR] {sym}: {portfolio_err} - proceeding with caution")
                
                # ═══════════════════════════════════════════════════════════════
                # END RISK MANAGEMENT CHECKS
                # ═══════════════════════════════════════════════════════════════
                
                # CRITICAL SAFETY CHECK: Ensure brackets can be placed before trading
                # If brackets can't meet minimum volume, increase position size or skip trade
                try:
                    market = ex.market(sym) or {}
                    limits = market.get("limits") or {}
                    min_amt = float((limits.get("amount") or {}).get("min", 0) or 0)
                    min_cost = float((limits.get("cost") or {}).get("min", 0) or 0)
                    
                    # Check if bracket qty will meet minimum
                    if min_amt > 0 and approx_qty < min_amt:
                        # Try to increase to minimum
                        adjusted_qty = min_amt * 1.05  # 5% buffer
                        adjusted_cost = adjusted_qty * price
                        
                        if adjusted_cost <= env_float("MAX_POSITION_USD", 10.0) and adjusted_cost <= usd_cash:
                            print(f"[ADJUST] {sym} qty {approx_qty:.6f} → {adjusted_qty:.6f} to meet bracket minimum")
                            approx_qty = adjusted_qty
                            usd_to_spend = adjusted_cost
                        else:
                            print(f"[SKIP] {sym} qty {approx_qty:.6f} < min {min_amt:.6f} and can't increase - SAFETY: no trade without brackets")
                            continue
                    
                    if min_cost > 0 and approx_qty * price < min_cost:
                        adjusted_cost = min_cost * 1.05
                        if adjusted_cost <= env_float("MAX_POSITION_USD", 10.0) and adjusted_cost <= usd_cash:
                            print(f"[ADJUST] {sym} cost ${approx_qty * price:.2f} → ${adjusted_cost:.2f} to meet bracket minimum")
                            approx_qty = adjusted_cost / price
                            usd_to_spend = adjusted_cost
                        else:
                            print(f"[SKIP] {sym} cost ${approx_qty * price:.2f} < min ${min_cost:.2f} and can't increase - SAFETY: no trade without brackets")
                            continue
                except Exception as e:
                    print(f"[BRACKET-CHECK-ERR] {sym}: {e} - proceeding with caution")
                
                # Recalculate final qty after adjustments
                approx_qty = usd_to_spend / price if price else 0.0
                
                # ═══════════════════════════════════════════════════════════════
                # EXECUTION MODE ROUTING: Market-only vs. Bracket orders
                # ═══════════════════════════════════════════════════════════════
                
                config = get_config()
                
                if not config.use_brackets:
                    # ────────────────────────────────────────────────────────────
                    # MARKET_ONLY MODE: Simple market buy, no brackets
                    # ────────────────────────────────────────────────────────────
                    
                    # ═══════════════════════════════════════════════════════════════
                    # FEE CHECK: Block trades that can't cover transaction costs
                    # ═══════════════════════════════════════════════════════════════
                    try:
                        # BYPASS CHECK: If BYPASS_FEE_BLOCK=1, skip all fee validation
                        bypass_fee_check = env_str("BYPASS_FEE_BLOCK", "0") == "1"
                        
                        if bypass_fee_check:
                            print(f"🔓 [FEE-BYPASS] {sym} - BYPASS_FEE_BLOCK=1, skipping fee validation")
                        else:
                            min_edge_required = get_minimum_edge_pct(safety_margin=0.08)  # 0.08% safety buffer (aggressive mode)
                            taker_fee_pct = get_taker_fee(sym) * 100  # Convert to percentage
                            
                            # edge_pct is calculated earlier as: ((price - sma20) / sma20) * 100
                            # For a profitable trade: edge_pct must exceed round-trip fees + buffer
                            if edge_pct is not None:
                                if edge_pct < min_edge_required:
                                    print(f"🚫 [FEE-BLOCK] {sym} - Edge {edge_pct:.2f}% < required {min_edge_required:.2f}%")
                                    print(f"   Taker fee: {taker_fee_pct:.4f}%, Round-trip: {taker_fee_pct*2:.4f}%, Required with buffer: {min_edge_required:.2f}%")
                                    print(f"   SKIPPING: Trade cannot profitably cover fees")
                                
                                # Log fee block event
                                if TELEMETRY_ENABLED and log_decision:
                                    try:
                                        log_decision(
                                            sym, "fee_block", 
                                            f"Edge {edge_pct:.2f}% < required {min_edge_required:.2f}%", 
                                            price, edge_pct, atr, pos_qty, eq_usd, executed=False
                                        )
                                    except Exception:
                                        pass
                                
                                    continue  # Skip this trade
                                else:
                                    print(f"✅ [FEE-CHECK-PASS] {sym} - Edge {edge_pct:.2f}% > required {min_edge_required:.2f}% (fee-profitable)")
                            else:
                                print(f"⚠️  [FEE-CHECK-SKIP] {sym} - No edge_pct available, proceeding with caution")
                    
                    except Exception as fee_err:
                        print(f"[FEE-CHECK-ERR] {sym}: {fee_err} - proceeding without fee validation")
                    
                    print(f"[MARKET-MODE] {sym} - Executing BUY via execute_entry_with_mode()")
                    
                    result = execute_entry_with_mode(
                        symbol=sym,
                        size_usd=usd_to_spend,
                        source="autopilot",
                        atr=atr,
                        reason=why
                    )
                    
                    if result.success:
                        print(f"✅ [MARKET-ENTRY-SUCCESS] {sym} - {result}")
                        
                        # Record trade for daily limits
                        try:
                            record_trade_opened(sym, mode_str)
                            print(f"[DAILY-LIMIT] {sym} - Trade recorded (mode: {mode_str})")
                        except Exception as record_err:
                            print(f"[DAILY-LIMIT-RECORD-ERR] {sym}: {record_err}")
                        
                        # MENTAL SL/TP: Store position with calculated exit levels FIRST
                        # (we need the SL/TP prices for the Discord notification)
                        position = None
                        try:
                            position = add_position(
                                symbol=sym,
                                entry_price=result.fill_price,
                                quantity=result.filled_qty,
                                atr=atr if atr and atr > 0 else result.fill_price * 0.02,
                                atr_sl_multiplier=2.0,
                                atr_tp_multiplier=3.0,
                                source="autopilot"
                            )
                            print(f"📍 [POSITION-STORED] {sym} - SL=${position.stop_loss_price:.4f}, TP=${position.take_profit_price:.4f}")
                        except Exception as pos_err:
                            print(f"[POSITION-TRACKER-ERR] {sym}: {pos_err} - position not tracked!")
                        
                        # Log to telemetry and send Discord notification (now with SL/TP)
                        if TELEMETRY_ENABLED and log_decision:
                            try:
                                log_decision(sym, "buy", why, result.fill_price, edge_pct, atr, pos_qty, eq_usd, executed=True)
                                if notify_trade:
                                    sl_price = position.stop_loss_price if position else None
                                    tp_price = position.take_profit_price if position else None
                                    notify_trade(sym, "buy", result.filled_qty, result.fill_price, why, sl_price, tp_price)
                            except Exception as log_err:
                                print(f"[TELEMETRY-ERR] {log_err}")
                        
                        trade_log.append({"symbol": sym, "action": "market_buy", "usd": float(f"{usd_to_spend:.2f}")})
                        print(f"🎯 [MARKET-BUY-COMPLETE] {sym} - Position opened, monitoring for exit signals")
                        
                        # CRITICAL: Exit loop iteration to prevent fallthrough to bracket code
                        continue
                    else:
                        print(f"❌ [MARKET-ENTRY-FAILED] {sym} - {result.error}")
                        continue
                
                elif BRACKET_MANAGER_ENABLED and get_bracket_manager is not None:
                    # ────────────────────────────────────────────────────────────
                    # BRACKET MODE: Place entry with TP/SL brackets
                    # ────────────────────────────────────────────────────────────
                    
                    print(f"[BRACKET-MODE] {sym} - Executing BUY with TP/SL brackets")
                    
                    # Execute trade (or simulate if backtest mode)
                    if BACKTEST_MODE_ENABLED and get_backtest:
                        backtest = get_backtest()
                        safe_price = price if price else 0.0
                        result = backtest.execute_trade(sym, "buy", safe_price, usd_to_spend, why)
                        result_str = f"[BACKTEST] Buy executed - no real order"
                        trade_success = True
                        order_result = None
                    else:
                        # Place entry order WITH brackets attached atomically
                        manager = get_bracket_manager()
                        bracket_order = manager.calculate_bracket_prices(
                            symbol=sym,
                            side="buy",
                            entry_price=price,
                            atr=atr
                        )
                        
                        if not bracket_order:
                            print(f"🚨 [BRACKET-CALC-ERR] {sym} - Failed to calculate bracket order, skipping")
                            continue
                        
                        # Set quantity based on USD amount
                        bracket_order.quantity = approx_qty
                        bracket_order.recalculate_metrics()
                        
                        # Execute atomic bracket order
                        trade_success, trade_message, order_result = manager.place_entry_with_brackets(bracket_order, ex)
                        result_str = trade_message
                    
                    print(result_str)
                    
                    # Only proceed with logging if trade succeeded
                    if not trade_success:
                        print(f"🚨 [TRADE-FAILED] {sym} - Entry order rejected: {result_str}")
                        continue
                    
                    # Extract actual fill info from order result
                    # NOTE: Fill data is nested inside order_result['fill_data'] from bracket_order_manager
                    actual_qty = approx_qty  # Default to estimate
                    actual_price = price
                    if order_result:
                        fill_data = order_result.get('fill_data', {})
                        if fill_data:
                            actual_qty = fill_data.get('filled', approx_qty)
                            actual_price = fill_data.get('average', price) or price
                        else:
                            # Fallback for direct keys (legacy or immediate fills)
                            actual_qty = order_result.get('filled', approx_qty)
                            actual_price = order_result.get('average', price) or price
                    
                    print(f"✅ [TRADE-SUCCESS] {sym} - Entry filled: {actual_qty:.6f} @ ${actual_price:.4f} | TP/SL attached via Kraken conditional close")
                    
                    # Record trade opened for daily limit tracking (applies to both paper/live)
                    try:
                        record_trade_opened(sym, mode_str)
                        print(f"[DAILY-LIMIT] {sym} - Trade recorded (mode: {mode_str})")
                    except Exception as record_err:
                        print(f"[DAILY-LIMIT-RECORD-ERR] {sym}: {record_err}")
                    
                    # Record to profit target system
                    if PROFIT_TARGET_ENABLED and get_target_system:
                        try:
                            target_sys = get_target_system()
                            target_sys.record_trade(0.0)  # Buy has no immediate profit
                        except Exception as e:
                            print(f"[TARGET-RECORD-ERR] {e}")
                    
                    trade_log.append({"symbol": sym, "action": "buy_with_brackets", "usd": float(f"{usd_to_spend:.2f}")})
                    
                    # POSITION TRACKER: Store position for monitoring alongside exchange orders
                    # This ensures position_tracker.py tracks the position with actual SL/TP prices
                    # Get SL/TP prices from bracket_order for Discord notification
                    bracket_sl_price = bracket_order.stop_price if bracket_order else None
                    bracket_tp_price = bracket_order.take_profit_price if bracket_order else None
                    
                    try:
                        
                        if bracket_sl_price and bracket_tp_price and actual_qty > 0:
                            # Create Position with ACTUAL bracket prices (not mental recalculation)
                            from position_tracker import Position, _load_positions_locked, _save_positions_locked, LOCK_FILE
                            import portalocker
                            
                            position = Position(
                                symbol=sym,
                                entry_price=actual_price,
                                quantity=actual_qty,
                                stop_loss_price=bracket_sl_price,
                                take_profit_price=bracket_tp_price,
                                atr=atr if atr and atr > 0 else actual_price * 0.02,
                                entry_timestamp=time.time(),
                                source="bracket_order",
                                is_short=False
                            )
                            
                            # Store with exclusive lock
                            with open(LOCK_FILE, 'a+') as lock_handle:
                                portalocker.lock(lock_handle, portalocker.LOCK_EX)
                                try:
                                    positions = _load_positions_locked(lock_handle)
                                    positions[sym] = position
                                    _save_positions_locked(positions, lock_handle)
                                finally:
                                    portalocker.unlock(lock_handle)
                            
                            print(f"📍 [BRACKET-POSITION-STORED] {sym} - SL=${bracket_sl_price:.4f}, TP=${bracket_tp_price:.4f} (real orders on Kraken)")
                        else:
                            print(f"⚠️  [BRACKET-POSITION-SKIP] {sym} - Missing SL/TP prices or qty, position not tracked")
                    except Exception as pos_err:
                        print(f"[BRACKET-POSITION-ERR] {sym}: {pos_err} - position not tracked in position_tracker!")
                    
                    # Log decision and trade to learning database (now with SL/TP)
                    if TELEMETRY_ENABLED and log_decision and log_trade:
                        try:
                            log_decision(sym, "buy", why, actual_price, edge_pct, atr, pos_qty, eq_usd, executed=True)
                            log_trade(sym, "buy", "market_buy", actual_qty, actual_price, usd_to_spend, None, why, "autopilot")
                            if notify_trade:
                                notify_trade(sym, "buy", actual_qty, actual_price or 0.0, why, bracket_sl_price, bracket_tp_price)
                        except Exception as log_err:
                            print(f"[TELEMETRY-ERR] {log_err}")
                    
                    # SUCCESS: Brackets are GUARANTEED by atomic order (conditional close API)
                    # No emergency flatten needed - if brackets fail, the entire order is rejected
                    print(f"✅ [BRACKET-GUARANTEED] {sym} - TP/SL attached via Kraken conditional close (atomic operation)")
                    
                    # CRITICAL: Exit loop iteration
                    continue
                
                else:
                    # Neither market-only nor bracket mode enabled - should not happen
                    print(f"[CONFIG-ERR] {sym} - Invalid execution mode configuration, skipping trade")
                    continue
            
            # ═══════════════════════════════════════════════════════════════
            # SHORT EXECUTION PATH (margin trading)
            # ═══════════════════════════════════════════════════════════════
            elif exec_action == "sell" and price:
                print(f"🎯 [EXEC-PATH] {sym} - ENTERING SHORT EXECUTION (action={action}, exec_action={exec_action}, pos_qty={pos_qty})")
                
                # Load config
                try:
                    config = get_config()
                except Exception as cfg_err:
                    print(f"[CONFIG-ERR] {sym} - Failed to load config: {cfg_err} - BLOCKING short trade")
                    log_evaluation(
                        symbol=sym,
                        decision="ERROR",
                        reason=f"Config load failed: {str(cfg_err)[:100]}",
                        trading_mode=trading_mode,
                        error_message=str(cfg_err)
                    )
                    continue
                
                # Check if shorts are enabled
                from margin_config import is_shorts_enabled
                if not is_shorts_enabled():
                    print(f"[SHORT-DISABLED] {sym} - Shorts disabled in config (ENABLE_SHORTS=0)")
                    continue
                
                eq_full: Dict[str, Any] = ex.fetch_balance()
                eq_usd = account_equity_usd(eq_full)
                
                # Position sizing for shorts (use same logic as longs)
                if not config.use_brackets:
                    # MARKET-ONLY MODE: Use SL-independent position sizing
                    print(f"[MARKET-SIZING] {sym} - Using calculate_market_position_size() for SHORT (no SL dependency)")
                    
                    sizing_result = calculate_market_position_size(
                        equity=eq_usd,
                        entry_price=price,
                        risk_per_trade_pct=0.005,  # 0.5% risk per trade (same as longs)
                        atr=atr,
                        use_synthetic_sl=True,  # Use ATR-based sizing if ATR available
                        synthetic_sl_multiplier=2.0,
                        max_position_pct=0.10  # Max 10% of equity
                    )
                    
                    usd_to_spend = sizing_result["position_size_usd"]
                    approx_qty = sizing_result["quantity"]
                    sizing_method = sizing_result["method"]
                    
                    print(
                        f"[MARKET-SIZING] {sym} - Method: {sizing_method}, "
                        f"Position: ${usd_to_spend:.2f}, Qty: {approx_qty:.6f}, "
                        f"Risk: ${sizing_result['risk_usd']:.2f} ({sizing_result['risk_pct']:.2f}%)"
                    )
                    
                else:
                    # BRACKET MODE: Not supported for shorts
                    print(f"[BRACKET-SKIP] {sym} - Brackets not supported for margin shorts, skipping")
                    continue
                
                # Validate position size
                if usd_to_spend <= 0.0:
                    print(f"[SKIP] {sym} SHORT qty=0 ({why})")
                    continue
                
                # Apply global position cap (backup safety check)
                max_position_env = env_float("MAX_POSITION_USD", 10.0)
                if usd_to_spend > max_position_env:
                    print(f"[POSITION-CAP] {sym} SHORT - Capping ${usd_to_spend:.2f} → ${max_position_env:.2f}")
                    usd_to_spend = max_position_env
                
                # ═══════════════════════════════════════════════════════════════
                # RISK MANAGEMENT CHECKS (MANDATORY - ALL MUST PASS)
                # ═══════════════════════════════════════════════════════════════
                
                # 1. DAILY TRADE LIMITS: Check global daily limit (applies to both paper/live)
                try:
                    mode_str = get_mode_str()
                    allowed, limit_reason = can_open_new_trade(sym, mode_str)
                    if not allowed:
                        print(f"🚫 [DAILY-LIMIT-BLOCK] {sym} SHORT - {limit_reason}")
                        log_evaluation(
                            symbol=sym,
                            decision="NO_TRADE",
                            reason=f"Daily limit reached: {limit_reason}",
                            trading_mode=trading_mode,
                            price=price,
                            rsi=rsi,
                            atr=atr,
                            regime=regime.value if regime else None,
                            adx=adx,
                            current_position_qty=pos_qty
                        )
                        continue
                except Exception as e:
                    print(f"[DAILY-LIMIT-CHECK-ERR] {sym}: {e}")
                
                # 2. FEE-AWARE EDGE CHECK: Verify edge > round-trip fees
                try:
                    # BYPASS CHECK: If BYPASS_FEE_BLOCK=1, skip all fee validation
                    bypass_fee_check = env_str("BYPASS_FEE_BLOCK", "0") == "1"
                    
                    if bypass_fee_check:
                        print(f"🔓 [FEE-BYPASS] {sym} SHORT - BYPASS_FEE_BLOCK=1, skipping fee validation")
                    else:
                        min_edge_required = get_minimum_edge_pct(safety_margin=0.08)  # 0.08% safety buffer (aggressive mode)
                        taker_fee_pct = get_taker_fee(sym) * 100  # Convert to percentage
                        
                        # edge_pct is calculated earlier as: ((price - sma20) / sma20) * 100
                        # For SHORT: Use absolute value since we profit on downward movement
                        if edge_pct is not None:
                            edge_abs = abs(edge_pct)
                            if edge_abs < min_edge_required:
                                print(f"🚫 [FEE-BLOCK] {sym} SHORT - Edge {edge_abs:.2f}% < required {min_edge_required:.2f}%")
                                print(f"   Taker fee: {taker_fee_pct:.4f}%, Round-trip: {taker_fee_pct*2:.4f}%, Required with buffer: {min_edge_required:.2f}%")
                                print(f"   SKIPPING: SHORT trade cannot profitably cover fees")
                                
                                # Log fee block event
                                if TELEMETRY_ENABLED and log_decision:
                                    log_decision(sym, "no_trade", f"fee_block_short_edge_{edge_abs:.2f}%", price, edge_abs, atr, pos_qty, eq_usd, executed=False)
                                
                                log_evaluation(
                                    symbol=sym,
                                    decision="NO_TRADE",
                                    reason=f"FEE_BLOCK: Short edge {edge_abs:.2f}% < min {min_edge_required:.2f}%",
                                    trading_mode=trading_mode,
                                    price=price,
                                    rsi=rsi,
                                    atr=atr,
                                    regime=regime.value if regime else None,
                                    adx=adx,
                                    current_position_qty=pos_qty
                                )
                                continue
                            else:
                                print(f"✅ [FEE-CHECK] {sym} SHORT - Edge {edge_abs:.2f}% > required {min_edge_required:.2f}% (taker: {taker_fee_pct:.4f}%)")
                        else:
                            print(f"⚠️  [FEE-CHECK] {sym} SHORT - No edge_pct available, proceeding with caution")
                    
                except Exception as fee_err:
                    print(f"[FEE-CHECK-ERR] {sym} SHORT: {fee_err} - proceeding without fee validation (RISKY)")
                
                # 3. EXECUTE SHORT ENTRY
                print(f"[MARKET-MODE] {sym} - Executing margin SHORT (no brackets)")
                
                from execution_manager import execute_market_short_entry
                
                result = execute_market_short_entry(
                    symbol=sym,
                    size_usd=usd_to_spend,
                    source="autopilot",
                    atr=atr,
                    reason=why
                )
                
                if result.success:
                    print(f"✅ [SHORT-ENTRY-SUCCESS] {sym} - {result}")
                    
                    # Record trade for daily limits
                    try:
                        record_trade_opened(sym, mode_str)
                        print(f"[DAILY-LIMIT] {sym} - Short trade recorded (mode: {mode_str})")
                    except Exception as record_err:
                        print(f"[DAILY-LIMIT-RECORD-ERR] {sym}: {record_err}")
                        
                    # MENTAL SL/TP: Store SHORT position with INVERTED exit levels FIRST
                    # For shorts: SL is ABOVE entry (stop loss on upside), TP is BELOW entry (take profit on downside)
                    position = None
                    try:
                        position = add_position(
                            symbol=sym,
                            entry_price=result.fill_price,
                            quantity=result.filled_qty,
                            atr=atr if atr and atr > 0 else result.fill_price * 0.02,
                            atr_sl_multiplier=2.0,
                            atr_tp_multiplier=3.0,
                            source="autopilot",
                            is_short=True  # CRITICAL: Mark as short position for inverted SL/TP
                        )
                        print(f"📍 [SHORT-POSITION-STORED] {sym} - SL=${position.stop_loss_price:.4f} (above entry), TP=${position.take_profit_price:.4f} (below entry)")
                    except Exception as pos_err:
                        print(f"[POSITION-TRACKER-ERR] {sym}: {pos_err} - short position not tracked!")
                    
                    # Log to telemetry and send Discord notification (now with SL/TP)
                    if TELEMETRY_ENABLED and log_decision:
                        try:
                            log_decision(sym, "sell_short", why, result.fill_price, edge_pct, atr, pos_qty, eq_usd, executed=True)
                            if notify_trade:
                                sl_price = position.stop_loss_price if position else None
                                tp_price = position.take_profit_price if position else None
                                notify_trade(sym, "sell_short", result.filled_qty, result.fill_price, why, sl_price, tp_price)
                        except Exception as log_err:
                            print(f"[TELEMETRY-ERR] {log_err}")
                    
                    trade_log.append({"symbol": sym, "action": "market_short", "usd": float(f"{usd_to_spend:.2f}")})
                    print(f"🎯 [MARKET-SHORT-COMPLETE] {sym} - Short position opened, monitoring for exit signals")
                    
                    continue
                else:
                    print(f"❌ [SHORT-ENTRY-FAILED] {sym} - {result.error}")
                    continue
            
            # ═══════════════════════════════════════════════════════════════
            # SELL_ALL EXECUTION PATH (position exit)
            # ═══════════════════════════════════════════════════════════════
            elif exec_action == "sell_all" and pos_qty > 0:
                print(f"[SELL] {sym} all @ mkt | {why}")
                
                # Calculate profit before executing
                sell_value = pos_qty * price if price else 0.0
                
                # ═══════════════════════════════════════════════════════════════
                # EXECUTION MODE ROUTING: Market-only vs. Command-based exit
                # ═══════════════════════════════════════════════════════════════
                
                if not config.use_brackets:
                    # MARKET_ONLY MODE: Use execution_manager for clean exit
                    print(f"[MARKET-MODE] {sym} - Executing market SELL (exit position)")
                    
                    result = execute_market_exit(
                        symbol=sym,
                        quantity=None,  # Auto-detect from position
                        full_position=True,
                        source="autopilot_exit",
                        reason=why
                    )
                    
                    if result.success:
                        print(f"✅ [MARKET-EXIT-SUCCESS] {sym} - {result}")
                        result_str = f"[MARKET_EXIT] Position closed: {result.filled_qty:.6f} @ ${result.fill_price:.4f}"
                    else:
                        print(f"❌ [MARKET-EXIT-FAILED] {sym} - {result.error}")
                        result_str = f"[MARKET_EXIT_ERROR] {result.error}"
                
                else:
                    # BRACKET MODE: Use command-based execution (legacy)
                    # Execute trade (or simulate if backtest mode)
                    if BACKTEST_MODE_ENABLED and get_backtest:
                        backtest = get_backtest()
                        safe_price = price if price else 0.0
                        result = backtest.execute_trade(sym, "sell", safe_price, sell_value, why)
                        result_str = f"[BACKTEST] Sell executed - no real order"
                    else:
                        result = run_command(f"sell all {sym}")
                        result_str = str(result)
                
                print(result_str)
                
                # Log decision and trade to learning database
                if TELEMETRY_ENABLED and log_decision and log_trade:
                    try:
                        log_decision(sym, "sell_all", why, price, edge_pct, atr, pos_qty, eq_now, executed=True)
                        log_trade(sym, "sell", "market_sell", pos_qty, price, None, None, why, "autopilot")
                        if notify_trade:
                            notify_trade(sym, "sell", pos_qty, price or 0.0, why)
                    except Exception as log_err:
                        print(f"[TELEMETRY-ERR] {log_err}")
                
                # Record to profit target system (estimate profit as sell_value - avg_cost)
                # Note: This is approximate without tracking cost basis
                if PROFIT_TARGET_ENABLED and get_target_system:
                    try:
                        target_sys = get_target_system()
                        # We don't have exact cost basis here, so we'll track via update_equity instead
                        target_sys.record_trade(0.0)
                    except Exception as e:
                        print(f"[TARGET-RECORD-ERR] {e}")
                
                alert(f"ℹ️ Exited {sym} (reason: {why})")
                set_cooldown(sym)
                trade_log.append({"symbol": sym, "action": "sell_all", "qty": float(f"{pos_qty:.8f}")})
                continue

            else:
                print(f"[HOLD] {sym} | {why}")
                
                # Log hold decision to learning database
                if TELEMETRY_ENABLED and log_decision:
                    try:
                        log_decision(sym, "hold", why, price, edge_pct, atr, pos_qty, eq_now, executed=False)
                    except Exception as log_err:
                        print(f"[TELEMETRY-ERR] {log_err}")

        except Exception as e:
            print(f"[ERR] {sym} -> {e}")
            
            # Log error to learning database
            if TELEMETRY_ENABLED and log_error:
                try:
                    error_action = action if 'action' in dir() else "unknown"
                    log_error("trading_loop_error", str(e), sym, {"action": error_action})
                except Exception:
                    pass

    # SAFETY MONITOR - Check for naked positions after all trading
    try:
        from safety_monitor import check_naked_positions
        safety_result = check_naked_positions(ex)
        
        if safety_result.get("naked_found", 0) > 0:
            print(f"🚨 [SAFETY-MONITOR] Found {safety_result['naked_found']} naked position(s)")
            print(f"    Emergency actions taken: {len(safety_result.get('emergency_actions', []))}")
            for action in safety_result.get('emergency_actions', []):
                print(f"    - {action}")
        
        if safety_result.get("errors"):
            print(f"⚠️ [SAFETY-MONITOR] Errors: {safety_result['errors']}")
    
    except Exception as safety_err:
        print(f"[SAFETY-MONITOR-ERR] Safety check failed: {safety_err}")
        import traceback
        traceback.print_exc()

    # open orders preview
    try:
        open_txt = run_command("open")
    except Exception as e:
        open_txt = f"[open_error] {e}"

    # per-symbol mini state
    per: List[Dict[str, Any]] = []
    for sym in symbols:
        try:
            _, closes2, price2 = ohlcv_1m(ex, sym, limit=3)
            pos_qty2, _usd2 = position_qty(ex, sym)
            per.append({
                "symbol": sym,
                "price": price2,
                "pos_qty": pos_qty2,
                "pos_value": round((pos_qty2 or 0) * (price2 or 0), 2),
            })
        except Exception as e:
            per.append({"symbol": sym, "error": str(e)})

    # optional pro metrics
    try:
        pm = pro_metrics(ex, symbols[0]) if symbols else {}
    except Exception as e:
        pm = {"error": str(e)}

    # write state.json (with heartbeat + running flag)
    # CRITICAL: Merge candle_tracking from loop (updated per-symbol) with runtime state
    now = time.time()
    state["autopilot_running"] = True
    state["last_loop_at"] = now
    state["validate_mode"] = env_str("KRAKEN_VALIDATE_ONLY", "1")
    state["equity_now_usd"] = round(eq_now, 2)
    state["equity_day_start_usd"] = round(_DAY_START_EQUITY or eq_now, 2)
    state["equity_change_usd"] = round((eq_now - (_DAY_START_EQUITY or eq_now)), 2)
    state["paused"] = paused()
    state["cooldowns"] = _COOLDOWN_UNTIL
    state["symbols"] = per
    state["open_orders_preview"] = (open_txt or "")[:2000]
    state["pro_metrics"] = pm
    state["last_actions"] = trade_log
    state["ts"] = now
    state["state_path"] = str(STATE_PATH)
    # candle_tracking already updated in state dict during loop - preserve it!
    write_state(state)
    
    # Log performance snapshot to learning database
    if TELEMETRY_ENABLED and log_performance:
        try:
            log_performance(
                equity_usd=eq_now,
                equity_change_usd=eq_now - (_DAY_START_EQUITY or eq_now),
                open_positions=per,
                symbols_traded=symbols,
                metadata={"paused": paused(), "validate_mode": env_str("KRAKEN_VALIDATE_ONLY", "1")}
            )
        except Exception as log_err:
            print(f"[PERF-LOG-ERR] {log_err}")

    # write diagnostic.json
    try:
        diag = collect_diagnostics(trade_log)
        Path("diagnostic.json").write_text(json.dumps(diag), encoding="utf-8")
    except Exception as e:
        print("[DIAG-WRITE-ERR]", e)

# -------------------------------------------------------------------
# main loop
# -------------------------------------------------------------------
def run_forever() -> None:
    if env_str("AUTONOMOUS", "0") != "1":
        print("AUTONOMOUS=0 — idle (set AUTONOMOUS=1 in .env to start)", flush=True)
        write_state({
            "autopilot_running": False,
            "last_loop_at": time.time(),
            "note": "autopilot idle",
            "equity_now_usd": 0,
            "equity_change_usd": 0,
            "state_path": str(STATE_PATH),
        })
        return

    ex = mk_ex()
    
    # Send Discord startup test ping if enabled
    if TELEMETRY_ENABLED and notify_trade:
        try:
            from discord_notifications import send_startup_test_ping
            send_startup_test_ping()
        except Exception as e:
            print(f"[DISCORD-TEST] Failed to send startup ping: {e}")
    
    # Log version/config to Data Vault on startup
    if DATA_VAULT_ENABLED and data_vault_log_version:
        try:
            cfg = get_config()
            mode_str = "paper" if cfg.paper_mode else "live"
            data_vault_log_version({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "zin_version": get_zin_version(),
                "config": get_config_for_logging(),
                "comment": f"Autopilot startup in {mode_str.upper()} mode"
            })
            print(f"[DATA-VAULT] Version {get_zin_version()} logged to data vault")
        except Exception as e:
            print(f"[DATA-VAULT] Failed to log version: {e}")
    
    # Check for daily/weekly summaries
    if TELEMETRY_ENABLED and check_summaries:
        try:
            check_summaries()
        except Exception as e:
            print(f"[SUMMARY-CHECK-ERR] {e}")
    
    # Get trading symbols - either from Crypto Universe Scanner or static list
    if CRYPTO_UNIVERSE_ENABLED and CryptoUniverseScanner:
        try:
            max_assets = env_int("MAX_UNIVERSE_ASSETS", 20)
            min_volume = env_float("MIN_VOLUME_24H", 100000.0)
            print(f"[UNIVERSE] Initializing Crypto Universe Scanner (max={max_assets}, min_vol=${min_volume:,.0f})...")
            scanner = CryptoUniverseScanner(
                exchange=ex,
                quote_currency="USD",
                max_assets=max_assets,
                min_volume_24h=min_volume
            )
            symbols = scanner.get_tradable_symbols()
            if symbols:
                print(f"[UNIVERSE] ✅ Using {len(symbols)} scanned symbols: {', '.join(symbols[:10])}{' ...' if len(symbols) > 10 else ''}")
            else:
                print("[UNIVERSE] ⚠️ Scanner returned empty list, falling back to static SYMBOLS from .env")
                symbols = [s.strip().upper() for s in env_str("SYMBOLS", "ZEC/USD").split(",") if s.strip()]
        except Exception as e:
            print(f"[UNIVERSE-ERR] Scanner failed: {e}")
            print("[UNIVERSE] ⚠️ Falling back to static SYMBOLS from .env")
            symbols = [s.strip().upper() for s in env_str("SYMBOLS", "ZEC/USD").split(",") if s.strip()]
    else:
        # CRYPTO_UNIVERSE_ENABLED=0 or scanner not available - use static list
        symbols = [s.strip().upper() for s in env_str("SYMBOLS", "ZEC/USD").split(",") if s.strip()]
        print(f"[UNIVERSE] Using static symbol list from SYMBOLS env var: {', '.join(symbols)}")
    
    iv = env_int("TRADE_INTERVAL_SEC", 60)  # 60s for fresh data, still 99% under Kraken limits
    print(f"[AUTOPILOT] Running on {len(symbols)} symbol(s) every {iv}s (mode={get_mode_str().upper()}, validate={env_str('KRAKEN_VALIDATE_ONLY','1')})", flush=True)

    # initialize day start equity
    try:
        _bal: Dict[str, Any] = ex.fetch_balance()
        eq0 = account_equity_usd(_bal)
    except Exception:
        eq0 = 0.0
    global _DAY_START_EQUITY
    _DAY_START_EQUITY = eq0
    
    # Reconciliation tracking - run every 60s
    last_reconciliation_time = 0
    reconciliation_interval = 60  # seconds

    # Track loop iterations for heartbeat
    loop_count = 0
    
    while True:
        loop_count += 1
        loop_once(ex, symbols)
        
        # Run reconciliation cycle for TP/SL fill monitoring
        current_time = time.time()
        if current_time - last_reconciliation_time >= reconciliation_interval:
            try:
                run_reconciliation_cycle()
                last_reconciliation_time = current_time
            except Exception as e:
                reconciliation_logger.error(f"[RECONCILE] Error in cycle: {e}")
        
        # Periodic snapshot (~3 per day in live mode)
        if SNAPSHOT_ENABLED and maybe_take_snapshot:
            try:
                maybe_take_snapshot()
            except Exception as e:
                print(f"[SNAPSHOT] Error taking snapshot: {e}")
        
        # Write heartbeat after each successful loop (for Reserved VM health monitoring)
        try:
            _write_heartbeat(loop_count, len(symbols), iv)
        except Exception as e:
            print(f"[HEARTBEAT] Warning: {e}")
        
        time.sleep(iv)

if __name__ == "__main__":
    try:
        from pathlib import Path
        
        # Ensure data directories exist
        Path("data").mkdir(exist_ok=True)
        Path("data/meta").mkdir(exist_ok=True)
        
        # =====================================================================
        # SAFETY CHECK: Instance Guard (Singleton Protection)
        # =====================================================================
        # Prevents multiple live ZIN instances from trading simultaneously.
        # If another active instance is detected, this process will flip to
        # validate-only mode instead of trading live.
        
        from instance_guard import (
            acquire_instance_lock,
            should_allow_live_trading,
            is_dev_environment
        )
        
        validate_mode = os.getenv("KRAKEN_VALIDATE_ONLY", "0") == "1"
        mode = get_mode_str()
        is_live_mode = mode.lower() == "live" and not validate_mode
        
        if is_live_mode:
            # Check 1: Dev environment safety gate
            allow_live, reason = should_allow_live_trading()
            print(f"[SAFETY] {reason}", flush=True)
            
            if not allow_live:
                print("=" * 60, flush=True)
                print("[SAFETY] ⚠️  FORCING VALIDATE-ONLY MODE FOR SAFETY", flush=True)
                print("[SAFETY] Dev environment live trading is disabled by default.", flush=True)
                print("[SAFETY] Set ALLOW_DEV_LIVE=1 to enable (not recommended).", flush=True)
                print("=" * 60, flush=True)
                os.environ["KRAKEN_VALIDATE_ONLY"] = "1"
                validate_mode = True
                is_live_mode = False
            
            # Check 2: Instance guard (only if still live mode)
            if is_live_mode:
                print("[INSTANCE-GUARD] Checking for other active ZIN instances...", flush=True)
                
                if not acquire_instance_lock(mode="live"):
                    print("=" * 60, flush=True)
                    print("[INSTANCE-GUARD] ⚠️  ANOTHER ACTIVE INSTANCE DETECTED!", flush=True)
                    print("[INSTANCE-GUARD] Forcing this process to validate-only mode.", flush=True)
                    print("[INSTANCE-GUARD] Only ONE live trading instance is allowed.", flush=True)
                    print("=" * 60, flush=True)
                    os.environ["KRAKEN_VALIDATE_ONLY"] = "1"
                    validate_mode = True
                    is_live_mode = False
                else:
                    print("[INSTANCE-GUARD] ✅ Lock acquired - this is the primary live instance", flush=True)
        else:
            print(f"[INSTANCE-GUARD] Skipping lock (validate_only={validate_mode}, mode={mode})", flush=True)
        
        # CRITICAL: Validate Kraken API credentials and connectivity before trading
        print("[STARTUP] Running Kraken health check...", flush=True)
        from kraken_health import kraken_health_check, get_health_summary
        
        health_results = kraken_health_check()
        print(get_health_summary(health_results), flush=True)
        
        # Fail-fast if credentials or connectivity issues detected
        if not all(r.ok for r in health_results.values()):
            print("\n" + "=" * 60, flush=True)
            print("🔴 CRITICAL: Kraken API health check FAILED", flush=True)
            print("=" * 60, flush=True)
            print("Live trading and live data access are DISABLED.", flush=True)
            print("Fix the issues above before running autopilot in LIVE mode.", flush=True)
            print("=" * 60 + "\n", flush=True)
            
            # Check if we're in validate/paper mode - if so, we can continue
            validate_mode = os.getenv("KRAKEN_VALIDATE_ONLY", "0") == "1"
            if not validate_mode:
                print("ERROR: Cannot run autopilot in LIVE mode without valid Kraken credentials.", flush=True)
                sys.exit(1)
            else:
                print("⚠️  Continuing in PAPER mode (validate mode enabled)", flush=True)
        else:
            print("✅ Kraken API health check PASSED - ready for live trading\n", flush=True)
        
        # Final mode after all safety checks
        final_mode = "VALIDATE-ONLY (SAFE)" if os.getenv("KRAKEN_VALIDATE_ONLY", "0") == "1" else "LIVE"
        print(f"[FINAL MODE] {final_mode}", flush=True)
        
        # =====================================================================
        # CRITICAL: Reload exchange manager AFTER safety checks finalize mode
        # =====================================================================
        # The ExchangeManager singleton is created at import time, before safety
        # checks run. Now that KRAKEN_VALIDATE_ONLY is finalized, reload so the
        # exchange picks up the correct paper/live mode.
        from exchange_manager import reload_exchange_config, is_paper_mode
        reload_exchange_config()
        
        # Log the actual exchange state for debugging
        is_deployed = os.getenv("REPL_DEPLOYMENT", "") == "1"
        deploy_env = "reserved_vm" if is_deployed else "dev"
        exchange_type = "PaperSimulator" if is_paper_mode() else "KrakenLive"
        print(f"[STARTUP] env={deploy_env} | mode={'validate-only' if os.getenv('KRAKEN_VALIDATE_ONLY', '0') == '1' else 'live'} | exchange={exchange_type}", flush=True)
        
        # Sanity check: ensure env var and exchange state match
        env_validate = os.getenv("KRAKEN_VALIDATE_ONLY", "0") == "1"
        if env_validate != is_paper_mode():
            print(f"[WARNING] Mode mismatch! KRAKEN_VALIDATE_ONLY={env_validate} but is_paper_mode()={is_paper_mode()}", flush=True)
        
        print("[MAIN] entering run_forever()", flush=True)
        run_forever()
    except Exception as e:
        import traceback
        print("[FATAL]", e, flush=True)
        traceback.print_exc()
        sys.exit(1)
