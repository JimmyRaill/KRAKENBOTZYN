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
from risk_manager import calculate_trade_risk, get_max_active_risk, PositionSnapshot
from trading_limits import can_open_new_trade, record_trade_opened, get_daily_limits

# Bracket order manager - MANDATORY for all trades
# Type hints for LSP
get_bracket_manager = None
BracketOrder = None
BracketConfig = None
BRACKET_MANAGER_ENABLED = False

try:
    from bracket_order_manager import get_bracket_manager, BracketOrder, BracketConfig
    BRACKET_MANAGER_ENABLED = True
    print("[INIT] ✅ Bracket Order Manager enabled - NO NAKED POSITIONS")
except ImportError as e:
    BRACKET_MANAGER_ENABLED = False
    print(f"[CRITICAL] Bracket Order Manager import failed: {e}")

# Self-learning imports
TELEMETRY_ENABLED = False
log_trade = log_decision = log_performance = log_error = None
notify_trade = check_summaries = None
try:
    from telemetry_db import log_trade, log_decision, log_performance, log_error
    from sms_notifications import notify_trade, check_summaries
    from time_context import get_time_info, get_prompt_context
    TELEMETRY_ENABLED = True
except ImportError:
    print("[WARNING] Telemetry modules not found - learning features disabled")

# EVALUATION LOGGING - Full transparency layer
from evaluation_log import log_evaluation

# RECONCILIATION SERVICE - TP/SL fill monitoring
from reconciliation_service import run_reconciliation_cycle

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
    try:
        # CRITICAL FIX: Handle None or empty balance responses
        if bal is None:
            return 0.0
        if not isinstance(bal, dict):
            return 0.0
        return float((bal.get("USD") or {}).get("total", 0.0) or 0.0)
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
                    indicators_5m=indicators_5m
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
            if regime and regime.value == 'TREND_DOWN' and pos_qty > 0:
                action = "sell_all"
                exec_action = "sell_all"  # Update exec_action too
                confidence_str = f"(confidence={trade_signal.confidence:.2f})" if trade_signal else ""
                why = f"TREND_DOWN regime - exit long position {confidence_str}"
                print(f"[REGIME-EXIT] {sym} - Forcing exit due to bearish regime")
            
            # Adjust action based on position
            if action == 'long' and pos_qty > 0:
                action = "hold"
                exec_action = "hold"  # Update exec_action too
                why = f"LONG signal but already in position ({pos_qty:.6f})"
            
            # Calculate edge for logging
            edge_pct = ((price - current_sma20) / current_sma20 * 100.0) if current_sma20 else None
            
            # Update candle tracking AFTER signal evaluation
            update_candle_tracking(state, sym, latest_ts, current_sma20, current_close)

            # EXECUTION ROUTING: Use exec_action (not raw action from strategy)
            if exec_action == "buy" and price:
                eq_full: Dict[str, Any] = ex.fetch_balance()
                eq_usd = account_equity_usd(eq_full)
                qty = qty_from_atr(eq_usd, atr, price)
                usd_to_spend = qty * price

                if usd_to_spend <= 0.0:
                    print(f"[SKIP] {sym} qty=0 ({why})")
                    continue

                usd_to_spend = min(usd_to_spend, env_float("MAX_POSITION_USD", 10.0))
                if usd_to_spend > usd_cash + 1e-6:
                    usd_to_spend = max(0.0, usd_cash)
                if usd_to_spend <= 0.0:
                    print(f"[SKIP] {sym} no cash ({why})")
                    continue

                approx_qty = usd_to_spend / price if price else 0.0
                
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
                
                # CRITICAL PRE-TRADE VALIDATION: Verify brackets can be placed BEFORE executing entry
                # This implements "NO NAKED POSITIONS" rule from spec
                if BRACKET_MANAGER_ENABLED and get_bracket_manager is not None:
                    try:
                        manager = get_bracket_manager()
                        test_bracket = manager.calculate_bracket_prices(
                            symbol=sym,
                            side="buy",
                            entry_price=price,
                            atr=atr
                        )
                        if test_bracket:
                            test_bracket.quantity = approx_qty
                            can_place, reason, _ = manager.validate_bracket_can_be_placed(
                                test_bracket, ex, allow_adjust=False
                            )
                            if not can_place:
                                print(f"🚨 [PRE-TRADE-BLOCK] {sym} - Cannot guarantee bracket placement: {reason}")
                                print(f"⚠️  SAFETY: Skipping trade to prevent naked position")
                                continue
                            else:
                                print(f"✅ [PRE-TRADE-OK] {sym} - Bracket validation passed")
                        else:
                            print(f"🚨 [PRE-TRADE-BLOCK] {sym} - Failed to calculate brackets")
                            continue
                    except Exception as e:
                        print(f"🚨 [PRE-TRADE-ERR] {sym} - Bracket validation failed: {e}")
                        continue
                
                print(f"[BUY] {sym} ~${usd_to_spend:.2f} (qty≈{approx_qty:.6f}) @ mkt | {why} | ATR={atr if atr else 0:.4f}")
                
                # Execute trade (or simulate if backtest mode)
                if BACKTEST_MODE_ENABLED and get_backtest:
                    backtest = get_backtest()
                    safe_price = price if price else 0.0
                    result = backtest.execute_trade(sym, "buy", safe_price, usd_to_spend, why)
                    result_str = f"[BACKTEST] Buy executed - no real order"
                else:
                    result = run_command(f"buy {usd_to_spend:.2f} usd {sym}")
                    result_str = str(result)
                
                print(result_str)
                
                # Log decision and trade to learning database
                if TELEMETRY_ENABLED and log_decision and log_trade:
                    try:
                        log_decision(sym, "buy", why, price, edge_pct, atr, pos_qty, eq_usd, executed=True)
                        log_trade(sym, "buy", "market_buy", approx_qty, price, usd_to_spend, None, why, "autopilot")
                        if notify_trade:
                            notify_trade(sym, "buy", approx_qty, price or 0.0, why)
                    except Exception as log_err:
                        print(f"[TELEMETRY-ERR] {log_err}")
                
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
                
                trade_log.append({"symbol": sym, "action": "buy", "usd": float(f"{usd_to_spend:.2f}")})
                
                # CRITICAL SAFETY: Place brackets ALWAYS - NO NAKED POSITIONS
                # Brackets are MANDATORY for every trade (uses fallback if no ATR)
                # If brackets fail, IMMEDIATELY flatten the position
                # Add small delay to prevent nonce issues
                time.sleep(0.5)
                
                brackets_placed = place_brackets(sym, price, approx_qty, atr, ex)
                if not brackets_placed:
                    print(f"🚨 [CRITICAL-SAFETY] {sym} BRACKETS FAILED - FLATTENING POSITION IMMEDIATELY!")
                    
                    # IMMEDIATE ACTION: Sell the entire position to prevent unprotected exposure
                    flatten_success = False
                    emergency_sell_result = None
                    
                    try:
                        time.sleep(0.3)  # Brief delay before emergency exit
                        emergency_sell = run_command(f"sell all {sym}")
                        emergency_sell_result = str(emergency_sell)
                        print(f"[EMERGENCY-FLATTEN] {sym} command executed: {emergency_sell}")
                        
                        # CRITICAL: ALWAYS verify by checking actual position (PRIMARY check)
                        # Poll with retries to handle async settlement
                        max_retries = 3
                        retry_delay = 0.7
                        
                        for attempt in range(max_retries):
                            time.sleep(retry_delay)  # Allow settlement
                            
                            try:
                                verify_qty, _ = position_qty(ex, sym)
                                if verify_qty <= 0.001:  # Position closed
                                    flatten_success = True
                                    print(f"✅ [FLATTEN-VERIFIED] {sym} - Position confirmed closed (qty: {verify_qty}, attempt {attempt+1}/{max_retries})")
                                    break
                                else:
                                    print(f"⏳ [FLATTEN-VERIFY-RETRY] {sym} - Position still exists: {verify_qty} (attempt {attempt+1}/{max_retries})")
                                    if attempt == max_retries - 1:
                                        # Final attempt failed
                                        flatten_success = False
                                        print(f"🚨 [FLATTEN-VERIFY-FAILED] {sym} - Position still exists after {max_retries} attempts: {verify_qty}")
                            except Exception as verify_err:
                                print(f"🚨 [FLATTEN-VERIFY-ERR] {sym} attempt {attempt+1}/{max_retries}: {verify_err}")
                                if attempt == max_retries - 1:
                                    print(f"🚨 [FLATTEN-VERIFY-FAILED] {sym} - Cannot confirm position closed after {max_retries} attempts")
                                    flatten_success = False
                        
                        # Log critical safety event with verification result
                        if TELEMETRY_ENABLED and log_error:
                            try:
                                log_error(
                                    error_type="bracket_failure_auto_flatten",
                                    description=f"Brackets failed for {sym}, position {'closed' if flatten_success else 'NOT CLOSED'}",
                                    symbol=sym,
                                    context={
                                        "qty": approx_qty, "price": price,
                                        "flatten_verified_success": flatten_success,
                                        "emergency_sell_result": str(emergency_sell_result)[:200]
                                    }
                                )
                            except Exception:
                                pass
                        
                        # Only mark as safe exit if flatten actually succeeded AND verified
                        if flatten_success and trade_log and trade_log[-1].get("symbol") == sym:
                            trade_log[-1]["action"] = "buy_then_emergency_exit"
                            trade_log[-1]["note"] = "bracket_failure_auto_flattened_and_verified"
                    except Exception as flatten_err:
                        flatten_success = False
                        print(f"🚨🚨🚨 [FLATTEN-EXCEPTION] {sym}: {flatten_err}")
                        
                        # RESTORE: Alert and log exception path
                        alert(f"🚨 FLATTEN EXCEPTION: {sym} - {flatten_err}")
                        if TELEMETRY_ENABLED and log_error:
                            try:
                                log_error(
                                    error_type="flatten_exception",
                                    description=f"Exception during emergency flatten of {sym}",
                                    symbol=sym,
                                    context={"exception": str(flatten_err)}
                                )
                            except Exception:
                                pass
                    
                    # CRITICAL: If flatten failed (either via error check or exception), PAUSE TRADING
                    if not flatten_success:
                        print(f"🚨🚨🚨 [CRITICAL-SAFETY-FAILURE] {sym} POSITION IS UNPROTECTED!")
                        print(f"⚠️  BRACKETS FAILED + EMERGENCY FLATTEN FAILED")
                        print(f"⚠️  PAUSING ALL TRADING FOR SAFETY")
                        
                        # EMERGENCY: Trigger global pause to prevent further exposure
                        global _PAUSED_UNTIL
                        _PAUSED_UNTIL = time.time() + (6 * 60 * 60)  # Pause for 6 hours
                        
                        # Alert operator
                        alert(f"🚨 CRITICAL SAFETY FAILURE: {sym} position unprotected! Trading paused for 6h. MANUAL INTERVENTION REQUIRED!")
                        
                        # Log critical double failure
                        if TELEMETRY_ENABLED and log_error:
                            try:
                                log_error(
                                    error_type="critical_double_failure",
                                    description=f"Both brackets AND emergency flatten failed for {sym} - TRADING PAUSED",
                                    symbol=sym,
                                    context={
                                        "qty": approx_qty, "price": price,
                                        "paused_until": _PAUSED_UNTIL
                                    }
                                )
                            except Exception:
                                pass
                        
                        # Exit loop immediately to prevent more trades
                        break

            elif exec_action == "sell_all" and pos_qty > 0:
                print(f"[SELL] {sym} all @ mkt | {why}")
                
                # Calculate profit before executing
                sell_value = pos_qty * price if price else 0.0
                
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
    
    # Send SMS startup test ping if enabled
    if TELEMETRY_ENABLED and notify_trade:
        try:
            from sms_notifications import send_startup_test_ping
            send_startup_test_ping()
        except Exception as e:
            print(f"[SMS-TEST] Failed to send startup ping: {e}")
    
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

    while True:
        loop_once(ex, symbols)
        
        # Run reconciliation cycle for TP/SL fill monitoring
        current_time = time.time()
        if current_time - last_reconciliation_time >= reconciliation_interval:
            try:
                run_reconciliation_cycle()
                last_reconciliation_time = current_time
            except Exception as e:
                logger.error(f"[RECONCILE] Error in cycle: {e}")
        
        time.sleep(iv)

if __name__ == "__main__":
    try:
        print("[MAIN] entering run_forever()", flush=True)
        run_forever()
    except Exception as e:
        import traceback
        print("[FATAL]", e, flush=True)
        traceback.print_exc()
        sys.exit(1)
