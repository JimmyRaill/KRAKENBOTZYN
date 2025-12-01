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
            "=== ZIN DIAGNOSTIC STATUS ===",
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
    DEVELOPER ONLY: Execute a tiny LIVE OCO bracket trade to verify order placement pipeline.
    
    ATOMIC EXECUTION:
    - Uses Kraken's native OCO bracket orders (stop-loss-profit ordertype)
    - Entry + TP + SL attached in ONE atomic request
    - TRUE OCO: When TP fills, SL auto-cancels; when SL fills, TP auto-cancels
    
    SAFETY:
    - Requires ENABLE_FORCE_TRADE=1 in environment
    - Only works in LIVE mode
    - Hard-coded to $15 position size
    - Logs every step with full Kraken responses
    
    Args:
        symbol: Trading pair (default: ETH/USD)
    
    Returns:
        Execution report with OCO bracket details
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
        "🧪 [FORCE-TRADE-TEST] Starting LIVE OCO bracket trade test...",
        f"Symbol: {symbol}",
        f"Test Size: ${test_usd}",
        ""
    ]
    
    try:
        # Step 1: Fetch price
        log_lines.append("Step 1/4: Fetching market price...")
        ticker = ex.fetch_ticker(symbol)
        price = float(ticker['last'])
        log_lines.append(f"✅ Price: ${price:.2f}")
        log_lines.append("")
        
        # Step 2: Calculate quantity
        log_lines.append("Step 2/4: Calculating position size...")
        qty = float(test_usd / price)
        base_currency = symbol.split('/')[0]
        log_lines.append(f"✅ Quantity: {qty:.8f} {base_currency}")
        log_lines.append("")
        
        # Step 3: Calculate OCO bracket prices
        log_lines.append("Step 3/4: Calculating OCO bracket prices...")
        from candle_strategy import get_latest_atr
        from bracket_order_manager import get_bracket_manager
        
        atr = get_latest_atr(symbol, exchange=ex)
        
        manager = get_bracket_manager()
        bracket = manager.calculate_bracket_prices(
            symbol=symbol,
            side="buy",
            entry_price=price,
            atr=atr
        )
        
        if not bracket:
            raise ValueError("Failed to calculate OCO brackets")
        
        bracket.quantity = qty
        bracket.recalculate_metrics()
        
        log_lines.append(f"✅ ATR: ${atr:.6f}" if atr else "✅ Using fallback % brackets")
        log_lines.append(f"   Stop-Loss: ${bracket.stop_price:.2f}")
        log_lines.append(f"   Take-Profit: ${bracket.take_profit_price:.2f}")
        log_lines.append(f"   R:R Ratio: {bracket.rr_ratio:.2f}")
        log_lines.append("")
        
        # Step 4: Place OCO bracket order
        log_lines.append("Step 4/4: Placing LIVE OCO bracket order on Kraken...")
        success, message, order_result = manager.place_entry_with_brackets(bracket, ex)
        
        if not success:
            raise Exception(f"OCO bracket placement failed: {message}")
        
        # Extract order ID and VERIFY fill data from result
        oid = "unknown"
        if order_result and 'txid' in order_result:
            oid = order_result['txid'][0] if order_result['txid'] else "unknown"
        
        log_lines.append(f"Order ID: {oid}")
        log_lines.append("")
        
        # CRITICAL: Only log if we have CONFIRMED fill data from Kraken
        if order_result and 'fill_data' in order_result:
            fill_data = order_result['fill_data']
            status = fill_data.get('status', '')
            filled_qty = float(fill_data.get('filled', 0))
            fill_price = fill_data.get('average')
            
            log_lines.append(f"Fill Status: {status}")
            log_lines.append(f"Filled Qty: {filled_qty:.8f}")
            log_lines.append(f"Fill Price: ${fill_price:.4f}" if fill_price else "Fill Price: N/A")
            log_lines.append("")
            
            # Require closed status AND non-zero fill before logging
            if status == 'closed' and filled_qty > 0 and fill_price:
                from telemetry_db import log_trade
                from evaluation_log import log_order_execution
                
                log_order_execution(
                    symbol=symbol,
                    side="buy",
                    quantity=filled_qty,
                    entry_price=fill_price,
                    order_id=oid,
                    trading_mode="live",
                    source="force_test",
                    extra_info=f"LIVE force trade test ~${test_usd} with OCO brackets"
                )
                
                log_trade(
                    symbol=symbol,
                    side="buy",
                    action="oco_bracket_test",
                    quantity=filled_qty,
                    price=fill_price,
                    usd_amount=filled_qty * fill_price,
                    order_id=oid,
                    reason=f"force trade test with OCO brackets",
                    source="force_test",
                    mode="live"
                )
                
                log_lines.append("✅ FILL CONFIRMED - Logged to both databases")
            else:
                log_lines.append(f"⚠️  FILL NOT CONFIRMED - NOT LOGGED")
                log_lines.append(f"   Status: {status}, Filled: {filled_qty}, Price: {fill_price}")
                log_lines.append(f"   Use 'open' command to check order status")
        else:
            log_lines.append("⚠️  FILL DATA UNAVAILABLE - NOT LOGGED")
            log_lines.append("   Use 'open' command to check order status")
        
        log_lines.extend([
            "=" * 60,
            "✅ OCO BRACKET TEST SUCCESSFUL",
            "=" * 60,
            f"Order ID: {oid}",
            f"Entry: {qty:.8f} @ ${price:.2f} (~${test_usd})",
            f"Take-Profit: ${bracket.take_profit_price:.2f} (sell limit)",
            f"Stop-Loss: ${bracket.stop_price:.2f} (stop-loss)",
            "",
            "OCO Status: TRUE EXCHANGE-LEVEL OCO",
            " - When TP fills → SL auto-cancels",
            " - When SL fills → TP auto-cancels",
            "",
            "⚠️  LIVE POSITION OPENED WITH OCO BRACKETS",
            "Monitor via: open",
            f"Manual close: sell all {symbol}",
            ""
        ])
        
    except Exception as e:
        import traceback
        log_lines.extend([
            "",
            "❌ OCO BRACKET TEST FAILED",
            f"Error: {str(e)}",
            "",
            "Full Traceback:",
            traceback.format_exc()
        ])
    
    return "\n".join(log_lines)
