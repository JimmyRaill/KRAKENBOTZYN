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
    
    TWO-STAGE EXECUTION:
    - Stage 1 (Entry): Place market buy order, log IMMEDIATELY to both databases
    - Stage 2 (Protection): Attempt to place TP/SL bracket orders
    
    CRITICAL: Entry logging happens IMMEDIATELY after Kraken confirms, BEFORE
    attempting protection. This prevents "silent success" bugs where entry executes
    but system reports failure due to downstream bracket errors.
    
    SAFETY:
    - Requires ENABLE_FORCE_TRADE=1 in environment
    - Only works in LIVE mode
    - Hard-coded to $15 position size
    - Logs every step with full Kraken responses
    
    Args:
        symbol: Trading pair (default: ETH/USD)
    
    Returns:
        Truthful execution report distinguishing entry vs protection outcomes
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
    from evaluation_log import record_entry_fill, TradeExecutionResult, EntryStatus, ProtectionStatus
    
    if not is_live_mode():
        return (
            "❌ [FORCE-TRADE] Only works in LIVE mode\n"
            f"Current mode: {get_mode_str().upper()}\n"
            "Set KRAKEN_VALIDATE_ONLY=0 to enable LIVE mode"
        )
    
    ex = get_exchange()
    test_usd = 15.0  # Tiny test size
    
    # Initialize execution result tracker
    result = TradeExecutionResult(
        entry_status=EntryStatus.NOT_ATTEMPTED,
        symbol=symbol,
        side="buy",
        trading_mode="live",
        source="force_test"
    )
    
    log_lines = [
        "🧪 [FORCE-TRADE-TEST] Starting LIVE trade test...",
        f"Symbol: {symbol}",
        f"Test Size: ${test_usd}",
        ""
    ]
    
    # ═════════════════════════════════════════════════════════════════════════
    # STAGE 1: ENTRY EXECUTION (Separate try-catch)
    # ═════════════════════════════════════════════════════════════════════════
    try:
        # Step 1: Fetch price
        log_lines.append("Step 1/3 (ENTRY): Fetching market price...")
        ticker = ex.fetch_ticker(symbol)
        price = float(ticker['last'])
        log_lines.append(f"✅ Price: ${price:.2f}")
        log_lines.append("")
        
        # Step 2: Calculate quantity
        log_lines.append("Step 2/3 (ENTRY): Calculating position size...")
        qty = float(test_usd / price)
        base_currency = symbol.split('/')[0]
        log_lines.append(f"✅ Quantity: {qty:.8f} {base_currency}")
        log_lines.append("")
        
        # Step 3: Place market buy order on Kraken
        log_lines.append("Step 3/3 (ENTRY): Placing LIVE market buy order on Kraken...")
        entry_order = ex.create_market_buy_order(symbol, qty)
        entry_id = str(entry_order.get('id') or entry_order.get('orderId', 'NO_ID'))
        actual_filled = entry_order.get("filled") or qty
        actual_price = entry_order.get("average") or entry_order.get("price") or price
        
        log_lines.append(f"✅ ENTRY EXECUTED ON KRAKEN: {entry_id}")
        log_lines.append(f"   Filled: {actual_filled:.8f} @ ${actual_price:.2f}")
        log_lines.append("")
        
        # CRITICAL: Log entry IMMEDIATELY to both databases (before attempting protection)
        log_lines.append("📝 Logging entry to both databases (forensic + telemetry)...")
        logging_result = record_entry_fill(
            symbol=symbol,
            side="buy",
            quantity=actual_filled,
            price=actual_price,
            order_id=entry_id,
            trading_mode="live",
            source="force_test",
            reason="force trade test",
            extra_info=f"LIVE force trade test ~${test_usd}"
        )
        
        if logging_result['forensic_log_success'] and logging_result['telemetry_log_success']:
            log_lines.append("✅ Entry logged to BOTH databases (executed_orders + trades)")
        else:
            log_lines.append(f"⚠️  Partial logging: {logging_result['errors']}")
        log_lines.append("")
        
        # Update result object with entry success
        result.entry_status = EntryStatus.SUCCESS
        result.entry_order_id = entry_id
        result.entry_price = actual_price
        result.entry_quantity = actual_filled
        
    except Exception as e:
        # Entry failed - nothing executed on Kraken
        import traceback
        result.entry_status = EntryStatus.FAILED
        result.errors.append(f"Entry execution failed: {str(e)}")
        
        log_lines.extend([
            "",
            "❌ ENTRY FAILED - NO ORDER PLACED ON KRAKEN",
            f"Error: {str(e)}",
            "",
            "Full Traceback:",
            traceback.format_exc()
        ])
        
        # Return immediately - no point attempting protection
        result.raw_message = "\n".join(log_lines)
        return result.to_user_message() + "\n\n" + result.raw_message
    
    # ═════════════════════════════════════════════════════════════════════════
    # STAGE 2: PROTECTION PLACEMENT (Separate try-catch)
    # ═════════════════════════════════════════════════════════════════════════
    # If we reach here, entry succeeded. Now try to protect it.
    
    log_lines.append("=" * 60)
    log_lines.append("STAGE 2: PROTECTIVE BRACKET PLACEMENT")
    log_lines.append("=" * 60)
    log_lines.append("")
    
    try:
        # Step 4: Calculate SL/TP using ATR
        log_lines.append("Step 1/3 (PROTECTION): Calculating stop-loss and take-profit...")
        from candle_strategy import calculate_atr
        ohlcv = ex.fetch_ohlcv(symbol, '5m', 100)
        atr = calculate_atr(ohlcv, period=14)
        
        if atr is None or atr <= 0:
            raise ValueError("Invalid ATR calculation - cannot determine stop-loss/take-profit")
        
        sl_price = actual_price - (2.0 * float(atr))  # 2x ATR stop-loss
        tp_price = actual_price + (3.0 * float(atr))  # 3x ATR take-profit
        
        log_lines.append(f"✅ ATR: ${atr:.2f}")
        log_lines.append(f"   Stop-Loss: ${sl_price:.2f} (2x ATR below entry)")
        log_lines.append(f"   Take-Profit: ${tp_price:.2f} (3x ATR above entry)")
        log_lines.append("")
        
        # Step 5: Place take-profit order
        log_lines.append("Step 2/3 (PROTECTION): Placing take-profit order...")
        tp_order = ex.create_limit_sell_order(symbol, actual_filled, tp_price)
        tp_id = str(tp_order.get('id') or tp_order.get('orderId', 'NO_ID'))
        log_lines.append(f"✅ Take-Profit Order ID: {tp_id}")
        log_lines.append("")
        
        # Step 6: Place stop-loss order
        log_lines.append("Step 3/3 (PROTECTION): Placing stop-loss order...")
        sl_order = ex.create_order(symbol, 'market', 'sell', actual_filled, None, {'stopPrice': sl_price})
        sl_id = str(sl_order.get('id') or sl_order.get('orderId', 'NO_ID'))
        log_lines.append(f"✅ Stop-Loss Order ID: {sl_id}")
        log_lines.append("")
        
        # Both TP and SL succeeded
        result.protection_status = ProtectionStatus.FULLY_PROTECTED
        result.tp_order_id = tp_id
        result.sl_order_id = sl_id
        
        log_lines.extend([
            "=" * 60,
            "✅ FULLY SUCCESSFUL - ENTRY + PROTECTION",
            "=" * 60,
            f"Entry: {entry_id} ({actual_filled:.8f} @ ${actual_price:.2f})",
            f"Take-Profit: {tp_id}",
            f"Stop-Loss: {sl_id}",
            "",
            "Status: FULLY PROTECTED",
            "",
            "⚠️  LIVE POSITION OPENED WITH BRACKETS",
            "Monitor via: open",
            f"Manual close: sell all {symbol}",
            ""
        ])
        
    except Exception as e:
        # Entry succeeded but protection failed
        import traceback
        result.protection_status = ProtectionStatus.FAILED
        result.errors.append(f"Protection placement failed: {str(e)}")
        
        log_lines.extend([
            "",
            "❌ PROTECTION FAILED - ENTRY SUCCEEDED BUT TP/SL PLACEMENT FAILED",
            f"Error: {str(e)}",
            "",
            "Full Traceback:",
            traceback.format_exc(),
            "",
            "=" * 60,
            "🚨 CRITICAL: NAKED POSITION EXISTS",
            "=" * 60,
            f"✅ Entry Order: {result.entry_order_id}",
            f"   Filled: {result.entry_quantity:.8f} @ ${result.entry_price:.2f}",
            f"   LOGGED TO: executed_orders + trades tables",
            "",
            "❌ NO PROTECTIVE BRACKETS PLACED",
            "",
            "⚠️  YOU MUST MANUALLY CLOSE OR PROTECT THIS POSITION:",
            f"   sell all {symbol}",
            ""
        ])
    
    # Return truthful message based on staged execution
    result.raw_message = "\n".join(log_lines)
    return result.to_user_message() + "\n\n" + result.raw_message
