"""
Trading Telemetry Database - Stores all trades, decisions, and learning data.
This is the bot's long-term memory for self-improvement.
"""
import sqlite3
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from contextlib import contextmanager


DB_PATH = Path(__file__).parent / "trading_memory.db"


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if they don't exist."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Trades table - records of executed trades
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
                take_profit REAL
            )
        """)
        
        # Migration: Add new columns if they don't exist (SQLite safe)
        # Batch 1: Previously added columns (Nov 12-13, 2025)
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN mode TEXT DEFAULT 'live'")
        except Exception:
            pass  # Column already exists
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN stop_loss REAL")
        except Exception:
            pass  # Column already exists
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN take_profit REAL")
        except Exception:
            pass  # Column already exists
        
        # Batch 2: Complete trade lifecycle fields (Nov 13, 2025)
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN trade_id TEXT")  # Optional external trade ID
        except Exception:
            pass
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN strategy TEXT")  # Regime/strategy used
        except Exception:
            pass
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN entry_price REAL")  # Explicit entry price
        except Exception:
            pass
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN exit_price REAL")  # Exit price (for closed trades)
        except Exception:
            pass
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN position_size REAL")  # Position size (quantity)
        except Exception:
            pass
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN initial_risk REAL")  # Risk at entry (USD)
        except Exception:
            pass
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN r_multiple REAL")  # P&L / initial_risk
        except Exception:
            pass
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN open_timestamp REAL")  # Trade open time
        except Exception:
            pass
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN close_timestamp REAL")  # Trade close time
        except Exception:
            pass
        
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN pnl REAL")  # Realized P&L
        except Exception:
            pass
        
        # Decisions table - all trading decisions (including holds)
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
        
        # Performance snapshots - daily/hourly equity tracking
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
        
        # Strategy insights - learned patterns and what works
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
        
        # Error log - mistakes and lessons learned
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
        
        # Conversation log - chat history for context
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
        
        # Create indexes for faster queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_performance_date ON performance(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_insights_category ON insights(category)")


def log_trade(
    symbol: str,
    side: str,
    action: str,
    quantity: Optional[float] = None,
    price: Optional[float] = None,
    usd_amount: Optional[float] = None,
    order_id: Optional[str] = None,
    reason: Optional[str] = None,
    source: str = "autopilot",
    metadata: Optional[Dict[str, Any]] = None,
    mode: str = "live",
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    # New lifecycle fields
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
    Log an executed trade.
    
    Args:
        symbol: Trading symbol (e.g., 'BTC/USD')
        side: 'buy' or 'sell'
        action: 'open' or 'close'
        quantity: Trade quantity
        price: Execution price
        usd_amount: USD value of trade
        order_id: Exchange order ID
        reason: Reason for trade (e.g., 'entry', 'stop_loss', 'take_profit')
        source: Source of trade ('autopilot', 'manual', etc.)
        metadata: Additional trade metadata
        mode: Trading mode ('paper' or 'live')
        stop_loss: Stop-loss price (for entry trades)
        take_profit: Take-profit price (for entry trades)
    
    Returns:
        Trade ID or None if error
    """
    now = time.time()
    dt = datetime.fromtimestamp(now)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                timestamp, date, symbol, side, action, quantity, price,
                usd_amount, order_id, reason, source, metadata, mode, stop_loss, take_profit,
                trade_id, strategy, entry_price, exit_price, position_size,
                initial_risk, r_multiple, open_timestamp, close_timestamp, pnl
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now,
            dt.strftime("%Y-%m-%d"),
            symbol,
            side,
            action,
            quantity,
            price,
            usd_amount,
            order_id,
            reason,
            source,
            json.dumps(metadata) if metadata else None,
            mode,
            stop_loss,
            take_profit,
            # New lifecycle fields
            trade_id,
            strategy,
            entry_price,
            exit_price,
            position_size,
            initial_risk,
            r_multiple,
            open_timestamp,
            close_timestamp,
            pnl
        ))
        return cursor.lastrowid


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
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO decisions (
                timestamp, date, symbol, action, reason, price, edge_pct,
                atr, position_qty, equity_usd, executed, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now,
            dt.strftime("%Y-%m-%d"),
            symbol,
            action,
            reason,
            price,
            edge_pct,
            atr,
            position_qty,
            equity_usd,
            1 if executed else 0,
            json.dumps(metadata) if metadata else None
        ))
        return cursor.lastrowid


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
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO performance (
                timestamp, date, hour, equity_usd, equity_change_usd,
                open_positions, symbols_traded, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now,
            dt.strftime("%Y-%m-%d"),
            dt.hour,
            equity_usd,
            equity_change_usd,
            json.dumps(open_positions) if open_positions else None,
            json.dumps(symbols_traded) if symbols_traded else None,
            json.dumps(metadata) if metadata else None
        ))
        return cursor.lastrowid


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
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO insights (
                created_at, category, insight_type, symbol, description,
                confidence, supporting_data, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now,
            category,
            insight_type,
            symbol,
            description,
            confidence,
            json.dumps(supporting_data) if supporting_data else None,
            json.dumps(metadata) if metadata else None
        ))
        return cursor.lastrowid


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
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO errors (
                timestamp, date, error_type, symbol, description,
                context, lesson_learned
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            now,
            dt.strftime("%Y-%m-%d"),
            error_type,
            symbol,
            description,
            json.dumps(context) if context else None,
            lesson_learned
        ))
        return cursor.lastrowid


def log_conversation(
    user_message: str,
    bot_response: str,
    intent: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """Log a conversation for context learning."""
    now = time.time()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO conversations (
                timestamp, user_message, bot_response, intent, context
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            now,
            user_message,
            bot_response,
            intent,
            json.dumps(context) if context else None
        ))
        return cursor.lastrowid


def get_recent_trades(symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Get recent trades, optionally filtered by symbol."""
    with get_db() as conn:
        cursor = conn.cursor()
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


def get_trading_stats(symbol: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
    """Get trading statistics for analysis."""
    cutoff = time.time() - (days * 24 * 60 * 60)
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        if symbol:
            # Symbol-specific stats
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
            # Overall stats
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


# Initialize database on import
init_db()
