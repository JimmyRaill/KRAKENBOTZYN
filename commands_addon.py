# commands_addon.py - Force trade test and debug status implementations

import json
import time
import os
from typing import Dict, Any
from exchange_manager import get_mode_str, is_paper_mode
from evaluation_log import log_order_execution, register_pending_child_order
from telemetry_db import log_trade

def _debug_status() -> str:
    """
    Return comprehensive diagnostic snapshot of trading system.
    
    Shows:
    - Trading mode (LIVE/PAPER)
    - Current balance/equity
    - Last evaluation timestamp and details
    - Key indicators from last evaluation
    - Recent statistics (24h evaluations and trades)
    """
    try:
        from exchange_manager import get_exchange, get_mode_str
        from account_state import get_balances
        from evaluation_log import get_last_evaluations
        from datetime import datetime, timedelta
        
        mode = get_mode_str().upper()
        ex = get_exchange()
        
        # Get current balances
        balances = get_balances()
        total_equity = sum(bal.get('usd_value', 0) for bal in balances.values()) if balances else 0
        usd_cash = balances.get('USD', {}).get('total', 0) if balances else 0
        
        # Get last evaluation
        last_evals = get_last_evaluations(limit=1)
        last_eval = last_evals[0] if last_evals else None
        
        # Get REAL trades from last 24 hours (not evaluations!)
        from telemetry_db import get_trading_stats_24h
        stats_24h = get_trading_stats_24h()
        
        # Get evaluations from last 24 hours for diagnostics
        all_evals = get_last_evaluations(limit=1000)
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        evals_24h = [
            e for e in all_evals 
            if datetime.fromisoformat(e['timestamp_utc'].replace('Z', '+00:00')) > cutoff_time
        ]
        
        # Build response
        lines = [
            "=== ZYN DIAGNOSTIC STATUS ===",
            "",
            f"🔧 Mode: {mode}",
            f"💰 Total Equity: ${total_equity:.2f}",
            f"💵 USD Cash: ${usd_cash:.2f}",
            "",
            "📊 Last Evaluation:",
        ]
        
        if last_eval:
            lines.extend([
                f"  Time: {last_eval['timestamp_utc']}",
                f"  Symbol: {last_eval['symbol']}",
                f"  Decision: {last_eval['decision']}",
                f"  Regime: {last_eval.get('regime', 'N/A')}",
                f"  Price: ${last_eval.get('price', 0):.2f}",
                f"  RSI: {last_eval.get('rsi', 0):.2f}",
                f"  ADX: {last_eval.get('adx', 0):.2f}",
                f"  ATR: {last_eval.get('atr', 0):.4f}",
                f"  Volume: {last_eval.get('volume', 'N/A')}",
                f"  Reason: {last_eval.get('reason', 'N/A')}",
            ])
        else:
            lines.append("  No evaluations found")
        
        lines.extend([
            "",
            "📈 Last 24 Hours (REAL trades, not evaluations):",
            f"  Total Evaluations: {len(evals_24h)}",
            f"  Total Trades Executed: {stats_24h['total_trades_24h']}",
            f"    └─ Autopilot: {stats_24h['autopilot_trades_24h']}",
            f"    └─ Manual Commands: {stats_24h['command_trades_24h']}",
            f"    └─ Force Tests: {stats_24h['force_test_trades_24h']}",
            f"    └─ Unknown Source: {stats_24h['unknown_trades_24h']}",
            ""
        ])
        
        # Show actual trades breakdown by symbol
        if stats_24h['trades']:
            symbols = {}
            for t in stats_24h['trades']:
                sym = t.get('symbol', 'UNKNOWN')
                if sym not in symbols:
                    symbols[sym] = []
                symbols[sym].append(t)
            
            lines.append("Trades by Symbol (last 24h):")
            for sym, sym_trades in symbols.items():
                lines.append(f"  {sym}: {len(sym_trades)} trades")
                for t in sym_trades[:3]:  # Show first 3 trades per symbol
                    side = t['side'].upper()
                    price = t.get('price', 0)
                    qty = t.get('quantity', 0)
                    source = t.get('source', 'unknown')
                    lines.append(f"    └─ {side} {qty:.4f} @ ${price:.2f} (source: {source})")
        else:
            lines.append("No trades executed in last 24 hours.")
        
        return "\n".join(lines)
        
    except Exception as e:
        import traceback
        return f"[DEBUG-STATUS-ERR] {e}\n{traceback.format_exc()}"


def _trades_24h_status() -> str:
    """
    Show REAL trades executed in last 24 hours with full source attribution.
    Uses timestamp filtering - no guessing, no vibes.
    """
    try:
        from telemetry_db import get_trading_stats_24h
        from datetime import datetime
        
        stats = get_trading_stats_24h()
        
        lines = [
            "=== TRADES IN LAST 24 HOURS (TIMESTAMP FILTERED) ===",
            "",
            f"📊 Total Trades: {stats['total_trades_24h']}",
            f"  └─ Autopilot: {stats['autopilot_trades_24h']}",
            f"  └─ Manual Commands: {stats['command_trades_24h']}",
            f"  └─ Force Tests: {stats['force_test_trades_24h']}",
            f"  └─ Unknown Source: {stats['unknown_trades_24h']}",
            ""
        ]
        
        if stats['trades']:
            lines.append("📜 Trade Details:")
            for t in stats['trades']:
                timestamp = datetime.fromtimestamp(t['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
                side = t['side'].upper()
                qty = t.get('quantity', 0)
                price = t.get('price', 0)
                usd = t.get('usd_amount', 0)
                source = t.get('source', 'unknown')
                reason = t.get('reason', 'N/A')
                
                lines.append(f"  [{timestamp}] {side} {t['symbol']}")
                lines.append(f"    Amount: {qty:.4f} @ ${price:.2f} = ${usd:.2f}")
                lines.append(f"    Source: {source}")
                lines.append(f"    Reason: {reason}")
                lines.append("")
        else:
            lines.append("No trades executed in last 24 hours.")
        
        return "\n".join(lines)
        
    except Exception as e:
        import traceback
        return f"[TRADES-24H-ERR] {e}\n{traceback.format_exc()}"


def _force_trade_test(symbol: str = "ETH/USD") -> str:
    """
    DEVELOPER ONLY: Execute a tiny LIVE trade to verify order placement pipeline.
    
    SAFETY:
    - Requires ENABLE_FORCE_TRADE=1 in environment
    - Only works in LIVE mode
    - Hard-coded to $15 position size
    - Logs every step with full Kraken responses
    - Places bracket orders (SL/TP) for protection
    
    Args:
        symbol: Trading pair (default: ETH/USD)
    
    Returns:
        Detailed log of execution with Kraken order IDs
    """
    # Safety check: Must be enabled
    if os.getenv("ENABLE_FORCE_TRADE", "0") != "1":
        return (
            "❌ [FORCE-TRADE] DISABLED\n"
            "This command requires ENABLE_FORCE_TRADE=1 in .env\n"
            "This is a safety feature to prevent accidental LIVE trades.\n\n"
            "To enable:\n"
            "1. Add ENABLE_FORCE_TRADE=1 to .env\n"
            "2. Restart workflows\n"
            "3. Run this command again\n"
            "4. REMOVE the flag after testing"
        )
    
    # Safety check: Must be LIVE mode
    from exchange_manager import get_mode_str, get_exchange, is_live_mode
    
    if not is_live_mode():
        return (
            "❌ [FORCE-TRADE] Only works in LIVE mode\n"
            f"Current mode: {get_mode_str().upper()}\n"
            "Set KRAKEN_VALIDATE_ONLY=0 to enable LIVE mode"
        )
    
    ex = get_exchange()
    test_usd = 15.0  # Tiny test size
    
    log_lines = [
        "🧪 [FORCE-TRADE-TEST] Starting LIVE trade test...",
        f"Symbol: {symbol}",
        f"Test Size: ${test_usd}",
        ""
    ]
    
    try:
        # Step 1: Fetch price
        log_lines.append("Step 1/5: Fetching market price...")
        ticker = ex.fetch_ticker(symbol)
        price = float(ticker['last'])
        log_lines.append(f"✅ Price: ${price:.2f}")
        log_lines.append(f"   Full ticker: {json.dumps(ticker, indent=2, default=str)}")
        log_lines.append("")
        
        # Step 2: Calculate quantity
        log_lines.append("Step 2/5: Calculating position size...")
        qty = float(test_usd / price)
        base_currency = symbol.split('/')[0]
        log_lines.append(f"✅ Quantity: {qty:.8f} {base_currency}")
        log_lines.append("")
        
        # Step 3: Place market buy order
        log_lines.append("Step 3/5: Placing LIVE market buy order...")
        entry_order = ex.create_market_buy_order(symbol, qty)
        entry_id = str(entry_order.get('id') or entry_order.get('orderId', 'NO_ID'))
        log_lines.append(f"✅ Entry Order ID: {entry_id}")
        log_lines.append(f"   Full response: {json.dumps(entry_order, indent=2, default=str)}")
        log_lines.append("")
        
        # Log the trade to both databases for complete tracking
        actual_filled = entry_order.get("filled") or qty
        actual_price = entry_order.get("average") or entry_order.get("price") or price
        
        # Log to executed_orders table (forensic log)
        try:
            log_order_execution(
                symbol=symbol,
                side="buy",
                quantity=actual_filled,
                entry_price=actual_price,
                order_id=entry_id,
                trading_mode="live",
                source="force_trade_test",
                extra_info=f"LIVE force trade test ~${test_usd}"
            )
        except Exception as log_err:
            log_lines.append(f"⚠️  Failed to log to executed_orders: {log_err}")
        
        # Log to telemetry DB (for "trades in last 24h" reporting)
        try:
            log_trade(
                symbol=symbol,
                side="buy",
                action="market_buy",
                quantity=actual_filled,
                price=actual_price,
                usd_amount=actual_filled * actual_price,
                order_id=entry_id,
                reason="force trade test",
                source="force_trade_test",
                mode="live"
            )
        except Exception as log_err:
            log_lines.append(f"⚠️  Failed to log to telemetry DB: {log_err}")
        
        # Step 4: Calculate SL/TP using ATR
        log_lines.append("Step 4/5: Calculating stop-loss and take-profit...")
        from candle_strategy import calculate_atr
        ohlcv = ex.fetch_ohlcv(symbol, '5m', 100)
        atr = calculate_atr(ohlcv, period=14)
        
        if atr is None or atr <= 0:
            raise ValueError("Invalid ATR calculation - cannot determine stop-loss/take-profit")
        
        sl_price = price - (2.0 * float(atr))  # 2x ATR stop-loss
        tp_price = price + (3.0 * float(atr))  # 3x ATR take-profit
        
        log_lines.append(f"✅ ATR: ${atr:.2f}")
        log_lines.append(f"   Stop-Loss: ${sl_price:.2f} (2x ATR below entry)")
        log_lines.append(f"   Take-Profit: ${tp_price:.2f} (3x ATR above entry)")
        log_lines.append("")
        
        # Step 5: Place bracket orders
        log_lines.append("Step 5/5: Placing protective bracket orders...")
        
        # Take-profit (limit sell)
        tp_order = ex.create_limit_sell_order(symbol, qty, tp_price)
        tp_id = str(tp_order.get('id') or tp_order.get('orderId', 'NO_ID'))
        log_lines.append(f"✅ Take-Profit Order ID: {tp_id}")
        log_lines.append(f"   TP response: {json.dumps(tp_order, indent=2, default=str)}")
        
        # Stop-loss (stop-market sell)
        sl_order = ex.create_order(symbol, 'market', 'sell', qty, None, {'stopPrice': sl_price})
        sl_id = str(sl_order.get('id') or sl_order.get('orderId', 'NO_ID'))
        log_lines.append(f"✅ Stop-Loss Order ID: {sl_id}")
        log_lines.append(f"   SL response: {json.dumps(sl_order, indent=2, default=str)}")
        log_lines.append("")
        
        # Success summary
        log_lines.extend([
            "=" * 60,
            "✅ FORCE TRADE TEST SUCCESSFUL",
            "=" * 60,
            f"Entry: {entry_id}",
            f"Take-Profit: {tp_id}",
            f"Stop-Loss: {sl_id}",
            "",
            "⚠️  LIVE POSITION OPENED",
            "This is a real trade with real money.",
            "Monitor the position and close manually if needed.",
            "",
            "To close manually:",
            f"  sell all {symbol}",
            ""
        ])
        
        return "\n".join(log_lines)
        
    except Exception as e:
        import traceback
        log_lines.extend([
            "",
            "❌ [FORCE-TRADE-TEST] FAILED",
            f"Error: {str(e)}",
            "",
            "Full Traceback:",
            traceback.format_exc()
        ])
        return "\n".join(log_lines)
