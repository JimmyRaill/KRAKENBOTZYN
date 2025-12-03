"""
Snapshot Builder Module - Creates periodic state snapshots for Zin Trading Bot

This module creates ~3 snapshots per day containing:
- Account/equity state
- Risk/config state  
- Open positions with mental SL/TP
- Performance summary
- System health

Snapshots are lightweight state dumps for future self-analysis and strategy evolution.
"""

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from pathlib import Path

SNAPSHOTS_DIR = Path("data/snapshots")

_last_snapshot_time: Optional[float] = None
_snapshots_today_count: int = 0
_snapshots_today_date: Optional[str] = None

SNAPSHOT_INTERVAL_HOURS = 8
MAX_SNAPSHOTS_PER_DAY = 3


def _get_utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


def _get_date_str() -> str:
    """Get current date as YYYY-MM-DD string."""
    return _get_utc_now().strftime("%Y-%m-%d")


def _generate_snapshot_id() -> str:
    """Generate unique snapshot ID."""
    timestamp = _get_utc_now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{timestamp}_{suffix}"


def _get_account_state() -> Dict[str, Any]:
    """
    Get account/equity state from account_state module.
    
    Returns dict with:
    - total_equity_usd
    - cash_balance_usd
    - balances
    - realized_pnl_usd (if available)
    - unrealized_pnl_usd
    - unrealized_pnl_pct
    """
    try:
        from account_state import get_portfolio_snapshot, get_mode_str
        
        portfolio = get_portfolio_snapshot()
        balances = portfolio.get('balances', {})
        total_equity = portfolio.get('total_equity_usd', 0)
        
        usd_balance = 0
        simplified_balances = {}
        
        for currency, bal_data in balances.items():
            if isinstance(bal_data, dict):
                amount = bal_data.get('total', 0)
                if amount > 0.00001:
                    simplified_balances[currency] = round(amount, 8)
                if currency in ['USD', 'ZUSD']:
                    usd_balance = bal_data.get('free', 0)
            else:
                if bal_data > 0.00001:
                    simplified_balances[currency] = round(bal_data, 8)
        
        unrealized_pnl = 0.0
        try:
            from position_tracker import get_all_positions
            from exchange_manager import get_exchange
            
            positions = get_all_positions()
            exchange = get_exchange()
            
            for symbol, pos in positions.items():
                try:
                    ticker = exchange.fetch_ticker(symbol)
                    current_price = ticker.get('last', pos.entry_price)
                    qty = pos.quantity if hasattr(pos, 'quantity') else 0
                    if pos.is_short:
                        pnl = (pos.entry_price - current_price) * qty
                    else:
                        pnl = (current_price - pos.entry_price) * qty
                    unrealized_pnl += pnl
                except Exception:
                    pass
        except Exception:
            pass
        
        unrealized_pnl_pct = (unrealized_pnl / total_equity * 100) if total_equity > 0 else 0.0
        
        return {
            "total_equity_usd": round(total_equity, 2),
            "cash_balance_usd": round(usd_balance, 2),
            "balances": simplified_balances,
            "realized_pnl_usd": None,
            "unrealized_pnl_usd": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 4)
        }
    except Exception as e:
        print(f"[SNAPSHOT] Warning: Failed to get account state: {e}")
        return {
            "total_equity_usd": None,
            "cash_balance_usd": None,
            "balances": {},
            "realized_pnl_usd": None,
            "unrealized_pnl_usd": None,
            "unrealized_pnl_pct": None,
            "error": str(e)
        }


def _get_risk_config() -> Dict[str, Any]:
    """
    Get current risk/config state from trading_config.
    
    Returns dict with key config values that explain bot behavior.
    """
    try:
        from trading_config import get_config, ZIN_VERSION
        
        config = get_config()
        
        return {
            "regime_min_atr_pct": config.regime_min_atr_pct,
            "min_confidence": config.min_confidence_threshold,
            "regime_override_confidence": config.regime_override_confidence,
            "breakout_confidence_bonus": config.breakout_boost,
            "max_risk_per_trade_pct": config.risk.risk_per_trade_pct * 100,
            "enable_shorts": config.risk.enable_shorts,
            "max_concurrent_positions": config.risk.max_trades_per_day,
            "symbol_whitelist": config.symbol_whitelist,
            "symbol_blacklist": config.symbol_blacklist if config.symbol_blacklist else [],
            "validate_only": config.validate_only,
            "execution_mode": os.getenv("EXECUTION_MODE", "MARKET_ONLY"),
            "fee_gate_enabled": config.fee_gate_enabled,
            "regime_filter_enabled": config.regime_filter_enabled
        }
    except Exception as e:
        print(f"[SNAPSHOT] Warning: Failed to get risk config: {e}")
        return {
            "error": str(e)
        }


def _get_open_positions() -> List[Dict[str, Any]]:
    """
    Get open positions with mental SL/TP from position_tracker.
    
    Returns list of position dicts.
    """
    try:
        from position_tracker import get_all_positions
        from exchange_manager import get_exchange
        
        positions = get_all_positions()
        exchange = get_exchange()
        
        result = []
        for symbol, pos in positions.items():
            current_price = pos.entry_price
            try:
                ticker = exchange.fetch_ticker(symbol)
                current_price = ticker.get('last', pos.entry_price)
            except Exception:
                pass
            
            qty = pos.quantity if hasattr(pos, 'quantity') else 0
            entry_price = pos.entry_price if hasattr(pos, 'entry_price') else 0
            
            if pos.is_short:
                pnl_usd = (entry_price - current_price) * qty
            else:
                pnl_usd = (current_price - entry_price) * qty
            
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
            if pos.is_short:
                pnl_pct = -pnl_pct
            
            size_quote = qty * entry_price
            sl_price = pos.stop_loss_price if hasattr(pos, 'stop_loss_price') else 0
            tp_price = pos.take_profit_price if hasattr(pos, 'take_profit_price') else 0
            opened_at = pos.opened_at if hasattr(pos, 'opened_at') else None
            
            result.append({
                "symbol": symbol,
                "side": "short" if pos.is_short else "long",
                "size_base": round(qty, 8),
                "size_quote": round(size_quote, 2),
                "entry_price": round(entry_price, 6),
                "current_price": round(current_price, 6),
                "mental_stop_loss": round(sl_price, 6) if sl_price else None,
                "mental_take_profit": round(tp_price, 6) if tp_price else None,
                "opened_at": opened_at,
                "unrealized_pnl_usd": round(pnl_usd, 2),
                "unrealized_pnl_pct": round(pnl_pct, 4)
            })
        
        return result
    except Exception as e:
        print(f"[SNAPSHOT] Warning: Failed to get positions: {e}")
        return []


def _get_performance_summary() -> Dict[str, Any]:
    """
    Get compact performance summary from data_logger.
    
    Uses existing compute_daily_stats() helper.
    """
    try:
        from data_logger import compute_daily_stats
        
        date_str = _get_date_str()
        stats = compute_daily_stats(date_str)
        
        return {
            "date": date_str,
            "total_trades_today": stats.get("total_trades", 0),
            "win_rate_today": stats.get("win_rate", 0.0),
            "realized_pnl_today_usd": stats.get("total_pnl_abs", 0.0),
            "max_drawdown_today_pct": stats.get("max_drawdown_pct", 0.0)
        }
    except Exception as e:
        print(f"[SNAPSHOT] Warning: Failed to get performance summary: {e}")
        return {
            "date": _get_date_str(),
            "total_trades_today": None,
            "win_rate_today": None,
            "realized_pnl_today_usd": None,
            "max_drawdown_today_pct": None,
            "error": str(e)
        }


def _get_system_health() -> Dict[str, Any]:
    """
    Get lightweight system health info.
    
    Returns:
    - last_decision_timestamp
    - open_positions_count
    - recent_anomalies
    - uptime (if available)
    """
    try:
        from pathlib import Path
        import json
        
        last_decision = None
        decisions_dir = Path("data/decisions")
        if decisions_dir.exists():
            decision_files = sorted(decisions_dir.glob("*.jsonl"), reverse=True)
            if decision_files:
                with open(decision_files[0], 'r') as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
                        if last_line:
                            record = json.loads(last_line)
                            last_decision = record.get("logged_at")
        
        open_positions_count = 0
        try:
            from position_tracker import get_all_positions
            open_positions_count = len(get_all_positions())
        except Exception:
            pass
        
        has_recent_anomaly = False
        anomalies_file = Path("data/anomalies/anomalies.jsonl")
        if anomalies_file.exists():
            try:
                with open(anomalies_file, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
                        if last_line:
                            record = json.loads(last_line)
                            logged_at = record.get("logged_at", "")
                            if logged_at.startswith(_get_date_str()):
                                has_recent_anomaly = True
            except Exception:
                pass
        
        return {
            "last_decision_timestamp": last_decision,
            "open_positions_count": open_positions_count,
            "has_recent_anomaly": has_recent_anomaly,
            "uptime_seconds": None
        }
    except Exception as e:
        print(f"[SNAPSHOT] Warning: Failed to get system health: {e}")
        return {
            "last_decision_timestamp": None,
            "open_positions_count": 0,
            "has_recent_anomaly": None,
            "error": str(e)
        }


def build_snapshot() -> Dict[str, Any]:
    """
    Build a complete state snapshot.
    
    Returns dict with all snapshot sections:
    - metadata
    - account
    - risk_config
    - open_positions
    - performance_summary
    - system_health
    """
    try:
        from trading_config import ZIN_VERSION
        from account_state import get_mode_str
        mode = get_mode_str()
        zin_version = ZIN_VERSION
    except Exception:
        mode = "unknown"
        zin_version = "ZIN_V1"
    
    now = _get_utc_now()
    
    snapshot = {
        "logged_at": now.isoformat(),
        "zin_version": zin_version,
        "mode": mode,
        "snapshot_id": _generate_snapshot_id(),
        "date": now.strftime("%Y-%m-%d"),
        
        "account": _get_account_state(),
        "risk_config": _get_risk_config(),
        "open_positions": _get_open_positions(),
        "performance_summary": _get_performance_summary(),
        "system_health": _get_system_health()
    }
    
    return snapshot


def _count_snapshots_today() -> int:
    """Count how many snapshots exist for today's date."""
    today = _get_date_str()
    count = 0
    
    if not SNAPSHOTS_DIR.exists():
        return 0
    
    for f in SNAPSHOTS_DIR.glob("*.json"):
        if f.name.startswith(today.replace("-", "")):
            count += 1
    
    return count


def _get_last_snapshot_time() -> Optional[float]:
    """Get timestamp of most recent snapshot file."""
    if not SNAPSHOTS_DIR.exists():
        return None
    
    snapshot_files = list(SNAPSHOTS_DIR.glob("*.json"))
    if not snapshot_files:
        return None
    
    latest = max(snapshot_files, key=lambda f: f.stat().st_mtime)
    return latest.stat().st_mtime


def _reset_daily_counters_if_needed():
    """Reset daily counters when date changes (UTC rollover)."""
    global _snapshots_today_count, _snapshots_today_date, _last_snapshot_time
    
    today = _get_date_str()
    
    if _snapshots_today_date != today:
        _snapshots_today_date = today
        _snapshots_today_count = _count_snapshots_today()
        _last_snapshot_time = _get_last_snapshot_time()


def should_take_snapshot() -> bool:
    """
    Check if we should take a snapshot now.
    
    Conditions:
    - Must be in LIVE mode
    - At least 8 hours since last snapshot
    - Less than 3 snapshots today
    """
    global _last_snapshot_time, _snapshots_today_count, _snapshots_today_date
    
    try:
        from account_state import get_mode_str
        mode = get_mode_str()
        if mode != "live":
            return False
    except Exception:
        return False
    
    _reset_daily_counters_if_needed()
    
    now = time.time()
    
    if _snapshots_today_count >= MAX_SNAPSHOTS_PER_DAY:
        return False
    
    if _last_snapshot_time is not None:
        hours_since_last = (now - _last_snapshot_time) / 3600
        if hours_since_last < SNAPSHOT_INTERVAL_HOURS:
            return False
    
    return True


def save_snapshot(snapshot: Dict[str, Any]) -> Optional[str]:
    """
    Save snapshot to individual JSON file.
    
    Returns filepath on success, None on failure.
    """
    global _last_snapshot_time, _snapshots_today_count
    
    try:
        import json
        
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        
        logged_at = snapshot.get("logged_at", _get_utc_now().isoformat())
        
        try:
            dt = datetime.fromisoformat(logged_at.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            dt = _get_utc_now()
        
        filename = dt.strftime("%Y%m%dT%H-%M-%SZ") + "_snapshot.json"
        
        filepath = SNAPSHOTS_DIR / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, indent=2, default=str, ensure_ascii=False)
        
        _last_snapshot_time = time.time()
        _snapshots_today_count += 1
        
        print(f"[SNAPSHOT] ✓ Saved snapshot: {filepath}")
        return str(filepath)
    
    except Exception as e:
        print(f"[SNAPSHOT] ERROR: Failed to save snapshot: {e}")
        return None


def maybe_take_snapshot() -> Optional[str]:
    """
    Check conditions and take snapshot if appropriate.
    
    This is the main entry point called from autopilot loop.
    Returns filepath if snapshot was taken, None otherwise.
    """
    if not should_take_snapshot():
        return None
    
    print("[SNAPSHOT] Taking periodic state snapshot...")
    
    snapshot = build_snapshot()
    filepath = save_snapshot(snapshot)
    
    if filepath:
        print(f"[SNAPSHOT] ✓ Snapshot complete ({_snapshots_today_count}/{MAX_SNAPSHOTS_PER_DAY} today)")
    
    return filepath


def force_snapshot() -> Optional[str]:
    """
    Force take a snapshot regardless of scheduling.
    
    Useful for manual triggering or testing.
    """
    print("[SNAPSHOT] Forcing snapshot (bypassing schedule)...")
    snapshot = build_snapshot()
    return save_snapshot(snapshot)
