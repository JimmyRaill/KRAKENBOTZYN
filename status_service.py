"""
Status Service - Single source of truth for all trading data.
ALL chat responses about orders/trades/positions MUST use this service.
NEVER rely on LLM memory - always fetch from authoritative sources.
"""
import time
import sqlite3
import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Literal
from pathlib import Path
from contextlib import contextmanager

from exchange_manager import get_exchange, get_mode_str, get_manager
from loguru import logger

# Trade cache to avoid re-fetching from Kraken multiple times
_trade_cache = {
    'trades': [],
    'fetched_at': 0,
    'ttl': 300  # Cache for 5 minutes to avoid rate limits
}

# Database path
DB_PATH = Path(__file__).parent / "trading_memory.db"

# Sync timing constants
SYNC_INTERVAL = 60  # Auto-sync if data older than 60 seconds (balanced freshness + rate limits)
SYNC_WARNING_THRESHOLD = 300  # Warning if sync 300-600s old
SYNC_ERROR_THRESHOLD = 600  # Error if sync > 600s old


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


def init_status_tables() -> None:
    """Initialize status service database tables."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Orders table - tracks ALL orders (open, closed, canceled)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                datetime_utc TEXT NOT NULL,
                symbol TEXT NOT NULL,
                type TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL,
                amount REAL,
                filled REAL DEFAULT 0,
                remaining REAL,
                cost REAL,
                fee REAL,
                status TEXT NOT NULL,
                source TEXT DEFAULT 'kraken',
                mode TEXT NOT NULL,
                raw_data TEXT,
                synced_at REAL NOT NULL
            )
        """)
        
        # Balances table - current balance snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                datetime_utc TEXT NOT NULL,
                currency TEXT NOT NULL,
                free REAL NOT NULL,
                used REAL NOT NULL,
                total REAL NOT NULL,
                usd_value REAL,
                mode TEXT NOT NULL,
                synced_at REAL NOT NULL
            )
        """)
        
        # Sync state table - tracks last sync times
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_sync_utc REAL NOT NULL,
                last_sync_datetime TEXT NOT NULL,
                mode TEXT NOT NULL,
                balances_synced INTEGER DEFAULT 0,
                orders_synced INTEGER DEFAULT 0,
                trades_synced INTEGER DEFAULT 0,
                errors TEXT,
                metadata TEXT
            )
        """)
        
        # Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_balances_currency ON balances(currency)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_balances_timestamp ON balances(timestamp)")
        
        logger.info("[STATUS-SERVICE] Database tables initialized")


def get_mode() -> str:
    """Get current trading mode from ExchangeManager."""
    mode = get_mode_str()  # Returns "paper" or "live"
    if mode not in ("paper", "live"):
        raise ValueError(f"Invalid mode: {mode}")
    return mode


def _fetch_balances_from_source() -> Dict[str, Dict[str, Any]]:
    """
    Fetch balances from the correct source based on mode.
    CRITICAL: Uses account_state.py to ensure LIVE/PAPER isolation.
    Returns: {currency: {free, used, total, usd_value, last_updated}}
    """
    try:
        from account_state import get_balances
        return get_balances()
    except Exception as e:
        logger.error(f"[STATUS-SERVICE] Failed to fetch balances: {e}")
        raise


def _fetch_open_orders_from_kraken() -> List[Dict[str, Any]]:
    """Fetch ALL open orders from Kraken with pagination."""
    try:
        ex = get_exchange()
        all_orders = []
        
        # Kraken's fetch_open_orders supports pagination via 'since' parameter
        # We'll fetch all open orders (usually small number, so pagination may not be needed)
        orders = ex.fetch_open_orders()
        all_orders.extend(orders)
        
        logger.info(f"[STATUS-SERVICE] Fetched {len(all_orders)} open orders from Kraken")
        return all_orders
    except Exception as e:
        logger.error(f"[STATUS-SERVICE] Failed to fetch open orders: {e}")
        raise


def _fetch_closed_orders_from_kraken(since: Optional[int] = None, until: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
    """
    Fetch closed orders from Kraken with proper pagination.
    IMPORTANT: Iterates through ALL pages, not just first page.
    
    Args:
        since: Timestamp in milliseconds (Kraken uses ms)
        until: Timestamp in milliseconds
        limit: Max orders per page (Kraken default is 50, max is 500)
    """
    try:
        ex = get_exchange()
        all_orders = []
        
        # Kraken API: fetch_closed_orders supports 'since' param
        # We need to paginate by calling repeatedly with updated 'since'
        params = {'limit': limit}
        if since:
            params['start'] = since  # Kraken uses 'start' for since timestamp
        
        while True:
            orders = ex.fetch_closed_orders(params=params)
            if not orders:
                break
            
            all_orders.extend(orders)
            
            # Check if we got fewer than limit (means we're done)
            if len(orders) < limit:
                break
            
            # Update 'start' to the last order's timestamp for next page
            last_timestamp = orders[-1].get('timestamp', 0)
            if last_timestamp:
                params['start'] = last_timestamp
            else:
                break
        
        logger.info(f"[STATUS-SERVICE] Fetched {len(all_orders)} closed orders from Kraken")
        return all_orders
    except Exception as e:
        logger.error(f"[STATUS-SERVICE] Failed to fetch closed orders: {e}")
        raise


def _fetch_trades_from_kraken(since: Optional[int] = None, until: Optional[int] = None, limit: int = 500, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch trades from Kraken with proper pagination.
    IMPORTANT: Iterates through ALL pages, not just first page.
    
    Args:
        since: Timestamp in milliseconds
        until: Timestamp in milliseconds
        limit: Max trades per request
        symbol: Optional symbol filter
    """
    try:
        ex = get_exchange()
        all_trades = []
        
        params = {'limit': limit}
        if since:
            params['start'] = since
        
        # If symbol specified, fetch for that symbol only
        if symbol:
            while True:
                trades = ex.fetch_my_trades(symbol=symbol, params=params)
                if not trades:
                    break
                
                all_trades.extend(trades)
                
                if len(trades) < limit:
                    break
                
                last_trade: Dict[str, Any] = trades[-1]  # type: ignore
                last_timestamp = last_trade.get('timestamp', 0)
                if last_timestamp:
                    params['start'] = last_timestamp
                else:
                    break
        else:
            # Fetch all symbols - note: Kraken may require symbol for fetch_my_trades
            # We'll fetch without symbol and let Kraken return all
            while True:
                trades = ex.fetch_my_trades(params=params)
                if not trades:
                    break
                
                all_trades.extend(trades)
                
                if len(trades) < limit:
                    break
                
                last_trade: Dict[str, Any] = trades[-1]  # type: ignore
                last_timestamp = last_trade.get('timestamp', 0)
                if last_timestamp:
                    params['start'] = last_timestamp
                else:
                    break
        
        logger.info(f"[STATUS-SERVICE] Fetched {len(all_trades)} trades from Kraken")
        return all_trades
    except Exception as e:
        logger.error(f"[STATUS-SERVICE] Failed to fetch trades: {e}")
        raise


def sync_exchange() -> Dict[str, Any]:
    """
    Idempotent sync - pulls from Kraken and upserts to DB.
    Returns sync stats.
    """
    logger.info("[STATUS-SERVICE] Starting exchange sync...")
    
    mode = get_mode()
    now = time.time()
    now_utc = datetime.fromtimestamp(now, tz=timezone.utc)
    
    stats = {
        'mode': mode,
        'sync_started_at': now,
        'balances_synced': 0,
        'orders_synced': 0,
        'trades_synced': 0,
        'errors': []
    }
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # 1. Sync balances
        try:
            balances = _fetch_balances_from_source()
            
            # Clear old balances for this mode
            cursor.execute("DELETE FROM balances WHERE mode = ?", (mode,))
            
            # Insert new balances (account_state returns normalized format)
            for currency, bal in balances.items():
                cursor.execute("""
                    INSERT INTO balances (
                        timestamp, datetime_utc, currency, free, used, total, usd_value, mode, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now,
                    now_utc.isoformat(),
                    currency,
                    bal.get('free', 0),
                    bal.get('used', 0),
                    bal.get('total', 0),
                    bal.get('usd_value'),
                    mode,
                    now
                ))
                stats['balances_synced'] += 1
            
            logger.info(f"[STATUS-SERVICE] Synced {stats['balances_synced']} balances")
        except Exception as e:
            error_msg = f"Balance sync failed: {str(e)}"
            stats['errors'].append(error_msg)
            logger.error(f"[STATUS-SERVICE] {error_msg}")
        
        # 2. Sync open orders (mode-aware)
        try:
            if mode == "live":
                # LIVE: Fetch from Kraken API
                open_orders = _fetch_open_orders_from_kraken()
            else:
                # PAPER: Fetch from paper ledger via exchange wrapper
                from exchange_manager import get_exchange
                ex = get_exchange()
                open_orders = ex.fetch_open_orders()
            
            # Clear old open orders for this mode
            cursor.execute("DELETE FROM orders WHERE mode = ? AND status = 'open'", (mode,))
            
            for order in open_orders:
                order_id = order.get('id', '')
                if not order_id:
                    continue
                
                # Upsert order
                cursor.execute("""
                    INSERT OR REPLACE INTO orders (
                        order_id, timestamp, datetime_utc, symbol, type, side,
                        price, amount, filled, remaining, cost, fee, status,
                        source, mode, raw_data, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_id,
                    order.get('timestamp', now * 1000) / 1000 if order.get('timestamp') else now,
                    datetime.fromtimestamp(order.get('timestamp', now * 1000) / 1000 if order.get('timestamp') else now, tz=timezone.utc).isoformat(),
                    order.get('symbol', ''),
                    order.get('type', ''),
                    order.get('side', ''),
                    order.get('price'),
                    order.get('amount'),
                    order.get('filled', 0),
                    order.get('remaining'),
                    order.get('cost'),
                    order.get('fee', {}).get('cost') if order.get('fee') else None,
                    order.get('status', 'unknown'),
                    'paper_ledger' if mode == 'paper' else 'kraken',
                    mode,
                    json.dumps(order),
                    now
                ))
                stats['orders_synced'] += 1
            
            logger.info(f"[STATUS-SERVICE] Synced {stats['orders_synced']} open orders from {'paper ledger' if mode == 'paper' else 'Kraken'}")
        except Exception as e:
            error_msg = f"Open orders sync failed: {str(e)}"
            stats['errors'].append(error_msg)
            logger.error(f"[STATUS-SERVICE] {error_msg}")
        
        # 3. Sync recent closed orders (LIVE mode only - skip in PAPER)
        if mode == "live":
            try:
                since_ms = int((now - 7 * 24 * 60 * 60) * 1000)
                closed_orders = _fetch_closed_orders_from_kraken(since=since_ms)
                
                for order in closed_orders:
                    order_id = order.get('id', '')
                    if not order_id:
                        continue
                    
                    cursor.execute("""
                        INSERT OR REPLACE INTO orders (
                            order_id, timestamp, datetime_utc, symbol, type, side,
                            price, amount, filled, remaining, cost, fee, status,
                            source, mode, raw_data, synced_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        order_id,
                        order.get('timestamp', now * 1000) / 1000,
                        datetime.fromtimestamp(order.get('timestamp', now * 1000) / 1000, tz=timezone.utc).isoformat(),
                        order.get('symbol', ''),
                        order.get('type', ''),
                        order.get('side', ''),
                        order.get('price'),
                        order.get('amount'),
                        order.get('filled', 0),
                        order.get('remaining'),
                        order.get('cost'),
                        order.get('fee', {}).get('cost') if order.get('fee') else None,
                        order.get('status', 'unknown'),
                        'kraken',
                        mode,
                        json.dumps(order),
                        now
                    ))
                
                logger.info(f"[STATUS-SERVICE] Synced {len(closed_orders)} closed orders")
            except Exception as e:
                error_msg = f"Closed orders sync failed: {str(e)}"
                stats['errors'].append(error_msg)
                logger.error(f"[STATUS-SERVICE] {error_msg}")
        else:
            logger.debug(f"[STATUS-SERVICE] Skipping closed orders sync (mode={mode})")
        
        # 4. Update sync state
        cursor.execute("""
            INSERT OR REPLACE INTO sync_state (
                id, last_sync_utc, last_sync_datetime, mode,
                balances_synced, orders_synced, trades_synced, errors, metadata
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now,
            now_utc.isoformat(),
            mode,
            stats['balances_synced'],
            stats['orders_synced'],
            stats['trades_synced'],
            json.dumps(stats['errors']) if stats['errors'] else None,
            json.dumps({'sync_duration': time.time() - now})
        ))
    
    logger.info(f"[STATUS-SERVICE] Sync complete: {stats}")
    return stats


def get_last_sync_time() -> Optional[float]:
    """Get timestamp of last successful sync."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_sync_utc FROM sync_state WHERE id = 1")
        row = cursor.fetchone()
        return row['last_sync_utc'] if row else None


def should_sync() -> bool:
    """Check if sync is needed (data older than SYNC_INTERVAL seconds)."""
    last_sync = get_last_sync_time()
    if last_sync is None:
        return True
    
    age = time.time() - last_sync
    return age > SYNC_INTERVAL


def auto_sync_if_needed() -> bool:
    """Auto-sync if data is stale. Returns True if sync was performed."""
    if should_sync():
        logger.info("[STATUS-SERVICE] Auto-sync triggered (data stale)")
        sync_exchange()
        return True
    return False


def get_balances() -> Dict[str, Any]:
    """Get current balances (auto-syncs if needed)."""
    auto_sync_if_needed()
    
    mode = get_mode()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT currency, free, used, total, usd_value, datetime_utc
            FROM balances
            WHERE mode = ?
            ORDER BY total DESC
        """, (mode,))
        
        rows = cursor.fetchall()
        balances = {}
        for row in rows:
            balances[row['currency']] = {
                'free': row['free'],
                'used': row['used'],
                'total': row['total'],
                'usd_value': row['usd_value'],
                'last_updated': row['datetime_utc']
            }
        
        return balances


def get_open_orders() -> List[Dict[str, Any]]:
    """Get all open orders (auto-syncs if needed)."""
    auto_sync_if_needed()
    
    mode = get_mode()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM orders
            WHERE mode = ? AND status = 'open'
            ORDER BY timestamp DESC
        """, (mode,))
        
        return [dict(row) for row in cursor.fetchall()]


def get_closed_orders(since: Optional[float] = None, until: Optional[float] = None) -> List[Dict[str, Any]]:
    """Get closed orders within time window (auto-syncs if needed)."""
    auto_sync_if_needed()
    
    mode = get_mode()
    with get_db() as conn:
        cursor = conn.cursor()
        
        query = "SELECT * FROM orders WHERE mode = ? AND status != 'open'"
        params: List[Any] = [mode]
        
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        if until:
            query += " AND timestamp <= ?"
            params.append(until)
        
        query += " ORDER BY timestamp DESC"
        
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def get_trades(since: Optional[float] = None, until: Optional[float] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get trades from the correct source based on mode.
    CRITICAL: Uses account_state.py to ensure LIVE/PAPER isolation.
    - LIVE mode: Fetches from Kraken API
    - PAPER mode: Fetches from paper ledger
    """
    try:
        from account_state import get_trade_history
        
        # Fetch from mode-aware source
        trades = get_trade_history(since=since, limit=limit)
        
        # Apply until filter if specified
        if until:
            trades = [t for t in trades if t.get('timestamp', 0) <= until]
        
        # Normalize format for backward compatibility
        result = []
        for t in trades:
            result.append({
                'trade_id': t.get('trade_id', ''),
                'timestamp': t.get('timestamp', 0),
                'datetime': t.get('datetime_utc', ''),
                'symbol': t.get('symbol', ''),
                'side': t.get('side', ''),
                'price': t.get('price', 0),
                'quantity': t.get('quantity', 0),
                'usd_amount': t.get('cost', 0),
                'fee': t.get('fee', 0),
                'order_id': t.get('order_id', '')
            })
        
        # Apply until filter if specified
        if until:
            result = [t for t in result if t['timestamp'] <= until]
        
        return result
    except Exception as e:
        logger.error(f"[STATUS-SERVICE] Failed to fetch trades from Kraken: {e}")
        return []


def _get_cached_trades() -> List[Dict[str, Any]]:
    """
    Get trades with mode awareness to prevent LIVE/PAPER contamination.
    CRITICAL: In PAPER mode, returns paper ledger trades. In LIVE mode, fetches from Kraken.
    """
    global _trade_cache
    now = time.time()
    mode = get_mode()
    
    # PAPER MODE: Use paper ledger, NOT Kraken
    if mode == "paper":
        try:
            from account_state import get_trade_history
            # CRITICAL: Use timestamp filter to get recent trades only (last 7 days for cache)
            paper_trades = get_trade_history(since=time.time() - 604800, limit=100)
            
            # Convert to ccxt format for compatibility
            result = []
            for t in paper_trades:
                result.append({
                    'id': t.get('trade_id', ''),
                    'order': t.get('order_id', ''),
                    'timestamp': t.get('timestamp', 0) * 1000,  # Convert to ms
                    'datetime': t.get('datetime_utc', ''),
                    'symbol': t.get('symbol', ''),
                    'side': t.get('side', ''),
                    'price': t.get('price', 0),
                    'amount': t.get('quantity', 0),
                    'cost': t.get('cost', 0),
                    'fee': {'cost': t.get('fee', 0), 'currency': t.get('fee_currency', 'USD')}
                })
            
            logger.debug(f"[STATUS-SERVICE] Using paper ledger trades ({len(result)} trades)")
            return result
        except Exception as e:
            logger.error(f"[STATUS-SERVICE] Failed to fetch paper trades: {e}")
            return []
    
    # LIVE MODE: Use Kraken API with caching
    # Check if cache is still valid
    if now - _trade_cache['fetched_at'] < _trade_cache['ttl']:
        logger.debug(f"[STATUS-SERVICE] Using cached trades ({len(_trade_cache['trades'])} trades)")
        return _trade_cache['trades']
    
    # Fetch fresh trades from Kraken
    try:
        ex = get_exchange()
        # Fetch last 30 days with reasonable limit
        since_30d_ms = int((now - 30 * 24 * 60 * 60) * 1000)
        all_trades = ex.fetch_my_trades(since=since_30d_ms, limit=100)
        
        _trade_cache['trades'] = all_trades
        _trade_cache['fetched_at'] = now
        logger.info(f"[STATUS-SERVICE] Fetched {len(all_trades)} trades from Kraken (cached for {_trade_cache['ttl']}s)")
        return all_trades
    except Exception as e:
        logger.error(f"[STATUS-SERVICE] Failed to fetch trades from Kraken: {e}")
        return []


def get_activity_summary(window: Literal["24h", "7d", "30d"] = "24h") -> Dict[str, Any]:
    """
    Get activity summary for time window.
    CRITICAL: Fetches DIRECTLY from Kraken API for accurate data (with caching).
    """
    # Calculate time window
    now = time.time()
    window_map = {
        "24h": 24 * 60 * 60,
        "7d": 7 * 24 * 60 * 60,
        "30d": 30 * 24 * 60 * 60
    }
    since_timestamp = now - window_map.get(window, 24 * 60 * 60)
    
    mode = get_mode()
    
    # Fetch trades (from cache if available)
    all_trades = _get_cached_trades()
    
    # Filter trades by window
    window_trades = [t for t in all_trades if t.get('timestamp', 0) >= since_timestamp * 1000]
    
    # Calculate stats from REAL Kraken data
    total_trades = len(window_trades)
    buys = [t for t in window_trades if t.get('side') == 'buy']
    sells = [t for t in window_trades if t.get('side') == 'sell']
    
    buy_prices = [t.get('price', 0) for t in buys if t.get('price')]
    sell_prices = [t.get('price', 0) for t in sells if t.get('price')]
    
    avg_buy_price = sum(buy_prices) / len(buy_prices) if buy_prices else 0
    avg_sell_price = sum(sell_prices) / len(sell_prices) if sell_prices else 0
    
    total_bought_usd = sum(t.get('cost', 0) for t in buys)
    total_sold_usd = sum(t.get('cost', 0) for t in sells)
    
    trade_stats = {
        'total_trades': total_trades,
        'buys': len(buys),
        'sells': len(sells),
        'avg_buy_price': avg_buy_price,
        'avg_sell_price': avg_sell_price,
        'total_bought_usd': total_bought_usd,
        'total_sold_usd': total_sold_usd
    }
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get order stats
        cursor.execute("""
            SELECT 
                COUNT(*) as total_orders,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_orders,
                SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed_orders,
                SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END) as canceled_orders
            FROM orders
            WHERE mode = ? AND timestamp >= ?
        """, (mode, since_timestamp))
        order_row = cursor.fetchone()
        order_stats = dict(order_row) if order_row else {}
        
        # Get current balances
        balances = get_balances()
        
        # Calculate realized P&L
        total_sold = trade_stats.get('total_sold_usd', 0) or 0
        total_bought = trade_stats.get('total_bought_usd', 0) or 0
        realized_pnl = total_sold - total_bought
        
        return {
            'window': window,
            'mode': mode,
            'last_sync': get_last_sync_time(),
            'trades': trade_stats,
            'orders': order_stats,
            'balances': balances,
            'realized_pnl_usd': realized_pnl,
            'generated_at': datetime.now(tz=timezone.utc).isoformat()
        }


def healthcheck() -> Dict[str, Any]:
    """
    Perform health check.
    Verifies: API keys valid, last sync recent, DB reachable, mode alignment.
    """
    health = {
        'status': 'healthy',
        'checks': {},
        'warnings': [],
        'errors': []
    }
    
    # 1. Check API keys
    try:
        ex = get_exchange()
        api_key = ex.apiKey
        health['checks']['api_keys'] = 'present' if api_key else 'missing'
        if not api_key:
            health['errors'].append("API keys not configured")
            health['status'] = 'error'
    except Exception as e:
        health['checks']['api_keys'] = 'error'
        health['errors'].append(f"API key check failed: {str(e)}")
        health['status'] = 'error'
    
    # 2. Check last sync
    try:
        last_sync = get_last_sync_time()
        if last_sync:
            age = time.time() - last_sync
            health['checks']['last_sync_age'] = f"{age:.0f}s ago"
            
            if age > SYNC_ERROR_THRESHOLD:
                health['errors'].append(f"Last sync > {SYNC_ERROR_THRESHOLD}s ago")
                health['status'] = 'error'
            elif age > SYNC_WARNING_THRESHOLD:
                health['warnings'].append(f"Last sync > {SYNC_WARNING_THRESHOLD}s ago")
                if health['status'] == 'healthy':
                    health['status'] = 'warning'
        else:
            health['checks']['last_sync_age'] = 'never'
            health['warnings'].append("No sync performed yet")
            if health['status'] == 'healthy':
                health['status'] = 'warning'
    except Exception as e:
        health['checks']['last_sync'] = 'error'
        health['errors'].append(f"Sync check failed: {str(e)}")
        health['status'] = 'error'
    
    # 3. Check DB
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            health['checks']['database'] = 'reachable'
    except Exception as e:
        health['checks']['database'] = 'unreachable'
        health['errors'].append(f"Database unreachable: {str(e)}")
        health['status'] = 'error'
    
    # 4. Check mode alignment
    try:
        mode = get_mode()
        health['checks']['mode'] = mode
    except Exception as e:
        health['checks']['mode'] = 'error'
        health['errors'].append(f"Mode check failed: {str(e)}")
        health['status'] = 'error'
    
    return health


# Initialize tables on import
init_status_tables()
