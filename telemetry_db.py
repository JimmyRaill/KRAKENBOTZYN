"""
Trading Telemetry Database - Stores all trades, decisions, and learning data.
This is the bot's long-term memory for self-improvement.

Updated to use PostgreSQL for persistence across VM republishes.
Falls back to SQLite for local development if PostgreSQL is unavailable.
"""
import json
import time
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from contextlib import contextmanager

SQLITE_DB_PATH = Path(__file__).parent / "trading_memory.db"

_pg_pool = None
_use_postgres = None


def _get_postgres_pool():
    """Get or create PostgreSQL connection pool."""
    global _pg_pool, _use_postgres
    
    if _use_postgres is False:
        return None
    
    if _pg_pool is None:
        try:
            import psycopg2
            from psycopg2 import pool
            
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                print("[TELEMETRY-DB] No DATABASE_URL, using SQLite fallback")
                _use_postgres = False
                return None
            
            _pg_pool = psycopg2.pool.SimpleConnectionPool(1, 5, database_url)
            _use_postgres = True
            print("[TELEMETRY-DB] PostgreSQL connection pool initialized")
        except Exception as e:
            print(f"[TELEMETRY-DB] PostgreSQL unavailable, using SQLite: {e}")
            _use_postgres = False
            return None
    
    return _pg_pool


@contextmanager
def get_db():
    """Context manager for database connections - PostgreSQL primary, SQLite fallback."""
    pool = _get_postgres_pool()
    
    if pool:
        conn = None
        try:
            conn = pool.getconn()
            conn.autocommit = False
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"[TELEMETRY-DB] PostgreSQL error: {e}")
            raise
        finally:
            if conn:
                pool.putconn(conn)
    else:
        import sqlite3
        conn = sqlite3.connect(str(SQLITE_DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _is_postgres() -> bool:
    """Check if using PostgreSQL."""
    _get_postgres_pool()
    return _use_postgres is True


def _placeholder(index: int = 1) -> str:
    """Return appropriate placeholder for DB type."""
    return "%s" if _is_postgres() else "?"


def init_db() -> None:
    """Create all tables if they don't exist."""
    if _is_postgres():
        _init_postgres_tables()
    else:
        _init_sqlite_tables()


def _init_postgres_tables() -> None:
    """Initialize PostgreSQL tables (already created via SQL tool)."""
    pass


def _init_sqlite_tables() -> None:
    """Initialize SQLite tables for local development."""
    import sqlite3
    conn = sqlite3.connect(str(SQLITE_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            action TEXT NOT NULL,
            quantity REAL,
            price REAL,
            usd_amount REAL,
            order_id TEXT,
            reason TEXT,
            source TEXT,
            metadata TEXT,
            mode TEXT DEFAULT 'live',
            stop_loss REAL,
            take_profit REAL,
            trade_id TEXT,
            strategy TEXT,
            entry_price REAL,
            exit_price REAL,
            position_size REAL,
            initial_risk REAL,
            r_multiple REAL,
            open_timestamp REAL,
            close_timestamp REAL,
            pnl REAL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            price REAL,
            edge_pct REAL,
            atr REAL,
            position_qty REAL,
            equity_usd REAL,
            executed INTEGER DEFAULT 0,
            metadata TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            date TEXT NOT NULL,
            hour INTEGER,
            equity_usd REAL NOT NULL,
            equity_change_usd REAL,
            open_positions TEXT,
            symbols_traded TEXT,
            metadata TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            category TEXT NOT NULL,
            insight_type TEXT NOT NULL,
            symbol TEXT,
            description TEXT NOT NULL,
            confidence REAL,
            supporting_data TEXT,
            metadata TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            date TEXT NOT NULL,
            error_type TEXT NOT NULL,
            symbol TEXT,
            description TEXT NOT NULL,
            context TEXT,
            lesson_learned TEXT,
            resolved INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            user_message TEXT NOT NULL,
            bot_response TEXT NOT NULL,
            intent TEXT,
            context TEXT
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_performance_date ON performance(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_insights_category ON insights(category)")
    
    conn.commit()
    conn.close()


def log_trade(
    symbol: str,
    side: str,
    action: str,
    quantity: Optional[float] = None,
    price: Optional[float] = None,
    usd_amount: Optional[float] = None,
    order_id: Optional[str] = None,
    reason: Optional[str] = None,
    source: str = "unknown",
    metadata: Optional[Dict[str, Any]] = None,
    mode: str = "live",
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    trade_id: Optional[str] = None,
    strategy: Optional[str] = None,
    entry_price: Optional[float] = None,
    exit_price: Optional[float] = None,
    position_size: Optional[float] = None,
    initial_risk: Optional[float] = None,
    r_multiple: Optional[float] = None,
    open_timestamp: Optional[float] = None,
    close_timestamp: Optional[float] = None,
    pnl: Optional[float] = None
) -> Optional[int]:
    """
    Log an executed trade to PostgreSQL (primary) or SQLite (fallback).
    """
    now = time.time()
    dt = datetime.fromtimestamp(now)
    date_str = dt.strftime("%Y-%m-%d")
    metadata_json = json.dumps(metadata) if metadata else None
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            if _is_postgres():
                cursor.execute("""
                    INSERT INTO telemetry_trades (
                        timestamp, date, symbol, side, action, quantity, price,
                        usd_amount, order_id, reason, source, metadata, mode, stop_loss, take_profit,
                        trade_id, strategy, entry_price, exit_price, position_size,
                        initial_risk, r_multiple, open_timestamp, close_timestamp, pnl
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    now, date_str, symbol, side, action, quantity, price,
                    usd_amount, order_id, reason, source, metadata_json, mode, stop_loss, take_profit,
                    trade_id, strategy, entry_price, exit_price, position_size,
                    initial_risk, r_multiple, open_timestamp, close_timestamp, pnl
                ))
                result = cursor.fetchone()
                return result[0] if result else None
            else:
                cursor.execute("""
                    INSERT INTO trades (
                        timestamp, date, symbol, side, action, quantity, price,
                        usd_amount, order_id, reason, source, metadata, mode, stop_loss, take_profit,
                        trade_id, strategy, entry_price, exit_price, position_size,
                        initial_risk, r_multiple, open_timestamp, close_timestamp, pnl
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now, date_str, symbol, side, action, quantity, price,
                    usd_amount, order_id, reason, source, metadata_json, mode, stop_loss, take_profit,
                    trade_id, strategy, entry_price, exit_price, position_size,
                    initial_risk, r_multiple, open_timestamp, close_timestamp, pnl
                ))
                return cursor.lastrowid
    except Exception as e:
        print(f"[TELEMETRY-DB] Failed to log trade: {e}")
        return None


def log_decision(
    symbol: str,
    action: str,
    reason: Optional[str] = None,
    price: Optional[float] = None,
    edge_pct: Optional[float] = None,
    atr: Optional[float] = None,
    position_qty: Optional[float] = None,
    equity_usd: Optional[float] = None,
    executed: bool = False,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """Log a trading decision (executed or not)."""
    now = time.time()
    dt = datetime.fromtimestamp(now)
    date_str = dt.strftime("%Y-%m-%d")
    metadata_json = json.dumps(metadata) if metadata else None
    executed_int = 1 if executed else 0
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            if _is_postgres():
                cursor.execute("""
                    INSERT INTO telemetry_decisions (
                        timestamp, date, symbol, action, reason, price, edge_pct,
                        atr, position_qty, equity_usd, executed, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    now, date_str, symbol, action, reason, price, edge_pct,
                    atr, position_qty, equity_usd, executed_int, metadata_json
                ))
                result = cursor.fetchone()
                return result[0] if result else None
            else:
                cursor.execute("""
                    INSERT INTO decisions (
                        timestamp, date, symbol, action, reason, price, edge_pct,
                        atr, position_qty, equity_usd, executed, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now, date_str, symbol, action, reason, price, edge_pct,
                    atr, position_qty, equity_usd, executed_int, metadata_json
                ))
                return cursor.lastrowid
    except Exception as e:
        print(f"[TELEMETRY-DB] Failed to log decision: {e}")
        return None


def log_performance(
    equity_usd: float,
    equity_change_usd: Optional[float] = None,
    open_positions: Optional[List[Dict[str, Any]]] = None,
    symbols_traded: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """Log a performance snapshot."""
    now = time.time()
    dt = datetime.fromtimestamp(now)
    date_str = dt.strftime("%Y-%m-%d")
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            if _is_postgres():
                cursor.execute("""
                    INSERT INTO telemetry_performance (
                        timestamp, date, hour, equity_usd, equity_change_usd,
                        open_positions, symbols_traded, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    now, date_str, dt.hour, equity_usd, equity_change_usd,
                    json.dumps(open_positions) if open_positions else None,
                    json.dumps(symbols_traded) if symbols_traded else None,
                    json.dumps(metadata) if metadata else None
                ))
                result = cursor.fetchone()
                return result[0] if result else None
            else:
                cursor.execute("""
                    INSERT INTO performance (
                        timestamp, date, hour, equity_usd, equity_change_usd,
                        open_positions, symbols_traded, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now, date_str, dt.hour, equity_usd, equity_change_usd,
                    json.dumps(open_positions) if open_positions else None,
                    json.dumps(symbols_traded) if symbols_traded else None,
                    json.dumps(metadata) if metadata else None
                ))
                return cursor.lastrowid
    except Exception as e:
        print(f"[TELEMETRY-DB] Failed to log performance: {e}")
        return None


def log_insight(
    category: str,
    insight_type: str,
    description: str,
    symbol: Optional[str] = None,
    confidence: Optional[float] = None,
    supporting_data: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """Store a learned insight or pattern."""
    now = time.time()
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            if _is_postgres():
                cursor.execute("""
                    INSERT INTO telemetry_insights (
                        created_at, category, insight_type, symbol, description,
                        confidence, supporting_data, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    now, category, insight_type, symbol, description, confidence,
                    json.dumps(supporting_data) if supporting_data else None,
                    json.dumps(metadata) if metadata else None
                ))
                result = cursor.fetchone()
                return result[0] if result else None
            else:
                cursor.execute("""
                    INSERT INTO insights (
                        created_at, category, insight_type, symbol, description,
                        confidence, supporting_data, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now, category, insight_type, symbol, description, confidence,
                    json.dumps(supporting_data) if supporting_data else None,
                    json.dumps(metadata) if metadata else None
                ))
                return cursor.lastrowid
    except Exception as e:
        print(f"[TELEMETRY-DB] Failed to log insight: {e}")
        return None


def log_error(
    error_type: str,
    description: str,
    symbol: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    lesson_learned: Optional[str] = None
) -> Optional[int]:
    """Log an error or mistake for learning."""
    now = time.time()
    dt = datetime.fromtimestamp(now)
    date_str = dt.strftime("%Y-%m-%d")
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            if _is_postgres():
                cursor.execute("""
                    INSERT INTO telemetry_errors (
                        timestamp, date, error_type, symbol, description,
                        context, lesson_learned
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    now, date_str, error_type, symbol, description,
                    json.dumps(context) if context else None, lesson_learned
                ))
                result = cursor.fetchone()
                return result[0] if result else None
            else:
                cursor.execute("""
                    INSERT INTO errors (
                        timestamp, date, error_type, symbol, description,
                        context, lesson_learned
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    now, date_str, error_type, symbol, description,
                    json.dumps(context) if context else None, lesson_learned
                ))
                return cursor.lastrowid
    except Exception as e:
        print(f"[TELEMETRY-DB] Failed to log error: {e}")
        return None


def log_conversation(
    user_message: str,
    bot_response: str,
    intent: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """Log a conversation for context learning."""
    now = time.time()
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            if _is_postgres():
                cursor.execute("""
                    INSERT INTO conversations (
                        timestamp, user_message, bot_response, context
                    ) VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (
                    now, user_message, bot_response,
                    json.dumps(context) if context else None
                ))
                result = cursor.fetchone()
                return result[0] if result else None
            else:
                cursor.execute("""
                    INSERT INTO conversations (
                        timestamp, user_message, bot_response, intent, context
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    now, user_message, bot_response, intent,
                    json.dumps(context) if context else None
                ))
                return cursor.lastrowid
    except Exception as e:
        print(f"[TELEMETRY-DB] Failed to log conversation: {e}")
        return None


def get_recent_trades(symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Get recent trades, optionally filtered by symbol."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            if _is_postgres():
                table = "telemetry_trades"
                if symbol:
                    cursor.execute(f"""
                        SELECT * FROM {table} 
                        WHERE symbol = %s 
                        ORDER BY timestamp DESC 
                        LIMIT %s
                    """, (symbol, limit))
                else:
                    cursor.execute(f"""
                        SELECT * FROM {table} 
                        ORDER BY timestamp DESC 
                        LIMIT %s
                    """, (limit,))
                
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
            else:
                if symbol:
                    cursor.execute("""
                        SELECT * FROM trades 
                        WHERE symbol = ? 
                        ORDER BY timestamp DESC 
                        LIMIT ?
                    """, (symbol, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM trades 
                        ORDER BY timestamp DESC 
                        LIMIT ?
                    """, (limit,))
                
                return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"[TELEMETRY-DB] Failed to get recent trades: {e}")
        return []


def get_trading_stats(symbol: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
    """Get trading statistics for analysis."""
    cutoff = time.time() - (days * 24 * 60 * 60)
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            if _is_postgres():
                table = "telemetry_trades"
                if symbol:
                    cursor.execute(f"""
                        SELECT 
                            COUNT(*) as total_trades,
                            SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) as buys,
                            SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) as sells,
                            AVG(usd_amount) as avg_trade_size
                        FROM {table}
                        WHERE symbol = %s AND timestamp > %s
                    """, (symbol, cutoff))
                else:
                    cursor.execute(f"""
                        SELECT 
                            COUNT(*) as total_trades,
                            SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) as buys,
                            SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) as sells,
                            AVG(usd_amount) as avg_trade_size,
                            COUNT(DISTINCT symbol) as symbols_traded
                        FROM {table}
                        WHERE timestamp > %s
                    """, (cutoff,))
                
                row = cursor.fetchone()
                if row:
                    columns = [desc[0] for desc in cursor.description]
                    return dict(zip(columns, row))
                return {}
            else:
                if symbol:
                    cursor.execute("""
                        SELECT 
                            COUNT(*) as total_trades,
                            SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) as buys,
                            SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) as sells,
                            AVG(usd_amount) as avg_trade_size
                        FROM trades
                        WHERE symbol = ? AND timestamp > ?
                    """, (symbol, cutoff))
                else:
                    cursor.execute("""
                        SELECT 
                            COUNT(*) as total_trades,
                            SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) as buys,
                            SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) as sells,
                            AVG(usd_amount) as avg_trade_size,
                            COUNT(DISTINCT symbol) as symbols_traded
                        FROM trades
                        WHERE timestamp > ?
                    """, (cutoff,))
                
                row = cursor.fetchone()
                return dict(row) if row else {}
    except Exception as e:
        print(f"[TELEMETRY-DB] Failed to get trading stats: {e}")
        return {}


def get_trading_stats_24h() -> Dict[str, Any]:
    """
    Get REAL trading statistics for last 24 hours with SOURCE breakdown.
    """
    cutoff = time.time() - (24 * 60 * 60)
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            if _is_postgres():
                table = "telemetry_trades"
                cursor.execute(f"""
                    SELECT COUNT(*) as total_trades FROM {table} WHERE timestamp > %s
                """, (cutoff,))
                result = cursor.fetchone()
                total = result[0] if result else 0
                
                cursor.execute(f"""
                    SELECT source, COUNT(*) as count
                    FROM {table}
                    WHERE timestamp > %s
                    GROUP BY source
                """, (cutoff,))
                
                source_counts = {}
                for row in cursor.fetchall():
                    source_counts[row[0]] = row[1]
                
                cursor.execute(f"""
                    SELECT symbol, side, price, quantity, usd_amount,
                           order_id, source, timestamp, reason
                    FROM {table}
                    WHERE timestamp > %s
                    ORDER BY timestamp DESC
                """, (cutoff,))
                
                columns = [desc[0] for desc in cursor.description]
                trades = [dict(zip(columns, row)) for row in cursor.fetchall()]
            else:
                cursor.execute("""
                    SELECT COUNT(*) as total_trades FROM trades WHERE timestamp > ?
                """, (cutoff,))
                total = cursor.fetchone()['total_trades']
                
                cursor.execute("""
                    SELECT source, COUNT(*) as count
                    FROM trades
                    WHERE timestamp > ?
                    GROUP BY source
                """, (cutoff,))
                
                source_counts = {row['source']: row['count'] for row in cursor.fetchall()}
                
                cursor.execute("""
                    SELECT symbol, side, price, quantity, usd_amount,
                           order_id, source, timestamp, reason
                    FROM trades
                    WHERE timestamp > ?
                    ORDER BY timestamp DESC
                """, (cutoff,))
                
                trades = [dict(row) for row in cursor.fetchall()]
            
            return {
                'total_trades_24h': total,
                'autopilot_trades_24h': source_counts.get('autopilot', 0),
                'command_trades_24h': source_counts.get('command', 0),
                'force_test_trades_24h': source_counts.get('force_test', 0),
                'unknown_trades_24h': source_counts.get('unknown', 0),
                'trades': trades
            }
    except Exception as e:
        print(f"[TELEMETRY-DB] Failed to get 24h stats: {e}")
        return {
            'total_trades_24h': 0,
            'autopilot_trades_24h': 0,
            'command_trades_24h': 0,
            'force_test_trades_24h': 0,
            'unknown_trades_24h': 0,
            'trades': []
        }


init_db()
