"""
Trade Analyzer - Learns from trading history to improve future decisions.
Analyzes wins, losses, and patterns to build trading intelligence.
"""
import sqlite3
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import time
from datetime import datetime, timedelta

from telemetry_db import get_db


def calculate_trade_pnl(trades: List[Dict[str, Any]], symbol: str) -> List[Dict[str, Any]]:
    """
    Calculate P&L for each trade pair (buy -> sell).
    Returns list of completed trades with profit/loss.
    """
    trades_pnl = []
    position = []
    
    for trade in sorted(trades, key=lambda t: t['timestamp']):
        if trade['symbol'] != symbol:
            continue
            
        if trade['side'] == 'buy':
            position.append(trade)
        elif trade['side'] == 'sell' and position:
            buy_trade = position.pop(0)
            
            buy_price = buy_trade.get('price', 0) or (
                buy_trade.get('usd_amount', 0) / buy_trade.get('quantity', 1) 
                if buy_trade.get('quantity') else 0
            )
            sell_price = trade.get('price', 0) or (
                trade.get('usd_amount', 0) / trade.get('quantity', 1)
                if trade.get('quantity') else 0
            )
            
            qty = min(
                buy_trade.get('quantity', 0) or 0,
                trade.get('quantity', 0) or 0
            )
            
            if buy_price > 0 and sell_price > 0 and qty > 0:
                pnl_usd = qty * (sell_price - buy_price)
                pnl_pct = ((sell_price - buy_price) / buy_price) * 100
                
                hold_time = trade['timestamp'] - buy_trade['timestamp']
                
                trades_pnl.append({
                    'symbol': symbol,
                    'buy_timestamp': buy_trade['timestamp'],
                    'sell_timestamp': trade['timestamp'],
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'quantity': qty,
                    'pnl_usd': pnl_usd,
                    'pnl_pct': pnl_pct,
                    'hold_time_seconds': hold_time,
                    'buy_reason': buy_trade.get('reason', ''),
                    'sell_reason': trade.get('reason', ''),
                    'winner': pnl_usd > 0,
                })
    
    return trades_pnl


def get_win_rate(symbol: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
    """Calculate win rate and related statistics."""
    cutoff = time.time() - (days * 24 * 60 * 60)
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        if symbol:
            cursor.execute("""
                SELECT * FROM trades
                WHERE symbol = ? AND timestamp > ?
                ORDER BY timestamp ASC
            """, (symbol, cutoff))
        else:
            cursor.execute("""
                SELECT * FROM trades
                WHERE timestamp > ?
                ORDER BY timestamp ASC
            """, (cutoff,))
        
        trades = [dict(row) for row in cursor.fetchall()]
    
    if not trades:
        return {
            'total_trades': 0,
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'profit_factor': 0,
        }
    
    symbols = list(set(t['symbol'] for t in trades))
    all_pnl = []
    
    for sym in symbols:
        sym_pnl = calculate_trade_pnl(trades, sym)
        all_pnl.extend(sym_pnl)
    
    if not all_pnl:
        return {
            'total_trades': len(trades),
            'completed_trades': 0,
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'profit_factor': 0,
        }
    
    winners = [t for t in all_pnl if t['winner']]
    losers = [t for t in all_pnl if not t['winner']]
    
    total_wins = sum(t['pnl_usd'] for t in winners)
    total_losses = abs(sum(t['pnl_usd'] for t in losers))
    
    avg_win = total_wins / len(winners) if winners else 0
    avg_loss = total_losses / len(losers) if losers else 0
    profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
    
    return {
        'total_trades': len(trades),
        'completed_trades': len(all_pnl),
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': (len(winners) / len(all_pnl) * 100) if all_pnl else 0,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'total_pnl': total_wins - total_losses,
        'profit_factor': profit_factor,
        'best_trade': max((t['pnl_usd'] for t in all_pnl), default=0),
        'worst_trade': min((t['pnl_usd'] for t in all_pnl), default=0),
    }


def analyze_what_works() -> Dict[str, Any]:
    """
    Analyze trading patterns to learn what strategies are working.
    Returns insights about successful vs unsuccessful patterns.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM trades
            WHERE timestamp > ?
            ORDER BY timestamp ASC
        """, (time.time() - 30 * 24 * 60 * 60,))
        
        trades = [dict(row) for row in cursor.fetchall()]
    
    if len(trades) < 2:
        return {'status': 'insufficient_data', 'message': 'Need more trades to analyze'}
    
    symbols = list(set(t['symbol'] for t in trades))
    insights = []
    
    for symbol in symbols:
        pnl_data = calculate_trade_pnl(trades, symbol)
        
        if len(pnl_data) < 2:
            continue
        
        winners = [t for t in pnl_data if t['winner']]
        losers = [t for t in pnl_data if not t['winner']]
        
        if winners:
            avg_hold_win = sum(t['hold_time_seconds'] for t in winners) / len(winners)
            insights.append({
                'symbol': symbol,
                'pattern': 'winning_hold_time',
                'avg_hold_hours': round(avg_hold_win / 3600, 1),
                'sample_size': len(winners)
            })
        
        if losers:
            avg_hold_loss = sum(t['hold_time_seconds'] for t in losers) / len(losers)
            insights.append({
                'symbol': symbol,
                'pattern': 'losing_hold_time',
                'avg_hold_hours': round(avg_hold_loss / 3600, 1),
                'sample_size': len(losers)
            })
        
        common_win_reasons = {}
        for t in winners:
            reason = t['buy_reason']
            if reason:
                common_win_reasons[reason] = common_win_reasons.get(reason, 0) + 1
        
        if common_win_reasons:
            best_reason = max(common_win_reasons.items(), key=lambda x: x[1])
            insights.append({
                'symbol': symbol,
                'pattern': 'best_entry_reason',
                'reason': best_reason[0],
                'success_count': best_reason[1]
            })
    
    return {
        'status': 'ok',
        'insights': insights,
        'total_symbols': len(symbols),
    }


def get_performance_summary(days: int = 7) -> Dict[str, Any]:
    """Get a comprehensive performance summary."""
    win_stats = get_win_rate(days=days)
    patterns = analyze_what_works()
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT equity_usd, timestamp 
            FROM performance
            WHERE timestamp > ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (time.time() - days * 24 * 60 * 60,))
        
        latest_perf = cursor.fetchone()
        
        cursor.execute("""
            SELECT equity_usd, timestamp 
            FROM performance
            WHERE timestamp > ?
            ORDER BY timestamp ASC
            LIMIT 1
        """, (time.time() - days * 24 * 60 * 60,))
        
        earliest_perf = cursor.fetchone()
    
    equity_change = 0
    if latest_perf and earliest_perf:
        latest = dict(latest_perf)
        earliest = dict(earliest_perf)
        equity_change = latest['equity_usd'] - earliest['equity_usd']
    
    return {
        'period_days': days,
        'equity_change_usd': equity_change,
        'win_rate_pct': round(win_stats['win_rate'], 2),
        'total_completed_trades': win_stats['completed_trades'],
        'profit_factor': round(win_stats['profit_factor'], 2) if win_stats['profit_factor'] != float('inf') else 'N/A',
        'avg_win_usd': round(win_stats['avg_win'], 2),
        'avg_loss_usd': round(win_stats['avg_loss'], 2),
        'total_pnl_usd': round(win_stats['total_pnl'], 2),
        'patterns_discovered': len(patterns.get('insights', [])),
    }


def get_learning_summary() -> str:
    """Get a human-readable learning summary for the LLM."""
    summary = get_performance_summary(days=30)
    patterns = analyze_what_works()
    
    lines = [
        f"📊 Learning Summary (Last 30 Days):",
        f"- Completed Trades: {summary['total_completed_trades']}",
        f"- Win Rate: {summary['win_rate_pct']}%",
        f"- Profit Factor: {summary['profit_factor']}",
        f"- Total P&L: ${summary['total_pnl_usd']}",
    ]
    
    if patterns.get('insights'):
        lines.append("\n🧠 Patterns Discovered:")
        for insight in patterns['insights'][:3]:
            if insight['pattern'] == 'best_entry_reason':
                lines.append(
                    f"- {insight['symbol']}: '{insight['reason']}' "
                    f"led to {insight['success_count']} wins"
                )
            elif insight['pattern'] == 'winning_hold_time':
                lines.append(
                    f"- {insight['symbol']}: Winners held avg "
                    f"{insight['avg_hold_hours']}h"
                )
    
    return "\n".join(lines)
