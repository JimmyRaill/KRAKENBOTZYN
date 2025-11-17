"""
Comprehensive Diagnostic Tools for Trading Bot

Provides truthful, reconciled reporting of bot status, trades, and performance.
Enforces strict no-hallucination rules by cross-checking Kraken API with local DBs.
"""

import sqlite3
import os
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
import ccxt
from loguru import logger


class DiagnosticReport:
    """Container for comprehensive bot diagnostic data"""
    
    def __init__(self):
        self.timestamp = datetime.now(timezone.utc)
        self.system_time = self.timestamp.isoformat()
        self.trading_mode = None
        self.config = {}
        self.universe = []
        self.trades_24h = []
        self.trades_7d = []
        self.executed_orders = []
        self.evaluations = {}
        self.errors = []
        self.reconciliation = {}
        self.health_status = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary for JSON serialization"""
        return {
            "timestamp": self.system_time,
            "trading_mode": self.trading_mode,
            "config": self.config,
            "universe": self.universe,
            "trades_24h": {
                "count": len(self.trades_24h),
                "trades": self.trades_24h
            },
            "trades_7d": {
                "count": len(self.trades_7d),
                "breakdown": self._count_by_source(self.trades_7d)
            },
            "executed_orders": {
                "count": len(self.executed_orders),
                "latest_10": self.executed_orders[:10]
            },
            "evaluations": self.evaluations,
            "reconciliation": self.reconciliation,
            "health_status": self.health_status,
            "errors": self.errors
        }
    
    def _count_by_source(self, trades: List[Dict]) -> Dict[str, int]:
        """Count trades grouped by source field"""
        counts = {}
        for trade in trades:
            source = trade.get('source', 'unknown')
            counts[source] = counts.get(source, 0) + 1
        return counts


def get_current_config() -> Dict[str, Any]:
    """
    Read current trading configuration from environment and config.
    
    Returns:
        Dictionary of config values
    """
    from trading_config import TradingConfig
    
    config = TradingConfig.from_env()
    
    return {
        "trading_mode": "paper" if config.paper_mode else "live",
        "use_brackets": os.getenv("USE_BRACKETS", "False").lower() in ("true", "1"),
        "autonomous": os.getenv("AUTONOMOUS", "0") == "1",
        "max_trades_per_day": config.risk.max_trades_per_day,
        "max_daily_loss_usd": config.risk.max_daily_loss_usd,
        "symbols": config.symbols,
        "risk_per_trade_pct": config.risk.risk_per_trade_pct
    }


def get_kraken_trades(
    since_hours: Optional[int] = None,
    since_days: Optional[int] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Fetch real trades from Kraken API.
    
    Args:
        since_hours: Filter trades from last N hours
        since_days: Filter trades from last N days
        limit: Max trades to fetch
        
    Returns:
        List of trade dictionaries from Kraken
    """
    try:
        api_key = os.getenv("KRAKEN_API_KEY", "")
        api_secret = os.getenv("KRAKEN_API_SECRET", "")
        
        if not api_key or not api_secret:
            logger.warning("[DIAGNOSTIC] No Kraken credentials - cannot fetch real trades")
            return []
        
        exchange = ccxt.kraken({
            'apiKey': api_key,
            'secret': api_secret
        })
        
        # Calculate timestamp filter
        since_timestamp = None
        if since_hours:
            since_timestamp = int((datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp() * 1000)
        elif since_days:
            since_timestamp = int((datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp() * 1000)
        
        trades = exchange.fetch_my_trades(since=since_timestamp, limit=limit)
        
        # Convert to simplified format
        simplified = []
        for t in trades:
            simplified.append({
                'symbol': t['symbol'],
                'side': t['side'],
                'amount': t['amount'],
                'price': t['price'],
                'cost': t['cost'],
                'timestamp': datetime.fromtimestamp(t['timestamp']/1000, tz=timezone.utc).isoformat(),
                'trade_id': t['id'],
                'order_id': t.get('order', 'N/A')
            })
        
        return simplified
        
    except Exception as e:
        logger.error(f"[DIAGNOSTIC] Failed to fetch Kraken trades: {e}")
        return []


def get_db_trades(db_path: str, since_hours: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Fetch trades from local SQLite database.
    
    Args:
        db_path: Path to SQLite database
        since_hours: Filter trades from last N hours
        
    Returns:
        List of trade dictionaries from DB
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if since_hours:
            import time
            cutoff = time.time() - (since_hours * 3600)
            cursor.execute("""
                SELECT * FROM trades 
                WHERE timestamp >= ? 
                ORDER BY timestamp DESC
            """, (cutoff,))
        else:
            cursor.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT 100")
        
        rows = cursor.fetchall()
        conn.close()
        
        trades = []
        for row in rows:
            trades.append({
                'id': row['id'],
                'symbol': row['symbol'],
                'side': row['side'],
                'quantity': row['quantity'],
                'price': row['price'],
                'timestamp': datetime.fromtimestamp(row['timestamp'], tz=timezone.utc).isoformat() if row['timestamp'] else None,
                'source': row.get('source', 'unknown'),
                'mode': row.get('mode', 'unknown'),
                'trade_id': row.get('trade_id'),
                'order_id': row.get('order_id')
            })
        
        return trades
        
    except Exception as e:
        logger.error(f"[DIAGNOSTIC] Failed to fetch DB trades from {db_path}: {e}")
        return []


def get_executed_orders(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch recent executed orders from evaluation_log.db.
    
    Args:
        limit: Max orders to fetch
        
    Returns:
        List of executed order dictionaries
    """
    try:
        conn = sqlite3.connect('evaluation_log.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM executed_orders 
            ORDER BY timestamp_utc DESC 
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        orders = []
        for row in rows:
            orders.append({
                'timestamp': row['timestamp_utc'],
                'symbol': row['symbol'],
                'side': row['side'],
                'quantity': row['quantity'],
                'price': row['price'],
                'order_id': row['order_id'],
                'trading_mode': row['trading_mode'],
                'source': row['source']
            })
        
        return orders
        
    except Exception as e:
        logger.error(f"[DIAGNOSTIC] Failed to fetch executed_orders: {e}")
        return []


def get_evaluation_stats(today_only: bool = True) -> Dict[str, int]:
    """
    Get evaluation and signal statistics.
    
    Args:
        today_only: If True, only count today's evaluations
        
    Returns:
        Dictionary with evaluation counts
    """
    try:
        conn = sqlite3.connect('evaluation_log.db')
        cursor = conn.cursor()
        
        if today_only:
            cursor.execute("""
                SELECT COUNT(*) FROM evaluations 
                WHERE DATE(timestamp_utc) = DATE('now')
            """)
            total = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(*) FROM evaluations 
                WHERE DATE(timestamp_utc) = DATE('now') 
                AND decision NOT IN ('HOLD', 'SKIP')
            """)
            non_hold = cursor.fetchone()[0]
        else:
            cursor.execute("SELECT COUNT(*) FROM evaluations")
            total = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(*) FROM evaluations 
                WHERE decision NOT IN ('HOLD', 'SKIP')
            """)
            non_hold = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_evaluations': total,
            'non_hold_signals': non_hold,
            'hold_percentage': round((total - non_hold) / total * 100, 1) if total > 0 else 0
        }
        
    except Exception as e:
        logger.error(f"[DIAGNOSTIC] Failed to get evaluation stats: {e}")
        return {'total_evaluations': 0, 'non_hold_signals': 0, 'hold_percentage': 0}


def reconcile_trades(
    kraken_trades: List[Dict],
    db_trades: List[Dict]
) -> Dict[str, Any]:
    """
    Reconcile Kraken trades with local database trades.
    
    Args:
        kraken_trades: Trades from Kraken API
        db_trades: Trades from local DB
        
    Returns:
        Reconciliation report with matches and mismatches
    """
    kraken_ids = {t['trade_id'] for t in kraken_trades if t.get('trade_id')}
    db_ids = {t['trade_id'] for t in db_trades if t.get('trade_id')}
    
    # Find mismatches
    only_in_kraken = kraken_ids - db_ids
    only_in_db = db_ids - kraken_ids
    matched = kraken_ids & db_ids
    
    return {
        'total_kraken': len(kraken_trades),
        'total_db': len(db_trades),
        'matched_count': len(matched),
        'missing_from_db': list(only_in_kraken),
        'missing_from_kraken': list(only_in_db),
        'reconciliation_rate': round(len(matched) / len(kraken_trades) * 100, 1) if kraken_trades else 0
    }


def generate_full_diagnostic() -> DiagnosticReport:
    """
    Generate comprehensive diagnostic report with strict truth enforcement.
    
    Returns:
        DiagnosticReport with all system status and trade data
    """
    logger.info("[DIAGNOSTIC] Generating full diagnostic report...")
    
    report = DiagnosticReport()
    
    # 1. Current config
    try:
        report.config = get_current_config()
        report.trading_mode = report.config['trading_mode']
    except Exception as e:
        report.errors.append(f"Config loading failed: {e}")
    
    # 2. Kraken trades (24h and 7d)
    try:
        report.trades_24h = get_kraken_trades(since_hours=24)
        report.trades_7d = get_kraken_trades(since_days=7)
        logger.info(f"[DIAGNOSTIC] Kraken trades: 24h={len(report.trades_24h)}, 7d={len(report.trades_7d)}")
    except Exception as e:
        report.errors.append(f"Kraken trade fetch failed: {e}")
    
    # 3. DB trades
    try:
        db_trades_24h = get_db_trades('trading_memory.db', since_hours=24)
        db_trades_7d = get_db_trades('trading_memory.db', since_hours=168)  # 7 days
        logger.info(f"[DIAGNOSTIC] DB trades: 24h={len(db_trades_24h)}, 7d={len(db_trades_7d)}")
    except Exception as e:
        report.errors.append(f"DB trade fetch failed: {e}")
        db_trades_24h = []
        db_trades_7d = []
    
    # 4. Executed orders log
    try:
        report.executed_orders = get_executed_orders(limit=10)
        logger.info(f"[DIAGNOSTIC] Executed orders: {len(report.executed_orders)}")
    except Exception as e:
        report.errors.append(f"Executed orders fetch failed: {e}")
    
    # 5. Evaluation stats
    try:
        report.evaluations = get_evaluation_stats(today_only=True)
        logger.info(f"[DIAGNOSTIC] Evaluations today: {report.evaluations}")
    except Exception as e:
        report.errors.append(f"Evaluation stats failed: {e}")
    
    # 6. Reconciliation
    try:
        report.reconciliation = reconcile_trades(report.trades_24h, db_trades_24h)
        logger.info(f"[DIAGNOSTIC] Reconciliation: {report.reconciliation['reconciliation_rate']}% match rate")
    except Exception as e:
        report.errors.append(f"Reconciliation failed: {e}")
    
    # 7. Health check
    try:
        from kraken_health import kraken_health_check
        health_results = kraken_health_check()
        report.health_status = {
            name: result.to_dict() 
            for name, result in health_results.items()
        }
    except Exception as e:
        report.errors.append(f"Health check failed: {e}")
    
    logger.info("[DIAGNOSTIC] ✅ Diagnostic report complete")
    return report


def print_diagnostic_summary(report: DiagnosticReport) -> str:
    """
    Format diagnostic report as human-readable summary.
    
    Args:
        report: DiagnosticReport instance
        
    Returns:
        Formatted summary string
    """
    lines = [
        "=" * 60,
        "TRADING BOT DIAGNOSTIC REPORT",
        "=" * 60,
        f"Generated: {report.system_time}",
        "",
        "=== CONFIGURATION ===",
        f"Trading Mode: {report.trading_mode}",
        f"Use Brackets: {report.config.get('use_brackets', 'unknown')}",
        f"Autonomous: {report.config.get('autonomous', 'unknown')}",
        f"Max Trades/Day: {report.config.get('max_trades_per_day', 'unknown')}",
        f"Max Daily Loss: ${report.config.get('max_daily_loss_usd', 'unknown')}",
        "",
        "=== TRADES (LAST 24 HOURS) ===",
        f"Kraken API: {len(report.trades_24h)} trades",
        f"Local DB: {report.reconciliation.get('total_db', 0)} trades",
        f"Match Rate: {report.reconciliation.get('reconciliation_rate', 0)}%",
        "",
        "=== TRADES (LAST 7 DAYS) ===",
        f"Total from Kraken: {len(report.trades_7d)}",
    ]
    
    # Source breakdown
    if report.trades_7d:
        lines.append("Breakdown by source:")
        breakdown = report.to_dict()['trades_7d']['breakdown']
        for source, count in breakdown.items():
            lines.append(f"  - {source}: {count}")
    
    lines.extend([
        "",
        "=== EXECUTED ORDERS LOG ===",
        f"Total entries: {len(report.executed_orders)}",
    ])
    
    if report.executed_orders:
        lines.append("Most recent:")
        for order in report.executed_orders[:3]:
            lines.append(f"  {order['timestamp']} | {order['symbol']} {order['side']} | Source: {order['source']}")
    
    lines.extend([
        "",
        "=== EVALUATIONS (TODAY) ===",
        f"Total: {report.evaluations.get('total_evaluations', 0)}",
        f"Non-HOLD signals: {report.evaluations.get('non_hold_signals', 0)}",
        f"HOLD rate: {report.evaluations.get('hold_percentage', 0)}%",
        "",
        "=== ERRORS ===",
    ])
    
    if report.errors:
        for error in report.errors:
            lines.append(f"  ❌ {error}")
    else:
        lines.append("  ✅ No errors")
    
    lines.append("=" * 60)
    
    return "\n".join(lines)


if __name__ == "__main__":
    # Standalone diagnostic test
    report = generate_full_diagnostic()
    print(print_diagnostic_summary(report))
