"""
Data Logger Module - Centralized structured logging for Zin Trading Bot

This module provides a centralized, append-only logging system for:
- Trade outcomes (JSONL per day)
- Decision events (JSONL per day) 
- Daily summaries (JSON per day)
- Version/config history (JSONL)
- Anomaly events (JSONL)

All logs are written to the /data directory in machine-readable formats.
Logging failures never crash the trading bot - all writes are wrapped in try/except.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pathlib import Path


DATA_DIR = Path("data")
TRADES_DIR = DATA_DIR / "trades"
DECISIONS_DIR = DATA_DIR / "decisions"
DAILY_DIR = DATA_DIR / "daily"
META_DIR = DATA_DIR / "meta"
ANOMALIES_DIR = DATA_DIR / "anomalies"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"


def _ensure_directories():
    """Create data directories if they don't exist."""
    for dir_path in [DATA_DIR, TRADES_DIR, DECISIONS_DIR, DAILY_DIR, META_DIR, ANOMALIES_DIR, SNAPSHOTS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)


def _get_timestamp() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _get_date_str() -> str:
    """Get current date as YYYY-MM-DD string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _safe_json_dumps(record: Dict[str, Any]) -> str:
    """Safely convert record to JSON string, handling non-serializable types."""
    def default_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, '__dict__'):
            return str(obj)
        return str(obj)
    
    return json.dumps(record, default=default_serializer, ensure_ascii=False)


def _append_jsonl(file_path: Path, record: Dict[str, Any]) -> bool:
    """Append a single JSON record to a JSONL file. Returns True on success."""
    try:
        _ensure_directories()
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(_safe_json_dumps(record) + '\n')
        return True
    except Exception as e:
        print(f"[DATA-LOGGER] WARNING: Failed to write to {file_path}: {e}")
        return False


def _write_json(file_path: Path, record: Dict[str, Any]) -> bool:
    """Write a single JSON record to a file (overwrites). Returns True on success."""
    try:
        _ensure_directories()
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(record, f, indent=2, default=str, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[DATA-LOGGER] WARNING: Failed to write to {file_path}: {e}")
        return False


class DataLogger:
    """
    Centralized data logger for Zin trading bot.
    
    All methods are designed to be safe - they will never crash the bot
    even if file I/O fails.
    """
    
    def __init__(self, zin_version: str = "ZIN_V1"):
        """Initialize the DataLogger with a version string."""
        self.zin_version = zin_version
        _ensure_directories()
    
    def log_trade(self, trade_record: Dict[str, Any]) -> bool:
        """
        Log a completed trade to the daily trades JSONL file.
        
        Expected fields (partial records are OK):
        - timestamp_open, timestamp_close
        - zin_version, mode (live/paper)
        - symbol, direction (long/short)
        - entry_price, exit_price, size
        - pnl_abs, pnl_pct
        - max_favorable_excursion_pct, max_adverse_excursion_pct
        - reason_code, regime (dict), decision_id
        
        Returns True on success, False on failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": trade_record.get("zin_version", self.zin_version),
            **trade_record
        }
        
        date_str = _get_date_str()
        file_path = TRADES_DIR / f"{date_str}_trades.jsonl"
        
        success = _append_jsonl(file_path, record)
        if success:
            print(f"[DATA-LOGGER] Trade logged: {trade_record.get('symbol', 'N/A')} {trade_record.get('direction', 'N/A')}")
        return success
    
    def log_decision(self, decision_record: Dict[str, Any]) -> bool:
        """
        Log a trading decision (evaluation) to the daily decisions JSONL file.
        
        Expected fields:
        - timestamp, zin_version, mode
        - symbol, timeframe
        - decision (NO_TRADE, ENTER_LONG, EXIT_LONG, etc.)
        - indicators (dict of indicator values used)
        - regime (dict with trend, volatility)
        - risk_context (dict with equity, active_risk_pct, etc.)
        - filters (dict of boolean filter states)
        - reason_code, decision_id
        
        Returns True on success, False on failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": decision_record.get("zin_version", self.zin_version),
            **decision_record
        }
        
        date_str = _get_date_str()
        file_path = DECISIONS_DIR / f"{date_str}_decisions.jsonl"
        
        return _append_jsonl(file_path, record)
    
    def log_daily_summary(self, summary_record: Dict[str, Any]) -> bool:
        """
        Log a daily summary to a per-day JSON file.
        
        Expected fields:
        - date, zin_version, mode
        - total_trades, win_rate
        - total_pnl_abs, total_pnl_pct
        - max_drawdown_pct
        - biggest_win_pct, biggest_loss_pct
        - subjective_tag (optional), notes (optional)
        
        Returns True on success, False on failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": summary_record.get("zin_version", self.zin_version),
            **summary_record
        }
        
        date_str = summary_record.get("date", _get_date_str())
        file_path = DAILY_DIR / f"{date_str}_summary.json"
        
        success = _write_json(file_path, record)
        if success:
            print(f"[DATA-LOGGER] Daily summary logged for {date_str}")
        return success
    
    def log_version(self, version_record: Dict[str, Any]) -> bool:
        """
        Log a version/config snapshot to the versions JSONL file.
        
        Expected fields:
        - timestamp, zin_version
        - config (dict of key config values)
        - comment (short description)
        
        Returns True on success, False on failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": version_record.get("zin_version", self.zin_version),
            **version_record
        }
        
        file_path = META_DIR / "versions.jsonl"
        
        success = _append_jsonl(file_path, record)
        if success:
            print(f"[DATA-LOGGER] Version logged: {self.zin_version}")
        return success
    
    def log_anomaly(self, anomaly_record: Dict[str, Any]) -> bool:
        """
        Log an anomaly/unexpected event to the anomalies JSONL file.
        
        Expected fields:
        - timestamp, zin_version
        - type (UNEXPECTED_BEHAVIOR, API_ERROR, POSITION_MISMATCH, etc.)
        - description (human-readable message)
        - context (dict with extras like symbol, regime, etc.)
        
        Returns True on success, False on failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": anomaly_record.get("zin_version", self.zin_version),
            **anomaly_record
        }
        
        file_path = ANOMALIES_DIR / "anomalies.jsonl"
        
        success = _append_jsonl(file_path, record)
        if success:
            print(f"[DATA-LOGGER] Anomaly logged: {anomaly_record.get('type', 'UNKNOWN')} - {anomaly_record.get('description', 'N/A')[:50]}")
        return success
    
    def log_snapshot(self, snapshot_record: Dict[str, Any]) -> bool:
        """
        Log a market/state snapshot (for future use).
        
        Returns True on success, False on failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": snapshot_record.get("zin_version", self.zin_version),
            **snapshot_record
        }
        
        date_str = _get_date_str()
        file_path = SNAPSHOTS_DIR / f"{date_str}_snapshots.jsonl"
        
        return _append_jsonl(file_path, record)


_data_logger_instance: Optional[DataLogger] = None


def get_data_logger(zin_version: str = None) -> DataLogger:
    """Get or create the singleton DataLogger instance."""
    global _data_logger_instance
    
    if _data_logger_instance is None:
        from trading_config import get_zin_version
        version = zin_version or get_zin_version()
        _data_logger_instance = DataLogger(zin_version=version)
    
    return _data_logger_instance


def log_trade(trade_record: Dict[str, Any]) -> bool:
    """Convenience function to log a trade."""
    return get_data_logger().log_trade(trade_record)


def log_decision(decision_record: Dict[str, Any]) -> bool:
    """Convenience function to log a decision."""
    return get_data_logger().log_decision(decision_record)


def log_daily_summary(summary_record: Dict[str, Any]) -> bool:
    """Convenience function to log a daily summary."""
    return get_data_logger().log_daily_summary(summary_record)


def log_version(version_record: Dict[str, Any]) -> bool:
    """Convenience function to log a version."""
    return get_data_logger().log_version(version_record)


def log_anomaly(anomaly_record: Dict[str, Any]) -> bool:
    """Convenience function to log an anomaly."""
    return get_data_logger().log_anomaly(anomaly_record)


def log_anomaly_event(
    anomaly_type: str,
    description: str,
    symbol: Optional[str] = None,
    **context
) -> bool:
    """
    Convenience function to log an anomaly event with a simplified interface.
    
    Args:
        anomaly_type: Type of anomaly (e.g., "API_ERROR", "POSITION_MISMATCH")
        description: Human-readable description
        symbol: Optional symbol associated with the anomaly
        **context: Additional context key-value pairs
    
    Returns True on success, False on failure.
    """
    record = {
        "timestamp": _get_timestamp(),
        "type": anomaly_type,
        "description": description,
        "context": {
            "symbol": symbol,
            **context
        } if symbol or context else {}
    }
    return log_anomaly(record)


def generate_decision_id(symbol: str, timeframe: str = "5m") -> str:
    """Generate a unique decision ID for linking decisions to trades."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"{timestamp}_{symbol.replace('/', '_')}_{timeframe}"


def read_trades_for_date(date_str: str) -> list:
    """
    Read all trades for a given date.
    
    Args:
        date_str: Date in YYYY-MM-DD format
    
    Returns list of trade records.
    """
    file_path = TRADES_DIR / f"{date_str}_trades.jsonl"
    trades = []
    
    try:
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))
    except Exception as e:
        print(f"[DATA-LOGGER] WARNING: Failed to read trades for {date_str}: {e}")
    
    return trades


def compute_daily_stats(date_str: str = None) -> Dict[str, Any]:
    """
    Compute daily statistics from trade logs.
    
    Args:
        date_str: Date in YYYY-MM-DD format, defaults to today
    
    Returns dict with computed stats.
    """
    if date_str is None:
        date_str = _get_date_str()
    
    trades = read_trades_for_date(date_str)
    
    if not trades:
        return {
            "date": date_str,
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl_abs": 0.0,
            "total_pnl_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "biggest_win_pct": 0.0,
            "biggest_loss_pct": 0.0
        }
    
    total_trades = len(trades)
    wins = sum(1 for t in trades if (t.get("pnl_abs") or 0) > 0)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    
    total_pnl_abs = sum(t.get("pnl_abs", 0) or 0 for t in trades)
    pnl_pcts = [t.get("pnl_pct", 0) or 0 for t in trades]
    total_pnl_pct = sum(pnl_pcts)
    
    biggest_win_pct = max((p for p in pnl_pcts if p > 0), default=0.0)
    biggest_loss_pct = min((p for p in pnl_pcts if p < 0), default=0.0)
    
    running_pnl = 0
    max_drawdown = 0
    peak = 0
    for pnl in pnl_pcts:
        running_pnl += pnl
        if running_pnl > peak:
            peak = running_pnl
        drawdown = peak - running_pnl
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    
    return {
        "date": date_str,
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "total_pnl_abs": round(total_pnl_abs, 2),
        "total_pnl_pct": round(total_pnl_pct, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "biggest_win_pct": round(biggest_win_pct, 4),
        "biggest_loss_pct": round(biggest_loss_pct, 4)
    }
