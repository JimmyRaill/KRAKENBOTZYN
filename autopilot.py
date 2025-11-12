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

# Self-learning imports
TELEMETRY_ENABLED = False
log_trade = log_decision = log_performance = log_error = None
try:
    from telemetry_db import log_trade, log_decision, log_performance, log_error
    from time_context import get_time_info, get_prompt_context
    TELEMETRY_ENABLED = True
except ImportError:
    print("[WARNING] Telemetry modules not found - learning features disabled")

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
CRYPTO_UNIVERSE_ENABLED = False
PROFIT_TARGET_ENABLED = False
MULTI_TIMEFRAME_ENABLED = False
API_WATCHDOG_ENABLED = False
BACKTEST_MODE_ENABLED = False

CryptoUniverseScanner = get_target_system = MultiTimeframeAnalyzer = None
get_watchdog = get_backtest = fetch_multi_timeframe_data = None

try:
    from crypto_universe import CryptoUniverseScanner, get_dynamic_universe
    if CryptoUniverseScanner:
        CRYPTO_UNIVERSE_ENABLED = os.getenv("ENABLE_CRYPTO_UNIVERSE", "0") == "1"
        if CRYPTO_UNIVERSE_ENABLED:
            print("[INIT] ✅ Crypto Universe Scanner enabled (200+ pairs)")
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
# exchange + alerts
# -------------------------------------------------------------------
def mk_ex():
    validate = env_str("KRAKEN_VALIDATE_ONLY", "1") in ("1", "true", "True")
    cfg = {
        "apiKey": env_str("KRAKEN_API_KEY", ""),
        "secret": env_str("KRAKEN_API_SECRET", ""),
        "options": {"validate": validate},
    }
    ex = ccxt.kraken(cfg)  # type: ignore[arg-type]
    ex.load_markets()
    return ex

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
    Place bracket orders for a position. Returns True if successful, False if failed.
    CRITICAL SAFETY: If brackets fail, caller MUST handle the unprotected position.
    """
    if not (avg_fill and qty and atr):
        print(f"[BRACKET-SKIP] {symbol} - missing parameters")
        return False
    
    tp_atr = env_float("TAKE_PROFIT_ATR", 1.2)
    sl_atr = env_float("STOP_LOSS_ATR", 0.6)
    tp_px = round(avg_fill + tp_atr * atr, 2)
    sl_px = round(avg_fill - sl_atr * atr, 2)
    
    # Check minimum order size BEFORE attempting brackets
    try:
        market = ex.market(symbol) or {}
        limits = market.get("limits") or {}
        min_amt = float((limits.get("amount") or {}).get("min", 0) or 0)
        min_cost = float((limits.get("cost") or {}).get("min", 0) or 0)
        
        # Check if quantity meets minimums
        if min_amt > 0 and qty < min_amt:
            print(f"[BRACKET-SKIP] {symbol} qty {qty:.6f} < min {min_amt:.6f} - position UNPROTECTED")
            return False
        
        if min_cost > 0 and qty * avg_fill < min_cost:
            print(f"[BRACKET-SKIP] {symbol} cost ${qty * avg_fill:.2f} < min ${min_cost:.2f} - position UNPROTECTED")
            return False
    except Exception as e:
        print(f"[BRACKET-CHECK-ERR] {symbol}: {e}")
        return False
    
    # Attempt to place brackets
    from commands import handle as run_command  # local import
    print(f"[BRACKETS] {symbol} TP={tp_px} SL={sl_px} qty={qty:.6f}")
    
    try:
        result = run_command(f"bracket {symbol} {qty:.6f} tp {tp_px} sl {sl_px}")
        result_str = str(result).lower()
        
        # Robust error detection - treat ANY error/warning as failure for safety
        error_indicators = ["error", "err", "fail", "invalid", "reject", "denied", "insufficient"]
        if any(indicator in result_str for indicator in error_indicators):
            print(f"[BRACKET-FAILED] {symbol} - {result}")
            return False
        
        # Also check for success indicators - if missing, assume failure
        success_indicators = ["ok", "success", "placed", "created"]
        if not any(indicator in result_str for indicator in success_indicators):
            print(f"[BRACKET-UNCERTAIN] {symbol} - no success confirmation, treating as failure: {result}")
            return False
        
        print(result)
        return True
    except Exception as e:
        print(f"[BRACKET-ERR] {symbol}: {e}")
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

    # per-symbol logic + trade log
    trade_log: List[Dict[str, Any]] = []
    for sym in symbols:
        action = "unknown"  # Initialize to avoid unbound variable in error handler
        try:
            if paused():
                print(f"[PAUSED] global pause active; skip {sym}")
                continue
            if cooldown_active(sym):
                print(f"[COOLDOWN] {sym} — skipping")
                continue

            ohlcv, closes, price = ohlcv_1m(ex, sym, limit=60)
            atr = compute_atr(ohlcv, period=14)
            pos_qty, usd_cash = position_qty(ex, sym)
            
            # MULTI-TIMEFRAME CONFIRMATION (if enabled)
            mtf_approved = True
            mtf_reason = ""
            if MULTI_TIMEFRAME_ENABLED and MultiTimeframeAnalyzer and fetch_multi_timeframe_data:
                try:
                    mtf_data = fetch_multi_timeframe_data(ex, sym)
                    analyzer = MultiTimeframeAnalyzer()
                    mtf_analysis = analyzer.analyze_all_timeframes(mtf_data)
                    
                    # Check if multi-timeframe supports the intended action
                    base_action, base_why = decide_action(price, closes, pos_qty)
                    if base_action == "buy":
                        if mtf_analysis["recommendation"] != "buy":
                            mtf_approved = False
                            mtf_reason = f"MTF: {mtf_analysis['consensus']} (alignment={mtf_analysis['alignment_score']:.0%})"
                    elif base_action == "sell_all":
                        if mtf_analysis["recommendation"] != "sell":
                            mtf_approved = False
                            mtf_reason = f"MTF: {mtf_analysis['consensus']} (alignment={mtf_analysis['alignment_score']:.0%})"
                except Exception as e:
                    print(f"[MTF-ERR] {sym}: {e}")
                    mtf_approved = True  # Don't block on MTF errors
            
            # Get action with MTF override if needed
            if not mtf_approved:
                action, why = "hold", mtf_reason
            else:
                action, why = decide_action(price, closes, pos_qty)
            
            # Calculate edge for logging
            sma20 = mean(closes[-20:]) if len(closes) >= 20 else None
            edge_pct = ((price - sma20) / sma20 * 100.0) if (price and sma20) else None

            if action == "buy" and price:
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
                    except Exception as log_err:
                        print(f"[TELEMETRY-ERR] {log_err}")
                
                # Record to profit target system
                if PROFIT_TARGET_ENABLED and get_target_system:
                    try:
                        target_sys = get_target_system()
                        target_sys.record_trade(0.0)  # Buy has no immediate profit
                    except Exception as e:
                        print(f"[TARGET-RECORD-ERR] {e}")
                
                trade_log.append({"symbol": sym, "action": "buy", "usd": float(f"{usd_to_spend:.2f}")})
                
                # CRITICAL SAFETY: Place brackets and handle failure
                # If brackets fail, IMMEDIATELY flatten the position
                if atr:
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
                            # Don't rely on string heuristics - check the actual balance
                            time.sleep(0.5)  # Allow settlement
                            
                            try:
                                verify_qty, _ = position_qty(ex, sym)
                                if verify_qty > 0.001:  # Still have position
                                    flatten_success = False
                                    print(f"🚨 [FLATTEN-VERIFY-FAILED] {sym} - Position still exists: {verify_qty}")
                                else:
                                    flatten_success = True
                                    print(f"✅ [FLATTEN-VERIFIED] {sym} - Position confirmed closed (qty: {verify_qty})")
                            except Exception as verify_err:
                                print(f"🚨 [FLATTEN-VERIFY-ERR] {sym}: {verify_err} - cannot confirm position closed")
                                flatten_success = False
                            
                            # Log critical safety event with verification result
                            if TELEMETRY_ENABLED and log_error:
                                try:
                                    log_error("bracket_failure_auto_flatten", 
                                             f"Brackets failed for {sym}, position {'closed' if flatten_success else 'NOT CLOSED'}", {
                                        "symbol": sym, "qty": approx_qty, "price": price,
                                        "flatten_verified_success": flatten_success,
                                        "emergency_sell_result": str(emergency_sell_result)[:200]
                                    })
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
                                    log_error("flatten_exception", f"Exception during emergency flatten of {sym}", {
                                        "symbol": sym, "exception": str(flatten_err)
                                    })
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
                                    log_error("critical_double_failure", 
                                             f"Both brackets AND emergency flatten failed for {sym} - TRADING PAUSED", {
                                        "symbol": sym, "qty": approx_qty, "price": price,
                                        "paused_until": _PAUSED_UNTIL
                                    })
                                except Exception:
                                    pass
                            
                            # Exit loop immediately to prevent more trades
                            break

            elif action == "sell_all" and pos_qty > 0:
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
    now = time.time()
    state: Dict[str, Any] = {
        "autopilot_running": True,
        "last_loop_at": now,
        "validate_mode": env_str("KRAKEN_VALIDATE_ONLY", "1"),
        "equity_now_usd": round(eq_now, 2),
        "equity_day_start_usd": round(_DAY_START_EQUITY or eq_now, 2),
        "equity_change_usd": round((eq_now - (_DAY_START_EQUITY or eq_now)), 2),
        "paused": paused(),
        "cooldowns": _COOLDOWN_UNTIL,
        "symbols": per,
        "open_orders_preview": (open_txt or "")[:2000],
        "pro_metrics": pm,
        "last_actions": trade_log,
        "ts": now,
        "state_path": str(STATE_PATH),
    }
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
    
    # Get trading symbols - either from Crypto Universe Scanner or static list
    if CRYPTO_UNIVERSE_ENABLED and CryptoUniverseScanner:
        try:
            print("[UNIVERSE] Initializing Crypto Universe Scanner...")
            scanner = CryptoUniverseScanner(
                exchange=ex,
                quote_currency="USD",
                max_assets=env_int("MAX_UNIVERSE_ASSETS", 20),
                min_volume_24h=env_float("MIN_VOLUME_24H", 10000.0)
            )
            symbols = scanner.get_tradable_symbols()
            if not symbols:
                print("[UNIVERSE] No symbols from scanner, falling back to static list")
                symbols = [s.strip().upper() for s in env_str("SYMBOLS", "ZEC/USD").split(",") if s.strip()]
        except Exception as e:
            print(f"[UNIVERSE-ERR] {e}, using static symbols")
            symbols = [s.strip().upper() for s in env_str("SYMBOLS", "ZEC/USD").split(",") if s.strip()]
    else:
        symbols = [s.strip().upper() for s in env_str("SYMBOLS", "ZEC/USD").split(",") if s.strip()]
    iv = env_int("TRADE_INTERVAL_SEC", 60)
    print(f"[AUTOPILOT] running on {symbols} every {iv}s (validate={env_str('KRAKEN_VALIDATE_ONLY','1')})", flush=True)

    # initialize day start equity
    try:
        _bal: Dict[str, Any] = ex.fetch_balance()
        eq0 = account_equity_usd(_bal)
    except Exception:
        eq0 = 0.0
    global _DAY_START_EQUITY
    _DAY_START_EQUITY = eq0

    while True:
        loop_once(ex, symbols)
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
