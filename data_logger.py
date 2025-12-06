"""
Data Logger Module - Centralized structured logging for Zin Trading Bot

This module provides a centralized, append-only logging system for:
- Trade outcomes (PostgreSQL + JSONL fallback)
- Decision events (PostgreSQL + JSONL fallback) 
- Daily summaries (PostgreSQL + JSON fallback)
- Version/config history (PostgreSQL + JSONL fallback)
- Anomaly events (PostgreSQL + JSONL fallback)

Primary storage is PostgreSQL for persistence across VM republishes.
File logging is kept as a fallback and for local development.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pathlib import Path
from contextlib import contextmanager

_discord_db_error_func = None

def _send_db_error_discord(operation: str, error: str, context: str = ""):
    """Send database error to Discord (lazy import to avoid circular dependency)."""
    global _discord_db_error_func
    if _discord_db_error_func is None:
        try:
            from discord_notifications import send_database_error_notification
            _discord_db_error_func = send_database_error_notification
        except ImportError:
            _discord_db_error_func = lambda *args, **kwargs: False
    
    try:
        _discord_db_error_func(operation, error, context)
    except Exception:
        pass

DATA_DIR = Path("data")
TRADES_DIR = DATA_DIR / "trades"
DECISIONS_DIR = DATA_DIR / "decisions"
DAILY_DIR = DATA_DIR / "daily"
META_DIR = DATA_DIR / "meta"
ANOMALIES_DIR = DATA_DIR / "anomalies"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

_db_pool = None
_db_available = None


_db_error_notified = False

def _get_db_connection():
    """Get a database connection from the pool."""
    global _db_pool, _db_available, _db_error_notified
    
    if _db_available is False:
        return None
    
    if _db_pool is None:
        try:
            import psycopg2
            from psycopg2 import pool
            
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                print("[DATA-LOGGER] No DATABASE_URL found, using file-only mode")
                _db_available = False
                if not _db_error_notified:
                    _db_error_notified = True
                    _send_db_error_discord("db_init", "DATABASE_URL not configured", "Using file-only mode")
                return None
            
            _db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, database_url)
            _db_available = True
            print("[DATA-LOGGER] PostgreSQL connection pool initialized")
        except Exception as e:
            print(f"[DATA-LOGGER] PostgreSQL unavailable, using file-only mode: {e}")
            _db_available = False
            if not _db_error_notified:
                _db_error_notified = True
                _send_db_error_discord("db_init", str(e), "Connection pool failed")
            return None
    
    try:
        return _db_pool.getconn()
    except Exception as e:
        print(f"[DATA-LOGGER] Failed to get DB connection: {e}")
        _send_db_error_discord("db_connection", str(e), "Pool exhausted")
        return None


def _return_db_connection(conn):
    """Return a connection to the pool."""
    global _db_pool
    if _db_pool and conn:
        try:
            _db_pool.putconn(conn)
        except Exception:
            pass


@contextmanager
def _db_cursor():
    """Context manager for database operations.
    
    Yields a cursor or None if DB unavailable.
    Caller must handle None case by falling back to file storage.
    On DB error during operations, caller's block will raise - they should catch and fallback.
    """
    conn = _get_db_connection()
    if conn is None:
        yield None
        return
    
    cursor = None
    try:
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"[DATA-LOGGER] DB error: {e}")
        raise
    finally:
        _return_db_connection(conn)


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


def _to_json(data: Any) -> Optional[str]:
    """Convert data to JSON string for database storage."""
    if data is None:
        return None
    try:
        return _safe_json_dumps(data)
    except Exception:
        return None


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
    
    Writes to PostgreSQL (primary) with file fallback.
    All methods are designed to be safe - they will never crash the bot
    even if database or file I/O fails.
    """
    
    def __init__(self, zin_version: str = "ZIN_V1"):
        """Initialize the DataLogger with a version string."""
        self.zin_version = zin_version
        _ensure_directories()
    
    def log_trade(self, trade_record: Dict[str, Any]) -> bool:
        """
        Log a completed trade to PostgreSQL and daily trades JSONL file.
        
        Returns True on success (either DB or file), False on total failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": trade_record.get("zin_version", self.zin_version),
            **trade_record
        }
        
        date_str = _get_date_str()
        db_success = False
        
        try:
            with _db_cursor() as cursor:
                if cursor:
                    cursor.execute("""
                        INSERT INTO trades (
                            trade_date, zin_version, mode, symbol, direction,
                            entry_price, exit_price, size, pnl_abs, pnl_pct,
                            max_favorable_excursion_pct, max_adverse_excursion_pct,
                            reason_code, regime, decision_id, timestamp_open,
                            timestamp_close, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                    """, (
                        date_str,
                        record.get("zin_version"),
                        record.get("mode", "live"),
                        record.get("symbol"),
                        record.get("direction"),
                        record.get("entry_price"),
                        record.get("exit_price"),
                        record.get("size"),
                        record.get("pnl_abs"),
                        record.get("pnl_pct"),
                        record.get("max_favorable_excursion_pct"),
                        record.get("max_adverse_excursion_pct"),
                        record.get("reason_code"),
                        _to_json(record.get("regime")),
                        record.get("decision_id"),
                        record.get("timestamp_open"),
                        record.get("timestamp_close"),
                        _to_json(record)
                    ))
            db_success = cursor is not None
            if db_success:
                print(f"[DATA-LOGGER] Trade logged to DB: {trade_record.get('symbol', 'N/A')}")
        except Exception as e:
            db_success = False
            print(f"[DATA-LOGGER] DB trade insert failed: {e}")
            _send_db_error_discord("trade_log", str(e), trade_record.get('symbol', ''))
        
        file_path = TRADES_DIR / f"{date_str}_trades.jsonl"
        file_success = _append_jsonl(file_path, record)
        
        if db_success or file_success:
            print(f"[DATA-LOGGER] Trade logged: {trade_record.get('symbol', 'N/A')} {trade_record.get('direction', 'N/A')}")
        
        return db_success or file_success
    
    def log_decision(self, decision_record: Dict[str, Any]) -> bool:
        """
        Log a trading decision to PostgreSQL and daily decisions JSONL file.
        
        Returns True on success (either DB or file), False on total failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": decision_record.get("zin_version", self.zin_version),
            **decision_record
        }
        
        date_str = _get_date_str()
        db_success = False
        
        try:
            with _db_cursor() as cursor:
                if cursor:
                    cursor.execute("""
                        INSERT INTO decisions (
                            decision_date, zin_version, mode, symbol, timeframe,
                            decision, indicators, regime, risk_context, filters,
                            reason_code, decision_id, confidence, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                    """, (
                        date_str,
                        record.get("zin_version"),
                        record.get("mode", "live"),
                        record.get("symbol"),
                        record.get("timeframe"),
                        record.get("decision"),
                        _to_json(record.get("indicators")),
                        _to_json(record.get("regime")),
                        _to_json(record.get("risk_context")),
                        _to_json(record.get("filters")),
                        record.get("reason_code"),
                        record.get("decision_id"),
                        record.get("confidence"),
                        _to_json(record)
                    ))
            db_success = cursor is not None
        except Exception as e:
            db_success = False
            print(f"[DATA-LOGGER] DB decision insert failed: {e}")
            if decision_record.get("decision") in ["BUY", "SELL"]:
                _send_db_error_discord("decision_log", str(e), decision_record.get('symbol', ''))
        
        file_path = DECISIONS_DIR / f"{date_str}_decisions.jsonl"
        file_success = _append_jsonl(file_path, record)
        
        return db_success or file_success
    
    def log_daily_summary(self, summary_record: Dict[str, Any]) -> bool:
        """
        Log a daily summary to PostgreSQL and per-day JSON file.
        
        Returns True on success (either DB or file), False on total failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": summary_record.get("zin_version", self.zin_version),
            **summary_record
        }
        
        date_str = summary_record.get("date", _get_date_str())
        db_success = False
        
        try:
            with _db_cursor() as cursor:
                if cursor:
                    cursor.execute("""
                        INSERT INTO daily_summaries (
                            summary_date, zin_version, mode, total_trades,
                            win_rate, total_pnl_abs, total_pnl_pct,
                            max_drawdown_pct, biggest_win_pct, biggest_loss_pct,
                            subjective_tag, notes
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (summary_date) DO UPDATE SET
                            zin_version = EXCLUDED.zin_version,
                            mode = EXCLUDED.mode,
                            total_trades = EXCLUDED.total_trades,
                            win_rate = EXCLUDED.win_rate,
                            total_pnl_abs = EXCLUDED.total_pnl_abs,
                            total_pnl_pct = EXCLUDED.total_pnl_pct,
                            max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                            biggest_win_pct = EXCLUDED.biggest_win_pct,
                            biggest_loss_pct = EXCLUDED.biggest_loss_pct,
                            subjective_tag = EXCLUDED.subjective_tag,
                            notes = EXCLUDED.notes,
                            logged_at = NOW()
                    """, (
                        date_str,
                        record.get("zin_version"),
                        record.get("mode", "live"),
                        record.get("total_trades", 0),
                        record.get("win_rate"),
                        record.get("total_pnl_abs"),
                        record.get("total_pnl_pct"),
                        record.get("max_drawdown_pct"),
                        record.get("biggest_win_pct"),
                        record.get("biggest_loss_pct"),
                        record.get("subjective_tag"),
                        record.get("notes")
                    ))
            db_success = cursor is not None
            if db_success:
                print(f"[DATA-LOGGER] Daily summary logged to DB for {date_str}")
        except Exception as e:
            db_success = False
            print(f"[DATA-LOGGER] DB daily summary insert failed: {e}")
        
        file_path = DAILY_DIR / f"{date_str}_summary.json"
        file_success = _write_json(file_path, record)
        
        if db_success or file_success:
            print(f"[DATA-LOGGER] Daily summary logged for {date_str}")
        
        return db_success or file_success
    
    def log_version(self, version_record: Dict[str, Any]) -> bool:
        """
        Log a version/config snapshot to PostgreSQL and versions JSONL file.
        
        Returns True on success (either DB or file), False on total failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": version_record.get("zin_version", self.zin_version),
            **version_record
        }
        
        db_success = False
        
        try:
            with _db_cursor() as cursor:
                if cursor:
                    cursor.execute("""
                        INSERT INTO versions (zin_version, config, comment)
                        VALUES (%s, %s, %s)
                    """, (
                        record.get("zin_version"),
                        _to_json(record.get("config")),
                        record.get("comment")
                    ))
            db_success = cursor is not None
            if db_success:
                print(f"[DATA-LOGGER] Version logged to DB: {self.zin_version}")
        except Exception as e:
            db_success = False
            print(f"[DATA-LOGGER] DB version insert failed: {e}")
        
        file_path = META_DIR / "versions.jsonl"
        file_success = _append_jsonl(file_path, record)
        
        if db_success or file_success:
            print(f"[DATA-LOGGER] Version logged: {self.zin_version}")
        
        return db_success or file_success
    
    def log_anomaly(self, anomaly_record: Dict[str, Any]) -> bool:
        """
        Log an anomaly/unexpected event to PostgreSQL and anomalies JSONL file.
        
        Returns True on success (either DB or file), False on total failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": anomaly_record.get("zin_version", self.zin_version),
            **anomaly_record
        }
        
        db_success = False
        
        try:
            with _db_cursor() as cursor:
                if cursor:
                    context = anomaly_record.get("context", {})
                    cursor.execute("""
                        INSERT INTO anomalies (
                            zin_version, anomaly_type, description, context, symbol, severity
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        record.get("zin_version"),
                        anomaly_record.get("type", "UNKNOWN"),
                        anomaly_record.get("description"),
                        _to_json(context),
                        context.get("symbol") if isinstance(context, dict) else None,
                        anomaly_record.get("severity", "warning")
                    ))
            db_success = cursor is not None
        except Exception as e:
            db_success = False
            print(f"[DATA-LOGGER] DB anomaly insert failed: {e}")
        
        file_path = ANOMALIES_DIR / "anomalies.jsonl"
        file_success = _append_jsonl(file_path, record)
        
        if db_success or file_success:
            print(f"[DATA-LOGGER] Anomaly logged: {anomaly_record.get('type', 'UNKNOWN')} - {anomaly_record.get('description', 'N/A')[:50]}")
        
        return db_success or file_success
    
    def log_snapshot(self, snapshot_record: Dict[str, Any]) -> Optional[str]:
        """
        Log a state snapshot to PostgreSQL and individual JSON file.
        
        Returns filepath on success, None on failure.
        """
        record = {
            "logged_at": _get_timestamp(),
            "zin_version": snapshot_record.get("zin_version", self.zin_version),
            **snapshot_record
        }
        
        try:
            _ensure_directories()
            
            logged_at = record.get("logged_at", _get_timestamp())
            dt = datetime.fromisoformat(logged_at.replace('Z', '+00:00'))
            snapshot_id = dt.strftime("%Y%m%dT%H-%M-%SZ")
            filename = snapshot_id + "_snapshot.json"
            
            try:
                with _db_cursor() as cursor:
                    if cursor:
                        cursor.execute("""
                            INSERT INTO snapshots (
                                snapshot_id, zin_version, account_status, risk_config,
                                open_positions, performance_summary, system_health, metadata
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (snapshot_id) DO UPDATE SET
                                zin_version = EXCLUDED.zin_version,
                                account_status = EXCLUDED.account_status,
                                risk_config = EXCLUDED.risk_config,
                                open_positions = EXCLUDED.open_positions,
                                performance_summary = EXCLUDED.performance_summary,
                                system_health = EXCLUDED.system_health,
                                metadata = EXCLUDED.metadata,
                                logged_at = NOW()
                        """, (
                            snapshot_id,
                            record.get("zin_version"),
                            _to_json(snapshot_record.get("account_status")),
                            _to_json(snapshot_record.get("risk_config")),
                            _to_json(snapshot_record.get("open_positions")),
                            _to_json(snapshot_record.get("performance_summary")),
                            _to_json(snapshot_record.get("system_health")),
                            _to_json(record)
                        ))
                        print(f"[DATA-LOGGER] Snapshot logged to DB: {snapshot_id}")
            except Exception as e:
                print(f"[DATA-LOGGER] DB snapshot insert failed: {e}")
            
            file_path = SNAPSHOTS_DIR / filename
            success = _write_json(file_path, record)
            
            if success:
                print(f"[DATA-LOGGER] Snapshot logged: {file_path}")
                return str(file_path)
            return None
        except Exception as e:
            print(f"[DATA-LOGGER] WARNING: Failed to write snapshot: {e}")
            return None


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
    Read all trades for a given date from PostgreSQL (primary) or file (fallback).
    """
    trades = []
    
    try:
        with _db_cursor() as cursor:
            if cursor:
                cursor.execute("""
                    SELECT metadata FROM trades WHERE trade_date = %s ORDER BY logged_at
                """, (date_str,))
                rows = cursor.fetchall()
                for row in rows:
                    if row[0]:
                        trades.append(json.loads(row[0]) if isinstance(row[0], str) else row[0])
                if trades:
                    return trades
    except Exception as e:
        print(f"[DATA-LOGGER] DB read failed, falling back to file: {e}")
    
    file_path = TRADES_DIR / f"{date_str}_trades.jsonl"
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
