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
def log_evaluation(
    symbol: str,
    decision: str,
    reason: str,
    trading_mode: str,
    price: Optional[float] = None,
    rsi: Optional[float] = None,
    atr: Optional[float] = None,
    volume: Optional[float] = None,
    position_size: float = 0.0,
    error_message: Optional[str] = None,
    regime: Optional[str] = None,
    adx: Optional[float] = None,
    bb_position: Optional[float] = None,
    sma20: Optional[float] = None,
    sma50: Optional[float] = None,
    candle_timestamp: Optional[str] = None,
    current_position_qty: float = 0.0,
    current_position_value: float = 0.0
):
    """Log a single evaluation to the database."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        timestamp_utc = datetime.utcnow().isoformat()
        cursor.execute("""
            INSERT INTO evaluations (
                timestamp_utc, symbol, price, rsi, atr, volume,
                decision, reason, position_size, error_message, trading_mode,
                regime, adx, bb_position, sma20, sma50,
                candle_timestamp, current_position_qty, current_position_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp_utc, symbol, price, rsi, atr, volume,
            decision, reason, position_size, error_message, trading_mode,
            regime, adx, bb_position, sma20, sma50,
            candle_timestamp, current_position_qty, current_position_value
        ))
        cursor.execute("""
            UPDATE heartbeat
            SET last_evaluation_ts_utc = ?, evaluation_count = evaluation_count + 1
            WHERE id = 1
        """, (timestamp_utc,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[EVAL-LOG] Failed to log evaluation: {e}")


def log_order_execution(
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    order_id: str,
    trading_mode: str,
    source: str,
    extra_info: str = "",
    order_type: str = "entry",
    parent_order_id: Optional[str] = None
):
    """Log a successfully executed order for later verification."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        timestamp_utc = datetime.utcnow().isoformat()
        cursor.execute("SELECT id FROM executed_orders WHERE order_id = ?", (order_id,))
        if cursor.fetchone():
            logger.debug(f"[ORDER-LOG] Order {order_id} already logged, skipping")
            conn.close()
            return
        cursor.execute("""
            INSERT INTO executed_orders (
                timestamp_utc, symbol, side, quantity, entry_price,
                order_id, trading_mode, source, extra_info,
                order_type, parent_order_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp_utc, symbol, side, quantity, entry_price,
            order_id, trading_mode, source, extra_info,
            order_type, parent_order_id
        ))
        conn.commit()
        conn.close()
        logger.info(
            f"[ORDER-EXECUTED] {symbol} {side.upper()} {quantity:.6f} @ ${entry_price:.2f} "
            f"(order_id={order_id}, type={order_type}, mode={trading_mode}, source={source})"
        )
    except Exception as e:
        logger.error(f"[ORDER-LOG] CRITICAL: Failed to log executed order: {e}")
        import sys
        print(f"❌ CRITICAL: Failed to log order execution: {e}", file=sys.stderr)


def get_executed_orders(limit: int = 50, symbol: Optional[str] = None, since_hours: int = 24) -> List[Dict[str, Any]]:
    """Get executed orders from the database (TRUTH VERIFICATION)."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cutoff_time = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
        if symbol:
            cursor.execute("""
                SELECT *
                FROM executed_orders
                WHERE symbol = ? AND timestamp_utc >= ?
                ORDER BY timestamp_utc DESC
                LIMIT ?
            """, (symbol, cutoff_time, limit))
        else:
            cursor.execute("""
                SELECT *
                FROM executed_orders
                WHERE timestamp_utc >= ?
                ORDER BY timestamp_utc DESC
                LIMIT ?
            """, (cutoff_time, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"[ORDER-LOG] Failed to get executed orders: {e}")
        return []


def get_last_evaluations(limit: int = 20, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get the last N evaluations, optionally filtered by symbol."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        if symbol:
            cursor.execute("""
                SELECT 
                    timestamp_utc, symbol, price, rsi, atr, volume,
                    decision, reason, position_size, trading_mode, regime, adx
                FROM evaluations
                WHERE symbol = ?
                ORDER BY timestamp_utc DESC
                LIMIT ?
            """, (symbol, limit))
        else:
            cursor.execute("""
                SELECT 
                    timestamp_utc, symbol, price, rsi, atr, volume,
                    decision, reason, position_size, trading_mode, regime, adx
                FROM evaluations
                ORDER BY timestamp_utc DESC
                LIMIT ?
            """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"[EVAL-LOG] Failed to get last evaluations: {e}")
        return []


def get_today_summary(symbol: Optional[str] = None) -> Dict[str, Any]:
    """Get summary of today's evaluations."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        if symbol:
            cursor.execute("""
                SELECT COUNT(*) as total
                FROM evaluations
                WHERE timestamp_utc >= ? AND symbol = ?
            """, (today_start, symbol))
        else:
            cursor.execute("""
                SELECT COUNT(*) as total
                FROM evaluations
                WHERE timestamp_utc >= ?
            """, (today_start,))
        total = cursor.fetchone()['total']
        if symbol:
            cursor.execute("""
                SELECT decision, COUNT(*) as count
                FROM evaluations
                WHERE timestamp_utc >= ? AND symbol = ?
                GROUP BY decision
                ORDER BY count DESC
            """, (today_start, symbol))
        else:
            cursor.execute("""
                SELECT decision, COUNT(*) as count
                FROM evaluations
                WHERE timestamp_utc >= ?
                GROUP BY decision
                ORDER BY count DESC
            """, (today_start,))
        decision_counts = {row['decision']: row['count'] for row in cursor.fetchall()}
        if symbol:
            cursor.execute("""
                SELECT reason, COUNT(*) as count
                FROM evaluations
                WHERE timestamp_utc >= ? AND decision = 'NO_TRADE' AND symbol = ?
                GROUP BY reason
                ORDER BY count DESC
            """, (today_start, symbol))
        else:
            cursor.execute("""
                SELECT reason, COUNT(*) as count
                FROM evaluations
                WHERE timestamp_utc >= ? AND decision = 'NO_TRADE'
                GROUP BY reason
                ORDER BY count DESC
            """, (today_start,))
        no_trade_reasons = [(row['reason'], row['count']) for row in cursor.fetchall()]
        conn.close()
        return {
            'total_evaluations': total,
            'decision_counts': decision_counts,
            'no_trade_reasons': no_trade_reasons,
            'symbol': symbol,
            'date': datetime.utcnow().strftime('%Y-%m-%d')
        }
    except Exception as e:
        logger.error(f"[EVAL-LOG] Failed to get today summary: {e}")
        return {
            'total_evaluations': 0,
            'decision_counts': {},
            'no_trade_reasons': [],
            'symbol': symbol,
            'date': datetime.utcnow().strftime('%Y-%m-%d'),
            'error': str(e)
        }


def explain_why_no_trades_today(symbol: Optional[str] = None) -> str:
    """Generate a data-backed explanation for why no trades occurred today."""
    try:
        summary = get_today_summary(symbol)
        total = summary['total_evaluations']
        decision_counts = summary['decision_counts']
        no_trade_reasons = summary['no_trade_reasons']
        if total == 0:
            return f"I haven't completed any evaluations today yet. The scheduler may not be running."
        buy_count = decision_counts.get('BUY', 0)
        sell_count = decision_counts.get('SELL', 0)
        no_trade_count = decision_counts.get('NO_TRADE', 0)
        error_count = decision_counts.get('ERROR', 0)
        symbol_text = f"{symbol}" if symbol else "all symbols"
        if buy_count > 0 or sell_count > 0:
            return f"I actually DID trade today on {symbol_text}! {buy_count} BUY signals, {sell_count} SELL signals out of {total} evaluations."
        if error_count == total:
            return f"All {total} evaluations today resulted in ERRORS. Please check the error logs immediately."
        if no_trade_count == 0:
            return f"I evaluated {symbol_text} {total} times today, but the data is unclear. Please check logs."
        explanation = f"I evaluated {symbol_text} {total} times today. All {no_trade_count} decisions were NO_TRADE.\n"
        if no_trade_reasons:
            explanation += "\nBreakdown of reasons:\n"
            for reason, count in no_trade_reasons[:5]:
                explanation += f"• {count}x: {reason}\n"
        if error_count > 0:
            explanation += f"\n⚠️ {error_count} evaluations had errors.\n"
        explanation += "\nThat's why I did not open any positions today."
        return explanation
    except Exception as e:
        logger.error(f"[EVAL-LOG] Failed to explain no trades: {e}")
        return f"Error generating explanation: {e}"


def get_heartbeat_status() -> Dict[str, Any]:
    """Check heartbeat status and detect stale loops."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT last_evaluation_ts_utc, evaluation_count FROM heartbeat WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        if not row:
            return {
                'status': 'unknown',
                'message': 'Heartbeat not initialized',
                'is_stale': True
            }
        last_ts = datetime.fromisoformat(row['last_evaluation_ts_utc'])
        now = datetime.utcnow()
        minutes_since = (now - last_ts).total_seconds() / 60
        is_stale = minutes_since > 10
        return {
            'status': 'stale' if is_stale else 'healthy',
            'last_evaluation_ts_utc': row['last_evaluation_ts_utc'],
            'minutes_since_last_evaluation': round(minutes_since, 1),
            'total_evaluations': row['evaluation_count'],
            'is_stale': is_stale,
            'message': f"⚠️ Evaluation loop has not run for {round(minutes_since, 1)} minutes. This indicates a scheduler problem." if is_stale else f"✅ Healthy - last run {round(minutes_since, 1)} minutes ago"
        }
    except Exception as e:
        logger.error(f"[EVAL-LOG] Failed to get heartbeat: {e}")
        return {
            'status': 'error',
            'message': f'Error checking heartbeat: {e}',
            'is_stale': True
        }
