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
    
    conn.commit()
    conn.close()
    logger.info(f"[EVAL-LOG] Database initialized at {DB_PATH}")


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
    """
    Log a single evaluation to the database.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        decision: "BUY", "SELL_ALL", "HOLD", "LONG", "NO_TRADE", "SKIP", "ERROR"
        reason: Short explanation (e.g., "RSI out of range")
        trading_mode: "PAPER" or "LIVE"
        price: Current price
        rsi: RSI value
        atr: ATR value
        volume: Volume value
        position_size: Size if trade executed, 0 otherwise
        error_message: Error details if decision == "ERROR"
        regime: Market regime
        adx: ADX value
        bb_position: Bollinger Band position (0-1)
        sma20: SMA20 value
        sma50: SMA50 value
        candle_timestamp: Candle timestamp for scheduler verification
        current_position_qty: Current open position quantity
        current_position_value: Current open position value in USD
    """
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
            SET last_evaluation_ts_utc = ?,
                evaluation_count = evaluation_count + 1
            WHERE id = 1
        """, (timestamp_utc,))
        
        conn.commit()
        conn.close()
        
        logger.debug(f"[EVAL-LOG] {symbol} {decision}: {reason}")
        
    except Exception as e:
        logger.error(f"[EVAL-LOG] Failed to log evaluation: {e}")


def get_last_evaluations(limit: int = 20, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get the last N evaluations, optionally filtered by symbol.
    
    Args:
        limit: Max number of rows to return
        symbol: Filter by symbol if provided
    
    Returns:
        List of evaluation dictionaries
    """
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
    """
    Get summary of today's evaluations.
    
    Args:
        symbol: Filter by symbol if provided
    
    Returns:
        Dictionary with counts and reason breakdown
    """
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
            """, (today_start, symbol))
        else:
            cursor.execute("""
                SELECT decision, COUNT(*) as count
                FROM evaluations
                WHERE timestamp_utc >= ?
                GROUP BY decision
            """)
        
        decision_counts = {row['decision']: row['count'] for row in cursor.fetchall()}
        
        if symbol:
            cursor.execute("""
                SELECT reason, COUNT(*) as count
                FROM evaluations
                WHERE timestamp_utc >= ? AND symbol = ? AND decision = 'NO_TRADE'
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
    """
    Generate a data-backed explanation for why no trades occurred today.
    
    Args:
        symbol: Filter by symbol if provided
    
    Returns:
        Human-readable explanation with counts and reasons
    """
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
    """
    Check heartbeat status and detect stale loops.
    
    Returns:
        Dictionary with last evaluation time and staleness warning
    """
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


init_evaluation_log_db()
