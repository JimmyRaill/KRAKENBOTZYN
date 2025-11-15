"""
Evaluation Log - Full Transparency Layer for Trading Bot
Records every 5-minute evaluation with indicators, decisions, and reasons.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path
from loguru import logger

DB_PATH = Path("evaluation_log.db")


def _get_connection() -> sqlite3.Connection:
    """Get database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_evaluation_log_db():
    """Initialize evaluation log database with schema."""
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL,
            rsi REAL,
            atr REAL,
            volume REAL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            position_size REAL DEFAULT 0,
            error_message TEXT,
            trading_mode TEXT NOT NULL,
            regime TEXT,
            adx REAL,
            bb_position REAL,
            sma20 REAL,
            sma50 REAL,
            candle_timestamp TEXT,
            current_position_qty REAL DEFAULT 0,
            current_position_value REAL DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp 
        ON evaluations(timestamp_utc DESC)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_symbol_timestamp 
        ON evaluations(symbol, timestamp_utc DESC)
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS heartbeat (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_evaluation_ts_utc TEXT NOT NULL,
            evaluation_count INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        INSERT OR IGNORE INTO heartbeat (id, last_evaluation_ts_utc, evaluation_count)
        VALUES (1, ?, 0)
    """, (datetime.utcnow().isoformat(),))
    
    # Create executed_orders table for TRUTH VERIFICATION
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS executed_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            order_id TEXT NOT NULL,
            trading_mode TEXT NOT NULL,
            source TEXT NOT NULL,
            extra_info TEXT DEFAULT '',
            order_type TEXT DEFAULT 'entry',
            parent_order_id TEXT DEFAULT NULL,
            fill_status TEXT DEFAULT 'filled'
        )
    """)
    
    # Migrate existing executed_orders schema if needed
    cursor.execute("PRAGMA table_info(executed_orders)")
    columns = {row[1] for row in cursor.fetchall()}
    
    if 'order_type' not in columns:
        cursor.execute("ALTER TABLE executed_orders ADD COLUMN order_type TEXT DEFAULT 'entry'")
    if 'parent_order_id' not in columns:
        cursor.execute("ALTER TABLE executed_orders ADD COLUMN parent_order_id TEXT DEFAULT NULL")
    if 'fill_status' not in columns:
        cursor.execute("ALTER TABLE executed_orders ADD COLUMN fill_status TEXT DEFAULT 'filled'")
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_executed_orders_timestamp 
        ON executed_orders(timestamp_utc DESC)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_executed_orders_symbol 
        ON executed_orders(symbol, timestamp_utc DESC)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_executed_orders_order_id 
        ON executed_orders(order_id)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_executed_orders_parent 
        ON executed_orders(parent_order_id)
    """)
    
    # Create pending_child_orders table for TP/SL monitoring
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_child_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_created_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            order_id TEXT NOT NULL UNIQUE,
            order_type TEXT NOT NULL,
            parent_order_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            limit_price REAL,
            trading_mode TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            last_checked_utc TEXT
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pending_orders_status 
        ON pending_child_orders(status, trading_mode)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pending_orders_order_id 
        ON pending_child_orders(order_id)
    """)
    
    # Create reconciliation_log table for tracking last seen timestamps
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_log (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_reconciliation_utc TEXT NOT NULL,
            last_seen_trade_timestamp INTEGER DEFAULT 0,
            reconciliation_count INTEGER DEFAULT 0,
            fills_logged INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        INSERT OR IGNORE INTO reconciliation_log (id, last_reconciliation_utc)
        VALUES (1, ?)
    """, (datetime.utcnow().isoformat(),))
    
    conn.commit()
    conn.close()
    logger.info(f"[EVAL-LOG] Database initialized at {DB_PATH}")


def register_pending_child_order(
    symbol: str,
    order_id: str,
    order_type: str,  # "tp" or "sl"
    parent_order_id: str,
    side: str,
    quantity: float,
    limit_price: Optional[float],
    trading_mode: str
):
    """
    Register a TP or SL order for monitoring.
    
    Args:
        symbol: Trading pair
        order_id: The TP/SL order ID from exchange
        order_type: "tp" or "sl"
        parent_order_id: The entry order ID
        side: "buy" or "sell"
        quantity: Order size
        limit_price: Limit price if applicable
        trading_mode: "live" or "paper"
    """
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        timestamp_utc = datetime.utcnow().isoformat()
        
        cursor.execute("""
            INSERT OR REPLACE INTO pending_child_orders (
                timestamp_created_utc, symbol, order_id, order_type,
                parent_order_id, side, quantity, limit_price, trading_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp_utc, symbol, order_id, order_type,
            parent_order_id, side, quantity, limit_price, trading_mode
        ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"[PENDING-ORDER] Registered {order_type.upper()} {symbol} order_id={order_id} parent={parent_order_id}")
        
    except Exception as e:
        logger.error(f"[PENDING-ORDER] Failed to register: {e}")


def get_pending_child_orders(trading_mode: Optional[str] = None, status: str = "pending") -> List[Dict[str, Any]]:
    """
    Get pending TP/SL orders for monitoring.
    
    Args:
        trading_mode: Filter by mode if provided
        status: Filter by status (default: "pending")
    
    Returns:
        List of pending order dictionaries
    """
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        if trading_mode:
            cursor.execute("""
                SELECT * FROM pending_child_orders
                WHERE status = ? AND trading_mode = ?
                ORDER BY timestamp_created_utc DESC
            """, (status, trading_mode))
        else:
            cursor.execute("""
                SELECT * FROM pending_child_orders
                WHERE status = ?
                ORDER BY timestamp_created_utc DESC
            """, (status,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
        
    except Exception as e:
        logger.error(f"[PENDING-ORDER] Failed to get pending orders: {e}")
        return []


def mark_pending_order_filled(order_id: str):
    """Mark a pending order as filled (so it's not checked again)."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE pending_child_orders
            SET status = 'filled', last_checked_utc = ?
            WHERE order_id = ?
        """, (datetime.utcnow().isoformat(), order_id))
        
        conn.commit()
        conn.close()
        
        logger.debug(f"[PENDING-ORDER] Marked {order_id} as filled")
        
    except Exception as e:
        logger.error(f"[PENDING-ORDER] Failed to mark filled: {e}")


def update_reconciliation_stats(fills_logged: int = 0):
    """Update reconciliation statistics."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE reconciliation_log
            SET last_reconciliation_utc = ?,
                reconciliation_count = reconciliation_count + 1,
                fills_logged = fills_logged + ?
            WHERE id = 1
        """, (datetime.utcnow().isoformat(), fills_logged))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"[RECONCILIATION] Failed to update stats: {e}")


def get_reconciliation_stats() -> Dict[str, Any]:
    """Get reconciliation statistics."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM reconciliation_log WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else {}
        
    except Exception as e:
        logger.error(f"[RECONCILIATION] Failed to get stats: {e}")
        return {}


init_evaluation_log_db()
