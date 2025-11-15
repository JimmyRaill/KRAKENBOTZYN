# commands.py — clean, strict router for Kraken via ccxt (CENTRALIZED EXCHANGE)
import os
import re
from typing import Optional

import ccxt
from dotenv import load_dotenv
from exchange_manager import get_exchange, get_mode_str, is_paper_mode
from evaluation_log import log_order_execution, register_pending_child_order

# Load .env from project root
load_dotenv(dotenv_path=".env", override=True)

HELP = (
    "Commands:\n"
    "  price <symbol>                      e.g. price zec/usd\n"
    "  bal                                 show balances\n"
    "  buy <usd> usd <symbol>              e.g. buy 25 usd zec/usd\n"
    "  sell all <symbol>                   e.g. sell all zec/usd\n"
    "  limit buy <symbol> <amount> @ <px>  e.g. limit buy zec/usd 2 @ 29.5\n"
    "  limit sell <symbol> <amount> @ <px> e.g. limit sell zec/usd 1.5 @ 34.2\n"
    "  stop sell <symbol> <amount> @ <stop>\n"
    "  stop buy  <symbol> <amount> @ <stop>\n"
    "  bracket <symbol> <amount> tp <px> sl <px>\n"
    "  open [symbol]\n"
    "  cancel <order_id> [symbol]\n"
    "  debug status                        show diagnostics\n"
    "  force trade test <symbol>           test LIVE order placement (requires ENABLE_FORCE_TRADE=1)\n"
    "  help\n"
)

# ----------------- ccxt bootstrap (CENTRALIZED) -----------------

def _ex():
    """Get the centralized exchange instance - ensures paper/live mode consistency"""
    return get_exchange()

# ----------------- helpers -----------------

def _norm_sym(s: str) -> str:
    s = (s or "").strip().upper().replace(":", "/").replace("-", "/")
    parts = s.split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) == 2 else s

def _safe_float(x: object, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(x)  # type: ignore[arg-type]
    except Exception:
        return default

def _last_price(ex, symbol: str) -> float:
    t = ex.fetch_ticker(symbol)
    last = _safe_float(t.get("last"))
    if last and last > 0:
        return last
    bid = _safe_float(t.get("bid"))
    ask = _safe_float(t.get("ask"))
    if bid and ask and ask > 0:
        return (bid + ask) / 2.0
    raise RuntimeError("no valid price for " + symbol)

def _balances_text(ex) -> str:
    # CRITICAL: Use account_state.get_balances() which handles Kraken asset normalization
    from account_state import get_balances
    
    try:
        balances = get_balances()
        
        # CRITICAL: Handle None response
        if balances is None:
            return "Balances: ERROR - get_balances() returned None"
        
        if not balances:
            return "Balances: (empty - check your account or mode)"
        
        lines = ["Balances:"]
        for currency, bal in sorted(balances.items()):
            total = bal.get('total', 0.0)
            usd_value = bal.get('usd_value', 0.0)
            if total > 0:
                if currency == 'USD':
                    lines.append(f"  {currency}: ${total:.2f}")
                else:
                    lines.append(f"  {currency}: {total:.8f} (${usd_value:.2f})")
        
        # Calculate total equity
        total_equity = sum(bal.get('usd_value', 0) for bal in balances.values())
        lines.append(f"\nTotal Portfolio Value: ${total_equity:.2f}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"[BAL-ERR] {e}"

def _free_coin_qty(ex, symbol: str) -> float:
    base = _norm_sym(symbol).split("/")[0].replace("-", "")
    bal = ex.fetch_balance()
    qty: Optional[float] = None
    free = bal.get("free") or {}
    if base in free:
        qty = _safe_float(free.get(base), None)
    if qty is None:
        qty = _safe_float((bal.get(base) or {}).get("total"), None)
    return qty or 0.0

def _open_orders_text(ex, symbol_filter: str | None = None) -> str:
    from exchange_manager import get_mode_str
    
    mode = get_mode_str()
    orders = ex.fetch_open_orders(symbol_filter) if symbol_filter else ex.fetch_open_orders()
    order_ids = [o['id'] for o in orders]
    
    # DIAGNOSTIC: Log what this path sees
    print(f"[OPEN CMD] mode={mode}, ex={type(ex).__name__}, open_order_ids={order_ids}")
    
    if not orders:
        return "(no open orders)"
    lines = []
    for o in orders:
        sid = str(o.get("id") or o.get("orderId") or "?")
        sym = o.get("symbol") or ""
        side = o.get("side") or ""
        typ = o.get("type") or ""
        amt = _safe_float(o.get("amount"), 0.0) or 0.0
        px = _safe_float(o.get("price"), 0.0) or 0.0
        status = o.get("status") or ""
        lines.append(f"{sid} | {sym} | {side} {typ} {amt} @ {px} | {status}")
    return "\n".join(lines)

def _trade_history_text(ex, symbol_filter: str | None = None, limit: int = 20) -> str:
    """
    Fetch and display recent trade history using fetch_my_trades().
    CRITICAL: This is separate from open orders - uses actual trade execution data.
    """
    from exchange_manager import get_mode_str
    from datetime import datetime
    
    mode = get_mode_str()
    
    try:
        # Fetch trade history from exchange
        if symbol_filter:
            trades = ex.fetch_my_trades(symbol=symbol_filter, limit=limit)
        else:
            trades = ex.fetch_my_trades(limit=limit)
        
        # DIAGNOSTIC: Log what this path sees
        print(f"[HISTORY CMD] mode={mode}, ex={type(ex).__name__}, trades_count={len(trades)}")
        
        if not trades:
            return "(no trade history)"
        
        lines = [f"Recent trades (limit {limit}):"]
        for t in trades:
            tid = str(t.get("id") or "?")
            sym = t.get("symbol") or ""
            side = t.get("side") or ""
            amt = _safe_float(t.get("amount"), 0.0) or 0.0
            px = _safe_float(t.get("price"), 0.0) or 0.0
            cost = _safe_float(t.get("cost"), 0.0) or 0.0
            timestamp = t.get("timestamp")
            
            # Format timestamp if available
            time_str = ""
            if timestamp:
                try:
                    dt = datetime.fromtimestamp(timestamp / 1000)  # ms to seconds
                    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    time_str = str(timestamp)
            
            lines.append(f"{tid} | {time_str} | {sym} | {side} {amt} @ ${px:.2f} | Cost: ${cost:.2f}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"[HISTORY-ERR] {e}"

def _ensure_min_cost(ex, symbol: str, amount: float, price: float) -> float:
    """
    Kraken enforces a min notional per order (market & limit).
    If current amount*price < min_cost, bump amount to min_cost/price.
    """
    m = ex.market(symbol) or {}
    lim = m.get("limits") or {}
    min_cost = _safe_float((lim.get("cost") or {}).get("min"), 0.0) or 0.0
    # Guard against division by zero
    if price and price > 0 and min_cost and amount * price < min_cost:
        amount = min_cost / price
    # also respect min amount if present
    min_amt = _safe_float((lim.get("amount") or {}).get("min"), 0.0) or 0.0
    if min_amt and amount < min_amt:
        amount = min_amt
    return amount

def _create_stop_market(ex, symbol: str, side: str, amount: float, stop_px: float):
    """
    Native stop-market using ccxt unified create_order with stopPrice.
    Logs full Kraken errors for debugging bracket failures.
    """
    amt = _safe_float(ex.amount_to_precision(symbol, amount), None)
    stp = _safe_float(ex.price_to_precision(symbol, stop_px), None)
    if amt is None or amt <= 0 or stp is None or stp <= 0:
        raise ValueError("bad stop params")
    
    params = {"stopPrice": stp, "trigger": "last"}  # Kraken via ccxt
    
    # Log attempt with full details
    print(f"[SL-CREATE-ATTEMPT] {symbol} | side={side} | amount={amt} | stop_price={stp} | params={params}")
    
    try:
        order = ex.create_order(symbol, "market", side, float(amt), None, params)
        order_id = order.get("id") or order.get("orderId") or "unknown"
        print(f"[SL-CREATE-SUCCESS] {symbol} stop-loss order placed | id={order_id}")
        return order
    except Exception as e:
        # Log FULL Kraken error (not truncated)
        print(f"[SL-CREATE-ERROR] {symbol} stop-loss FAILED | Error type: {type(e).__name__}")
        print(f"[SL-CREATE-ERROR] Full error: {repr(e)}")
        
        # Try to log to evaluation_log for forensics
        try:
            from evaluation_log import log_evaluation
            log_evaluation(
                symbol=symbol,
                decision="SL_ORDER_FAILED",
                reason=f"Stop-loss order creation failed: {side} {amt} @ stop ${stp}",
                error_message=f"{type(e).__name__}: {str(e)}"
            )
        except Exception as log_err:
            print(f"[SL-CREATE-ERROR] Could not log to evaluation_log: {log_err}")
        
        # Re-raise the original exception (preserves Kraken error message)
        raise


def _place_tp_and_sl_with_retry(ex, sym, fill_size, tp_p, sl_p, side, max_retries=3, delay_sec=2):
    """
    Place TP and SL orders with retry logic.
    
    CRITICAL: If TP succeeds but SL fails, this function raises an exception BUT
    the caller can still access the tp_order via exception handling by checking
    if it was assigned before the SL failure.
    
    Args:
        ex: Exchange instance
        sym: Symbol
        fill_size: Filled quantity
        tp_p: Take-profit price
        sl_p: Stop-loss price
        side: 'long' or 'short'
        max_retries: Maximum retry attempts (default 3)
        delay_sec: Delay between retries in seconds (default 2)
    
    Returns:
        (tp_order, sl_order) on success
    
    Raises:
        Exception if all retries fail (preserves original Kraken error)
    """
    import time
    
    last_err = None
    tp_order = None  # Track TP order across retries
    
    for attempt in range(1, max_retries + 1):
        try:
            if side == 'long':
                # LONG: Sell TP and SL
                # Create TP first (limit order, rarely fails)
                tp_order = ex.create_limit_sell_order(sym, float(fill_size), float(tp_p))
                tp_id = tp_order.get("id") or tp_order.get("orderId") or "unknown"
                print(f"[BRACKET-RETRY] TP order placed: {tp_id} @ ${tp_p}")
                
                # Create SL (stop-market, more likely to fail due to trigger price issues)
                sl_order = _create_stop_market(ex, sym, "sell", float(fill_size), float(sl_p))
                sl_id = sl_order.get("id") or sl_order.get("orderId") or "unknown"
                print(f"[BRACKET-RETRY] SL order placed: {sl_id} @ stop ${sl_p}")
            else:
                # SHORT: Buy TP and SL
                tp_order = ex.create_limit_buy_order(sym, float(fill_size), float(tp_p))
                tp_id = tp_order.get("id") or tp_order.get("orderId") or "unknown"
                print(f"[BRACKET-RETRY] TP order placed: {tp_id} @ ${tp_p}")
                
                sl_order = _create_stop_market(ex, sym, "buy", float(fill_size), float(sl_p))
                sl_id = sl_order.get("id") or sl_order.get("orderId") or "unknown"
                print(f"[BRACKET-RETRY] SL order placed: {sl_id} @ stop ${sl_p}")
            
            # Success!
            if attempt > 1:
                print(f"✅ [BRACKET-RETRY] Success on attempt {attempt} for {sym} TP/SL")
            return tp_order, sl_order
        
        except Exception as e:
            last_err = e
            
            # If TP was created but SL failed, we have a problem
            if tp_order:
                tp_id = tp_order.get("id") or tp_order.get("orderId")
                print(f"⚠️  [BRACKET-RETRY] Attempt {attempt}/{max_retries}: TP created ({tp_id}) but SL failed: {e}")
                
                # Cancel the TP order before retrying (prevent orphans during retries)
                if tp_id and attempt < max_retries:
                    try:
                        print(f"[BRACKET-RETRY] Canceling TP {tp_id} before retry")
                        ex.cancel_order(tp_id, sym)
                        tp_order = None  # Reset for next attempt
                    except Exception as cancel_err:
                        print(f"[BRACKET-RETRY] Failed to cancel TP {tp_id}: {cancel_err}")
            else:
                print(f"⚠️  [BRACKET-RETRY] Attempt {attempt}/{max_retries} failed for {sym} TP/SL: {e}")
            
            if attempt < max_retries:
                print(f"    Retrying in {delay_sec}s...")
                time.sleep(delay_sec)
            else:
                print(f"❌ [BRACKET-RETRY] All {max_retries} attempts failed for {sym} TP/SL")
                # If TP exists on final failure, attach it to exception for caller to handle
                if tp_order:
                    # Store tp_order in exception for rollback handler to access
                    last_err.tp_order = tp_order
    
    # All retries exhausted - raise with tp_order attached if it exists
    raise last_err

# ----------------- public router -----------------

def handle(text: str) -> str:
    if not text:
        return HELP

    s = text.strip()
    if s.lower() in ("help", "h", "?"):
        return HELP

    ex = _ex()
    
    # Debug status command
    if s.lower() in ("debug status", "show diagnostics", "status"):
        from commands_addon import _debug_status
        return _debug_status()
    
    # Force trade test command
    if s.lower().startswith("force trade test"):
        from commands_addon import _force_trade_test
        parts = s.split()
        symbol = _norm_sym(parts[3]) if len(parts) > 3 else "ETH/USD"
        return _force_trade_test(symbol)

    # bal
    if s.lower() == "bal":
        try:
            return _balances_text(ex)
        except Exception as e:
            return f"[BAL-ERR] {e}"

    # price <symbol>
    m = re.fullmatch(r"(?i)price\s+([A-Za-z0-9:/\-\._]+)", s)
    if m:
        sym = _norm_sym(m.group(1))
        try:
            px = _last_price(ex, sym)
            return f"{sym} last price: {px}"
        except Exception as e:
            return f"[PRICE-ERR] {e}"

    # buy <usd> usd <symbol>
    m = re.fullmatch(r"(?i)buy\s+([0-9]+(?:\.[0-9]+)?)\s*usd\s+([A-Za-z0-9:/\-\._]+)", s)
    if m:
        usd = _safe_float(m.group(1), None)
        sym = _norm_sym(m.group(2))
        if usd is None or usd <= 0:
            return "[BUY-ERR] invalid usd amount"
        try:
            px = _last_price(ex, sym)
            amt = usd / px
            amt = _ensure_min_cost(ex, sym, amt, px)
            amt = _safe_float(ex.amount_to_precision(sym, amt), None)
            if amt is None or amt <= 0:
                return "[BUY-ERR] amount precision produced zero"
            order = ex.create_market_buy_order(sym, float(amt))
            oid = str(order.get("id") or order.get("orderId") or "<no-id>")
            
            # Log executed order for TRUTH VERIFICATION - use ACTUAL fill data from exchange
            # CRITICAL: NEVER fall back to requested amounts - only log exchange-confirmed fills
            actual_filled = _safe_float(order.get("filled"), None)
            actual_avg_price = _safe_float(order.get("average") or order.get("price"), None)
            order_status = order.get("status", "unknown")
            remaining = _safe_float(order.get("remaining"), None)
            
            # STRICT: Only log if fully filled (status closed/filled AND remaining=0 AND we have actual fill data)
            # Never log partial fills or use requested quantity fallbacks
            is_fully_filled = (
                order_status in ["closed", "filled"] and
                (remaining is None or remaining == 0) and
                actual_filled is not None and actual_filled > 0 and
                actual_avg_price is not None
            )
            
            if is_fully_filled:
                log_order_execution(
                    symbol=sym,
                    side="buy",
                    quantity=actual_filled,
                    entry_price=actual_avg_price,
                    order_id=oid,
                    trading_mode=get_mode_str().lower(),
                    source="command",
                    extra_info=f"market buy ~${usd:.2f} status={order_status}"
                )
            
            return f"BUY OK {sym} ~${usd:.2f} (qty≈{amt}) id={oid}"
        except Exception as e:
            return f"[BUY-ERR] {e}"

    # sell all <symbol>
    m = re.fullmatch(r"(?i)sell\s+all\s+([A-Za-z0-9:/\-\._]+)", s)
    if m:
        sym = _norm_sym(m.group(1))
        try:
            qty = _free_coin_qty(ex, sym)
            if not qty or qty <= 0:
                return "[SELL-ERR] no position/qty"
            qtp = ex.amount_to_precision(sym, qty)
            qf = _safe_float(qtp, None)
            if qf is None or qf <= 0:
                return "[SELL-ERR] amount precision produced zero"
            order = ex.create_market_sell_order(sym, float(qf))
            oid = str(order.get("id") or order.get("orderId") or "<no-id>")
            
            # Log executed order for TRUTH VERIFICATION - use ACTUAL fill data from exchange
            # CRITICAL: NEVER fall back to requested amounts - only log exchange-confirmed fills
            actual_filled = _safe_float(order.get("filled"), None)
            actual_avg_price = _safe_float(order.get("average") or order.get("price"), None)
            order_status = order.get("status", "unknown")
            remaining = _safe_float(order.get("remaining"), None)
            
            # STRICT: Only log if fully filled (status closed/filled AND remaining=0 AND we have actual fill data)
            # Never log partial fills or use requested quantity fallbacks
            is_fully_filled = (
                order_status in ["closed", "filled"] and
                (remaining is None or remaining == 0) and
                actual_filled is not None and actual_filled > 0 and
                actual_avg_price is not None
            )
            
            if is_fully_filled:
                log_order_execution(
                    symbol=sym,
                    side="sell",
                    quantity=actual_filled,
                    entry_price=actual_avg_price,
                    order_id=oid,
                    trading_mode=get_mode_str().lower(),
                    source="command",
                    extra_info=f"market sell all status={order_status}"
                )
            
            return f"SELL OK {sym} qty={qf} id={oid}"
        except Exception as e:
            return f"[SELL-ERR] {e}"

    # limit buy <symbol> <amount> @ <px>
    m = re.fullmatch(
        r"(?i)limit\s+buy\s+([A-Za-z0-9:/\-\._]+)\s+([0-9]+(?:\.[0-9]+)?)\s*@\s*([0-9]+(?:\.[0-9]+)?)",
        s,
    )
    if m:
        sym = _norm_sym(m.group(1))
        amt = _safe_float(m.group(2), None)
        px = _safe_float(m.group(3), None)
        if amt is None or amt <= 0 or px is None or px <= 0:
            return "[LIMIT-BUY-ERR] bad amount or price"
        try:
            amt = _ensure_min_cost(ex, sym, amt, px)
            amt_p = _safe_float(ex.amount_to_precision(sym, amt), None)
            px_p = _safe_float(ex.price_to_precision(sym, px), None)
            if amt_p is None or amt_p <= 0 or px_p is None or px_p <= 0:
                return "[LIMIT-BUY-ERR] precision produced zero"
            order = ex.create_limit_buy_order(sym, float(amt_p), float(px_p))
            oid = str(order.get("id") or order.get("orderId") or "<no-id>")
            return f"LIMIT BUY OK {sym} {amt_p} @ {px_p} id={oid}"
        except Exception as e:
            return f"[LIMIT-BUY-ERR] {e}"

    # limit sell <symbol> <amount> @ <px>
    m = re.fullmatch(
        r"(?i)limit\s+sell\s+([A-Za-z0-9:/\-\._]+)\s+([0-9]+(?:\.[0-9]+)?)\s*@\s*([0-9]+(?:\.[0-9]+)?)",
        s,
    )
    if m:
        sym = _norm_sym(m.group(1))
        amt = _safe_float(m.group(2), None)
        px = _safe_float(m.group(3), None)
        if amt is None or amt <= 0 or px is None or px <= 0:
            return "[LIMIT-SELL-ERR] bad amount or price"
        try:
            amt_p = _safe_float(ex.amount_to_precision(sym, amt), None)
            px_p  = _safe_float(ex.price_to_precision(sym, px), None)
            if amt_p is None or amt_p <= 0 or px_p is None or px_p <= 0:
                return "[LIMIT-SELL-ERR] precision produced zero"
            order = ex.create_limit_sell_order(sym, float(amt_p), float(px_p))
            oid = str(order.get("id") or order.get("orderId") or "<no-id>")
            return f"LIMIT SELL OK {sym} {amt_p} @ {px_p} id={oid}"
        except Exception as e:
            return f"[LIMIT-SELL-ERR] {e}"

    # stop sell <symbol> <amount> @ <stop>
    m = re.fullmatch(
        r"(?i)stop\s+sell\s+([A-Za-z0-9:/\-\._]+)\s+([0-9]+(?:\.[0-9]+)?)\s*@\s*([0-9]+(?:\.[0-9]+)?)",
        s,
    )
    if m:
        sym = _norm_sym(m.group(1))
        amt = _safe_float(m.group(2), None)
        stp = _safe_float(m.group(3), None)
        if amt is None or amt <= 0 or stp is None or stp <= 0:
            return "[STOP-SELL-ERR] bad amount or stop"
        try:
            o = _create_stop_market(ex, sym, "sell", float(amt), float(stp))
            oid = str(o.get("id") or o.get("orderId") or "<no-id>")
            return f"STOP SELL OK {sym} {amt} @ stop {stp} id={oid}"
        except Exception as e:
            return f"[STOP-SELL-ERR] {e}"

    # stop buy <symbol> <amount> @ <stop>
    m = re.fullmatch(
        r"(?i)stop\s+buy\s+([A-Za-z0-9:/\-\._]+)\s+([0-9]+(?:\.[0-9]+)?)\s*@\s*([0-9]+(?:\.[0-9]+)?)",
        s,
    )
    if m:
        sym = _norm_sym(m.group(1))
        amt = _safe_float(m.group(2), None)
        stp = _safe_float(m.group(3), None)
        if amt is None or amt <= 0 or stp is None or stp <= 0:
            return "[STOP-BUY-ERR] bad amount or stop"
        try:
            o = _create_stop_market(ex, sym, "buy", float(amt), float(stp))
            oid = str(o.get("id") or o.get("orderId") or "<no-id>")
            return f"STOP BUY OK {sym} {amt} @ stop {stp} id={oid}"
        except Exception as e:
            return f"[STOP-BUY-ERR] {e}"

    # bracket <symbol> <amount> tp <px> sl <px>
    # FIXED: Now creates entry order + TP + SL (complete bracket)
    m = re.fullmatch(
        r"(?i)bracket\s+([A-Za-z0-9:/\-\._]+)\s+([0-9]+(?:\.[0-9]+)?)\s+tp\s+([0-9]+(?:\.[0-9]+)?)\s+sl\s+([0-9]+(?:\.[0-9]+)?)",
        s,
    )
    if m:
        sym = _norm_sym(m.group(1))
        amt = _safe_float(m.group(2), None)
        tp  = _safe_float(m.group(3), None)
        sl  = _safe_float(m.group(4), None)
        if any(x is None or x <= 0 for x in (amt, tp, sl)):
            return "[BRACKET-ERR] bad amount or prices"
        try:
            amt_p = _safe_float(ex.amount_to_precision(sym, amt), None)
            tp_p  = _safe_float(ex.price_to_precision(sym, tp), None)
            sl_p  = _safe_float(ex.price_to_precision(sym, sl), None)
            if (amt_p is None or amt_p <= 0) or (tp_p is None or tp_p <= 0) or (sl_p is None or sl_p <= 0):
                return "[BRACKET-ERR] precision produced zero"
            
            # CRITICAL FIX: Create market BUY entry order first
            # TP > current price means LONG position (buy entry)
            current_price = _last_price(ex, sym)
            
            # Determine direction from TP/SL relative to current price
            is_long = tp_p > current_price
            
            # STRICT VALIDATION: Ensure TP/SL are on correct sides with tolerance
            # Use tick size for precision (assume $0.01 minimum separation)
            min_sep = max(current_price * 0.001, 0.01)  # 0.1% or $0.01 minimum
            
            if is_long:
                # LONG: TP must be above, SL must be below
                if tp_p <= current_price + min_sep:
                    return f"[BRACKET-ERR] LONG TP must be above market (TP=${tp_p:.2f}, market=${current_price:.2f})"
                if sl_p >= current_price - min_sep:
                    return f"[BRACKET-ERR] LONG SL must be below market (SL=${sl_p:.2f}, market=${current_price:.2f})"
            else:
                # SHORT: TP must be below, SL must be above
                if tp_p >= current_price - min_sep:
                    return f"[BRACKET-ERR] SHORT TP must be below market (TP=${tp_p:.2f}, market=${current_price:.2f})"
                if sl_p <= current_price + min_sep:
                    return f"[BRACKET-ERR] SHORT SL must be above market (SL=${sl_p:.2f}, market=${current_price:.2f})"
            
            # Execute bracket with post-fill validation and rollback protection
            entry_order = None
            tp_order = None
            sl_order = None
            
            try:
                if is_long:
                    # LONG: Market buy entry
                    entry_order = ex.create_market_buy_order(sym, float(amt_p))
                    entry_id = str(entry_order.get("id") or entry_order.get("orderId") or "<no-id>")
                    side_str = "LONG"
                    
                    # Get actual fill price and size - use fetch_order for authoritative data
                    fill_price = _safe_float(entry_order.get("price") or entry_order.get("average"), None)
                    fill_size = _safe_float(entry_order.get("filled") or entry_order.get("amount"), None)
                    
                    # Fallback: fetch_order if immediate response lacks fill data
                    # Note: Skip fetch_order in paper mode since paper orders return complete data
                    if (not fill_price or not fill_size) and not is_paper_mode():
                        try:
                            fetched = ex.fetch_order(entry_id, sym)
                            fill_price = _safe_float(fetched.get("price") or fetched.get("average"), None)
                            fill_size = _safe_float(fetched.get("filled") or fetched.get("amount"), amt_p)
                        except Exception as fetch_err:
                            # Could not get fill data - close position defensively
                            print(f"[BRACKET-ABORT] fetch_order failed: {fetch_err} - closing position")
                            ex.create_market_sell_order(sym, float(amt_p))
                            return f"[BRACKET-ERR] Entry executed but could not verify fill data - position closed for safety"
                    
                    # Use fill_size or fall back to requested amount
                    fill_size = fill_size or amt_p
                    
                    # POST-FILL VALIDATION: Ensure TP/SL still valid after fill
                    if fill_price:
                        if tp_p <= fill_price:
                            print(f"[BRACKET-ABORT] LONG TP ${tp_p} below/at fill ${fill_price} - closing position")
                            ex.create_market_sell_order(sym, float(fill_size))
                            return f"[BRACKET-ERR] Entry filled at ${fill_price:.2f} but TP ${tp_p:.2f} is not above - position closed for safety (slippage detected)"
                        if sl_p >= fill_price:
                            print(f"[BRACKET-ABORT] LONG SL ${sl_p} above/at fill ${fill_price} - closing position")
                            ex.create_market_sell_order(sym, float(fill_size))
                            return f"[BRACKET-ERR] Entry filled at ${fill_price:.2f} but SL ${sl_p:.2f} is not below - position closed for safety (slippage detected)"
                    
                    # Create protective orders using ACTUAL fill size with retry logic
                    tp_order = None
                    sl_order = None
                    try:
                        tp_order, sl_order = _place_tp_and_sl_with_retry(
                            ex, sym, fill_size, tp_p, sl_p, side='long', 
                            max_retries=3, delay_sec=2
                        )
                        
                        # Register TP/SL orders for monitoring
                        trading_mode = get_mode_str().lower()
                        tp_id = tp_order.get("id") or tp_order.get("orderId")
                        sl_id = sl_order.get("id") or sl_order.get("orderId")
                        
                        if tp_id:
                            register_pending_child_order(
                                symbol=sym,
                                order_id=tp_id,
                                order_type="tp",
                                parent_order_id=entry_id,
                                side="sell",
                                quantity=fill_size,
                                limit_price=tp_p,
                                trading_mode=trading_mode
                            )
                        
                        if sl_id:
                            register_pending_child_order(
                                symbol=sym,
                                order_id=sl_id,
                                order_type="sl",
                                parent_order_id=entry_id,
                                side="sell",
                                quantity=fill_size,
                                limit_price=sl_p,
                                trading_mode=trading_mode
                            )
                        
                    except Exception as protect_err:
                        # ROLLBACK: TP/SL creation failed - must cleanup completely
                        print(f"[BRACKET-ROLLBACK] TP/SL creation failed after retries: {protect_err}")
                        
                        # Track rollback success for accurate reporting
                        tp_canceled = False
                        position_closed = False
                        rollback_errors = []
                        
                        # Step 1: Cancel TP order if it was created (prevents orphan TP)
                        # Check both local tp_order and exception-attached tp_order
                        tp_to_cancel = tp_order or getattr(protect_err, 'tp_order', None)
                        if tp_to_cancel:
                            tp_id = tp_to_cancel.get("id") or tp_to_cancel.get("orderId")
                            if tp_id:
                                try:
                                    print(f"[BRACKET-ROLLBACK] Canceling orphan TP order {tp_id} for {sym}")
                                    ex.cancel_order(tp_id, sym)
                                    print(f"[BRACKET-ROLLBACK] ✅ TP order {tp_id} canceled successfully")
                                    tp_canceled = True
                                except Exception as cancel_err:
                                    err_msg = f"Failed to cancel TP {tp_id}: {repr(cancel_err)}"
                                    print(f"[BRACKET-ROLLBACK] ⚠️ {err_msg}")
                                    rollback_errors.append(err_msg)
                        
                        # Step 2: Close position (market-sell the entry)
                        try:
                            print(f"[BRACKET-ROLLBACK] Closing {sym} position with market sell of {fill_size}")
                            ex.create_market_sell_order(sym, float(fill_size))
                            print(f"[BRACKET-ROLLBACK] ✅ Position closed successfully")
                            position_closed = True
                        except Exception as close_err:
                            err_msg = f"CRITICAL: Failed to close position {sym}: {repr(close_err)}"
                            print(f"[BRACKET-ROLLBACK] 🚨 {err_msg}")
                            rollback_errors.append(err_msg)
                            
                            # Log critical failure to evaluation_log for alerting
                            try:
                                from evaluation_log import log_evaluation
                                log_evaluation(
                                    symbol=sym,
                                    decision="ROLLBACK_FAILED",
                                    reason="Bracket rollback could not close position - MANUAL INTERVENTION REQUIRED",
                                    error_message=err_msg
                                )
                            except:
                                pass
                        
                        # Build truthful error message based on actual outcomes
                        rollback_status = []
                        if tp_to_cancel:
                            rollback_status.append(f"TP cancel: {'✅ SUCCESS' if tp_canceled else '❌ FAILED'}")
                        rollback_status.append(f"Position close: {'✅ SUCCESS' if position_closed else '❌ FAILED'}")
                        
                        rollback_summary = ", ".join(rollback_status)
                        error_details = " | ".join(rollback_errors) if rollback_errors else "See logs"
                        
                        return f"[BRACKET-ERR] Bracket FAILED for {sym}: Entry filled, TP placed, SL failed after 3 retries. Rollback: {rollback_summary}. Errors: {error_details}. Original error: {protect_err}"
                else:
                    # SHORT: Market sell entry
                    entry_order = ex.create_market_sell_order(sym, float(amt_p))
                    entry_id = str(entry_order.get("id") or entry_order.get("orderId") or "<no-id>")
                    side_str = "SHORT"
                    
                    # Get actual fill price and size - use fetch_order for authoritative data
                    fill_price = _safe_float(entry_order.get("price") or entry_order.get("average"), None)
                    fill_size = _safe_float(entry_order.get("filled") or entry_order.get("amount"), None)
                    
                    # Fallback: fetch_order if immediate response lacks fill data
                    # Note: Skip fetch_order in paper mode since paper orders return complete data
                    if (not fill_price or not fill_size) and not is_paper_mode():
                        try:
                            fetched = ex.fetch_order(entry_id, sym)
                            fill_price = _safe_float(fetched.get("price") or fetched.get("average"), None)
                            fill_size = _safe_float(fetched.get("filled") or fetched.get("amount"), amt_p)
                        except Exception as fetch_err:
                            # Could not get fill data - close position defensively
                            print(f"[BRACKET-ABORT] fetch_order failed: {fetch_err} - closing position")
                            ex.create_market_buy_order(sym, float(amt_p))
                            return f"[BRACKET-ERR] Entry executed but could not verify fill data - position closed for safety"
                    
                    # Use fill_size or fall back to requested amount
                    fill_size = fill_size or amt_p
                    
                    # POST-FILL VALIDATION: Ensure TP/SL still valid after fill
                    if fill_price:
                        if tp_p >= fill_price:
                            print(f"[BRACKET-ABORT] SHORT TP ${tp_p} above/at fill ${fill_price} - closing position")
                            ex.create_market_buy_order(sym, float(fill_size))
                            return f"[BRACKET-ERR] Entry filled at ${fill_price:.2f} but TP ${tp_p:.2f} is not below - position closed for safety (slippage detected)"
                        if sl_p <= fill_price:
                            print(f"[BRACKET-ABORT] SHORT SL ${sl_p} below/at fill ${fill_price} - closing position")
                            ex.create_market_buy_order(sym, float(fill_size))
                            return f"[BRACKET-ERR] Entry filled at ${fill_price:.2f} but SL ${sl_p:.2f} is not above - position closed for safety (slippage detected)"
                    
                    # Create protective orders using ACTUAL fill size with retry logic
                    tp_order = None
                    sl_order = None
                    try:
                        tp_order, sl_order = _place_tp_and_sl_with_retry(
                            ex, sym, fill_size, tp_p, sl_p, side='short', 
                            max_retries=3, delay_sec=2
                        )
                        
                        # Register TP/SL orders for monitoring
                        trading_mode = get_mode_str().lower()
                        tp_id = tp_order.get("id") or tp_order.get("orderId")
                        sl_id = sl_order.get("id") or sl_order.get("orderId")
                        
                        if tp_id:
                            register_pending_child_order(
                                symbol=sym,
                                order_id=tp_id,
                                order_type="tp",
                                parent_order_id=entry_id,
                                side="buy",
                                quantity=fill_size,
                                limit_price=tp_p,
                                trading_mode=trading_mode
                            )
                        
                        if sl_id:
                            register_pending_child_order(
                                symbol=sym,
                                order_id=sl_id,
                                order_type="sl",
                                parent_order_id=entry_id,
                                side="buy",
                                quantity=fill_size,
                                limit_price=sl_p,
                                trading_mode=trading_mode
                            )
                        
                    except Exception as protect_err:
                        # ROLLBACK: TP/SL creation failed - must cleanup completely
                        print(f"[BRACKET-ROLLBACK] TP/SL creation failed after retries: {protect_err}")
                        
                        # Track rollback success for accurate reporting
                        tp_canceled = False
                        position_closed = False
                        rollback_errors = []
                        
                        # Step 1: Cancel TP order if it was created (prevents orphan TP)
                        # Check both local tp_order and exception-attached tp_order
                        tp_to_cancel = tp_order or getattr(protect_err, 'tp_order', None)
                        if tp_to_cancel:
                            tp_id = tp_to_cancel.get("id") or tp_to_cancel.get("orderId")
                            if tp_id:
                                try:
                                    print(f"[BRACKET-ROLLBACK] Canceling orphan TP order {tp_id} for {sym}")
                                    ex.cancel_order(tp_id, sym)
                                    print(f"[BRACKET-ROLLBACK] ✅ TP order {tp_id} canceled successfully")
                                    tp_canceled = True
                                except Exception as cancel_err:
                                    err_msg = f"Failed to cancel TP {tp_id}: {repr(cancel_err)}"
                                    print(f"[BRACKET-ROLLBACK] ⚠️ {err_msg}")
                                    rollback_errors.append(err_msg)
                        
                        # Step 2: Close position (market-buy to cover short)
                        try:
                            print(f"[BRACKET-ROLLBACK] Closing {sym} SHORT position with market buy of {fill_size}")
                            ex.create_market_buy_order(sym, float(fill_size))
                            print(f"[BRACKET-ROLLBACK] ✅ Position closed successfully")
                            position_closed = True
                        except Exception as close_err:
                            err_msg = f"CRITICAL: Failed to close SHORT position {sym}: {repr(close_err)}"
                            print(f"[BRACKET-ROLLBACK] 🚨 {err_msg}")
                            rollback_errors.append(err_msg)
                            
                            # Log critical failure to evaluation_log for alerting
                            try:
                                from evaluation_log import log_evaluation
                                log_evaluation(
                                    symbol=sym,
                                    decision="ROLLBACK_FAILED",
                                    reason="Bracket rollback could not close SHORT position - MANUAL INTERVENTION REQUIRED",
                                    error_message=err_msg
                                )
                            except:
                                pass
                        
                        # Build truthful error message based on actual outcomes
                        rollback_status = []
                        if tp_to_cancel:
                            rollback_status.append(f"TP cancel: {'✅ SUCCESS' if tp_canceled else '❌ FAILED'}")
                        rollback_status.append(f"Position close: {'✅ SUCCESS' if position_closed else '❌ FAILED'}")
                        
                        rollback_summary = ", ".join(rollback_status)
                        error_details = " | ".join(rollback_errors) if rollback_errors else "See logs"
                        
                        return f"[BRACKET-ERR] Bracket FAILED for {sym}: Entry filled, TP placed, SL failed after 3 retries. Rollback: {rollback_summary}. Errors: {error_details}. Original error: {protect_err}"
                
                tid = str(tp_order.get("id") or tp_order.get("orderId") or "<no-id>")
                sid = str(sl_order.get("id") or sl_order.get("orderId") or "<no-id>")
                
                # Log executed order for TRUTH VERIFICATION
                # CRITICAL: Use ACTUAL fill data from entry_order, not requested amounts
                # fill_price and fill_size were already extracted from entry_order above (lines 540-558)
                entry_status = entry_order.get("status", "unknown")
                entry_remaining = _safe_float(entry_order.get("remaining"), None)
                
                # STRICT: Only log if entry was FULLY filled (status closed/filled AND remaining=0)
                # Never log if we don't have actual exchange-confirmed fill data
                is_entry_filled = (
                    entry_status in ["closed", "filled"] and
                    (entry_remaining is None or entry_remaining == 0) and
                    fill_size is not None and fill_size > 0 and
                    fill_price is not None
                )
                
                if is_entry_filled:
                    log_order_execution(
                        symbol=sym,
                        side="buy" if is_long else "sell",
                        quantity=fill_size,  # Already validated above as actual fill size from entry_order
                        entry_price=fill_price,  # Already validated as actual fill price from entry_order
                        order_id=entry_id,
                        trading_mode=get_mode_str().lower(),
                        source="command",
                        extra_info=f"bracket {side_str} TP=${tp_p} SL=${sl_p} tp_id={tid} sl_id={sid} status={entry_status}"
                    )
                # NOTE: TP/SL fills are NOT logged here - they're limit orders that execute later
                # Future enhancement: Add monitoring system to log TP/SL executions when they fill
                
                return (f"BRACKET OK {side_str} {sym} amt={amt_p}\n"
                       f"  Entry: {side_str} @ market, id={entry_id}\n"
                       f"  TP: {tp_p} id={tid}\n"
                       f"  SL: {sl_p} id={sid}")
            
            except Exception as entry_err:
                # Entry itself failed - no rollback needed
                return f"[BRACKET-ERR] Entry order failed: {entry_err}"
        except Exception as e:
            return f"[BRACKET-ERR] {e}"

    # open [symbol]
    m = re.fullmatch(r"(?i)open(?:\s+([A-Za-z0-9:/\-\._]+))?", s)
    if m:
        f = m.group(1)
        try:
            # DIAGNOSTIC: Log exchange instance type
            ex_type = type(ex).__name__
            mode = get_mode_str()
            print(f"[CMD-OPEN-DEBUG] Mode={mode} | Exchange type: {ex_type}")
            
            sym = _norm_sym(f) if f else None
            return _open_orders_text(ex, sym)
        except Exception as e:
            return f"[OPEN-ERR] {e}"

    # history [symbol] [limit]
    # Examples: "history", "history BTC/USD", "history BTC/USD 50"
    m = re.fullmatch(r"(?i)history(?:\s+([A-Za-z0-9:/\-\._]+))?(?:\s+(\d+))?", s)
    if m:
        sym_filter = m.group(1)
        limit_str = m.group(2)
        try:
            sym = _norm_sym(sym_filter) if sym_filter else None
            limit = int(limit_str) if limit_str else 20
            return _trade_history_text(ex, sym, limit)
        except Exception as e:
            return f"[HISTORY-ERR] {e}"
    
    # reconcile_tp_sl - Manually trigger TP/SL fill reconciliation
    if s.lower() in ("reconcile_tp_sl", "reconcile tp sl", "check tp sl"):
        try:
            from reconciliation_service import reconcile_tp_sl_fills
            trading_mode = get_mode_str()
            result = reconcile_tp_sl_fills(trading_mode)
            
            pending_count = result['pending_count']
            filled_count = result['filled_count']
            errors = result.get('errors', [])
            
            result_lines = [
                f"=== TP/SL RECONCILIATION RESULTS ({trading_mode}) ===",
                f"📊 Pending orders checked: {pending_count}",
                f"✅ Fills logged: {filled_count}",
            ]
            
            if errors:
                result_lines.append(f"\n⚠️ Errors ({len(errors)}):")
                for err in errors[:5]:  # Show first 5 errors
                    result_lines.append(f"  • {err}")
            
            if filled_count > 0:
                fills = result.get('fills_logged', [])
                result_lines.append(f"\n💰 New fills logged:")
                for fill in fills:
                    result_lines.append(
                        f"  • {fill['type'].upper()} {fill['symbol']} "
                        f"{fill['quantity']:.6f} @ ${fill['price']:.2f}"
                    )
            elif pending_count > 0:
                result_lines.append(f"\n⏳ All {pending_count} pending orders still open (no fills yet)")
            else:
                result_lines.append(f"\n✅ No pending TP/SL orders to check")
            
            return "\n".join(result_lines)
            
        except Exception as e:
            return f"[RECONCILE-ERR] {e}"
    
    # debug_trade <symbol> - Show complete lifecycle of trades for a symbol
    m = re.fullmatch(r"(?i)debug[_ ]trade\s+([A-Za-z0-9:/\-\._]+)", s)
    if m:
        sym = _norm_sym(m.group(1))
        try:
            from evaluation_log import get_last_evaluations, get_executed_orders, get_pending_child_orders
            from datetime import datetime, timedelta
            
            result_lines = [f"=== TRADE LIFECYCLE DEBUG: {sym} ===\n"]
            
            # 1. Check evaluation log for signals
            evals = get_last_evaluations(limit=10, symbol=sym)
            result_lines.append(f"📊 EVALUATIONS (last 10):")
            if evals:
                for ev in evals:
                    decision = ev.get('decision', '?')
                    reason = ev.get('reason', '?')
                    ts = ev.get('timestamp_utc', '?')
                    result_lines.append(f"  • {ts[:19]} | {decision}: {reason}")
            else:
                result_lines.append(f"  • No evaluations found")
            
            # 2. Check executed_orders table (including TP/SL fills)
            result_lines.append(f"\n📝 EXECUTED ORDERS (last 24h):")
            executed = get_executed_orders(limit=20, symbol=sym, since_hours=24)
            if executed:
                entry_orders = [o for o in executed if o.get('order_type', 'entry') == 'entry']
                tp_orders = [o for o in executed if o.get('order_type') == 'tp']
                sl_orders = [o for o in executed if o.get('order_type') == 'sl']
                
                if entry_orders:
                    result_lines.append(f"  ENTRY orders:")
                    for order in entry_orders:
                        ts = order.get('timestamp_utc', '?')
                        side = order.get('side', '?')
                        qty = order.get('quantity', 0)
                        price = order.get('entry_price', 0)
                        order_id = order.get('order_id', '?')
                        source = order.get('source', '?')
                        result_lines.append(
                            f"    • {ts[:19]} | {side.upper()} {qty:.6f} @ ${price:.2f} "
                            f"| id={order_id} | source={source}"
                        )
                
                if tp_orders:
                    result_lines.append(f"  TP orders (filled):")
                    for order in tp_orders:
                        ts = order.get('timestamp_utc', '?')
                        side = order.get('side', '?')
                        qty = order.get('quantity', 0)
                        price = order.get('entry_price', 0)
                        order_id = order.get('order_id', '?')
                        parent = order.get('parent_order_id', '?')
                        result_lines.append(
                            f"    • {ts[:19]} | TP {side.upper()} {qty:.6f} @ ${price:.2f} "
                            f"| id={order_id} | parent={parent}"
                        )
                
                if sl_orders:
                    result_lines.append(f"  SL orders (filled):")
                    for order in sl_orders:
                        ts = order.get('timestamp_utc', '?')
                        side = order.get('side', '?')
                        qty = order.get('quantity', 0)
                        price = order.get('entry_price', 0)
                        order_id = order.get('order_id', '?')
                        parent = order.get('parent_order_id', '?')
                        result_lines.append(
                            f"    • {ts[:19]} | SL {side.upper()} {qty:.6f} @ ${price:.2f} "
                            f"| id={order_id} | parent={parent}"
                        )
            else:
                result_lines.append(f"  • No executed orders found in database")
            
            # 2.5. Check pending TP/SL orders
            result_lines.append(f"\n⏳ PENDING TP/SL ORDERS (awaiting fill):")
            pending = get_pending_child_orders(trading_mode=get_mode_str().lower(), status="pending")
            pending_for_symbol = [p for p in pending if p.get('symbol') == sym]
            if pending_for_symbol:
                for order in pending_for_symbol:
                    order_type = order.get('order_type', '?')
                    order_id = order.get('order_id', '?')
                    side = order.get('side', '?')
                    qty = order.get('quantity', 0)
                    limit_price = order.get('limit_price', 0)
                    parent = order.get('parent_order_id', '?')
                    result_lines.append(
                        f"  • {order_type.upper()} {side.upper()} {qty:.6f} @ ${limit_price:.2f} "
                        f"| id={order_id} | parent={parent}"
                    )
            else:
                result_lines.append(f"  • No pending TP/SL orders")
            
            # 3. Check Kraken trade history
            result_lines.append(f"\n💰 KRAKEN TRADE HISTORY (last 20):")
            trades = ex.fetch_my_trades(symbol=sym, limit=20)
            if trades:
                for t in trades:
                    tid = str(t.get("id") or "?")
                    side = t.get("side") or "?"
                    amt = _safe_float(t.get("amount"), 0.0)
                    px = _safe_float(t.get("price"), 0.0)
                    timestamp = t.get("timestamp")
                    time_str = ""
                    if timestamp:
                        try:
                            dt = datetime.fromtimestamp(timestamp / 1000)
                            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            time_str = str(timestamp)
                    result_lines.append(f"  • {time_str} | {side.upper()} {amt:.6f} @ ${px:.2f} | id={tid}")
            else:
                result_lines.append(f"  • No trades in Kraken history")
            
            # 4. Check current open orders
            result_lines.append(f"\n📋 CURRENT OPEN ORDERS:")
            opens = ex.fetch_open_orders(symbol=sym)
            if opens:
                for o in opens:
                    oid = str(o.get("id") or "?")
                    side = o.get("side") or "?"
                    amt = _safe_float(o.get("amount"), 0.0)
                    px = _safe_float(o.get("price"), 0.0)
                    order_type = o.get("type") or "?"
                    result_lines.append(f"  • {order_type} {side.upper()} {amt:.6f} @ ${px:.2f} | id={oid}")
            else:
                result_lines.append(f"  • No open orders")
            
            # 5. Summary
            result_lines.append(f"\n📈 SUMMARY:")
            result_lines.append(f"  • Signal generated: {'YES' if evals and any(e.get('decision') in ['BUY', 'LONG', 'SELL', 'SELL_ALL'] for e in evals) else 'NO'}")
            result_lines.append(f"  • Entry orders logged: {len([o for o in executed if o.get('order_type', 'entry') == 'entry']) if executed else 0}")
            result_lines.append(f"  • TP fills logged: {len([o for o in executed if o.get('order_type') == 'tp']) if executed else 0}")
            result_lines.append(f"  • SL fills logged: {len([o for o in executed if o.get('order_type') == 'sl']) if executed else 0}")
            result_lines.append(f"  • Pending TP/SL: {len(pending_for_symbol)}")
            result_lines.append(f"  • Current open orders: {len(opens) if opens else 0}")
            
            return "\n".join(result_lines)
            
        except Exception as e:
            return f"[DEBUG-TRADE-ERR] {e}"

    # cancel <order_id> [symbol]
    m = re.fullmatch(r"(?i)cancel\s+([A-Za-z0-9\-_]+)(?:\s+([A-Za-z0-9:/\-\._]+))?", s)
    if m:
        oid = m.group(1)
        f = m.group(2)
        try:
            sym = _norm_sym(f) if f else None
            res = ex.cancel_order(oid, sym) if sym else ex.cancel_order(oid)
            status = res.get("status") if isinstance(res, dict) else "submitted"
            return f"CANCEL OK {oid} -> {status}"
        except Exception as e:
            return f"[CANCEL-ERR] {e}"

    # paper debug ledger - dumps paper ledger for debugging
    if s.lower() in ("paper debug ledger", "debug ledger", "dump ledger"):
        if not is_paper_mode():
            return "[DEBUG] Not in paper mode - no paper ledger to dump"
        
        try:
            from account_state import get_paper_ledger
            import json
            ledger = get_paper_ledger()
            
            ledger_data = {
                'mode': 'PAPER',
                'total_orders': len(ledger.orders),
                'open_orders': [o for o in ledger.orders if o.get('status') == 'open'],
                'closed_orders': [o for o in ledger.orders if o.get('status') == 'closed'],
                'cancelled_orders': [o for o in ledger.orders if o.get('status') == 'cancelled'],
                'balances': ledger.get_balances(),
                'trades': len(ledger.trades)
            }
            
            return (
                f"[PAPER-LEDGER DEBUG]\n"
                f"Total orders: {ledger_data['total_orders']}\n"
                f"Open orders: {len(ledger_data['open_orders'])}\n"
                f"Closed orders: {len(ledger_data['closed_orders'])}\n"
                f"Cancelled orders: {len(ledger_data['cancelled_orders'])}\n"
                f"Trades: {ledger_data['trades']}\n\n"
                f"Full JSON:\n{json.dumps(ledger_data, indent=2)}"
            )
        except Exception as e:
            return f"[DEBUG-ERR] {e}"

    # debug status
    if s.lower() in ("debug status", "status"):
        try:
            from account_state import get_balances
            from evaluation_log import get_last_evaluations
            from exchange_manager import ExchangeManager
            from datetime import datetime, timezone, timedelta
            import json
            
            # 1. Current mode
            mode = get_mode_str()
            manager = ExchangeManager()
            validate_mode = manager._validate_mode
            
            # 2. Symbols
            symbols_str = os.getenv("SYMBOLS", "ZEC/USD")
            symbols = [s.strip().upper() for s in symbols_str.split(",")]
            
            # 3. Equity
            balances = get_balances()
            equity_usd = 0.0
            if balances:
                for curr, bal_data in balances.items():
                    if isinstance(bal_data, dict):
                        equity_usd += bal_data.get('usd_value', 0)
            
            # 4. Last evaluation
            last_evals = get_last_evaluations(limit=1)
            last_eval = last_evals[0] if last_evals else None
            
            # 5. Evaluation counts (last 24h)
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=24)
            
            all_evals_24h = get_last_evaluations(limit=500)
            evals_24h = [
                e for e in all_evals_24h 
                if e.get('timestamp_utc') and datetime.fromisoformat(e['timestamp_utc'].replace('Z', '+00:00')) > cutoff
            ]
            
            eval_counts = {
                "total": len(evals_24h),
                "by_symbol": {}
            }
            for e in evals_24h:
                sym = e.get('symbol', 'unknown')
                eval_counts['by_symbol'][sym] = eval_counts['by_symbol'].get(sym, 0) + 1
            
            # 6. Trades (last 24h) - only for LIVE mode
            trades_24h = {"total": 0, "by_symbol": {}}
            if mode == "live":
                try:
                    # Fetch trades from last 24h
                    since_ms = int(cutoff.timestamp() * 1000)
                    all_trades = ex.fetch_my_trades(since=since_ms, limit=100)
                    
                    trades_24h["total"] = len(all_trades)
                    for trade in all_trades:
                        sym = trade.get('symbol', 'unknown')
                        trades_24h['by_symbol'][sym] = trades_24h['by_symbol'].get(sym, 0) + 1
                except Exception as trades_err:
                    trades_24h = {"error": str(trades_err)}
            
            # Build result
            result = {
                "mode": mode,
                "validate_mode": validate_mode,
                "symbols": symbols,
                "equity_usd": round(equity_usd, 2),
                "balances": balances,
                "last_evaluation": last_eval,
                "eval_counts_last_24h": eval_counts,
                "trades_last_24h": trades_24h
            }
            
            return json.dumps(result, indent=2)
        
        except Exception as e:
            import traceback
            return f"[DEBUG-STATUS-ERR] {e}\n{traceback.format_exc()}"
    
    # show evaluations [symbol] [limit]
    m = re.fullmatch(r"(?i)show\s+evaluations(?:\s+([A-Za-z0-9:/\-\._]+))?(?:\s+(\d+))?", s)
    if m:
        try:
            from evaluation_log import get_last_evaluations
            import json
            
            symbol = m.group(1).upper() if m.group(1) else None
            limit = int(m.group(2)) if m.group(2) else 20
            
            # Cap at 100
            if limit > 100:
                limit = 100
            
            evals = get_last_evaluations(limit=limit, symbol=symbol)
            
            return json.dumps({"evaluations": evals}, indent=2)
        
        except Exception as e:
            import traceback
            return f"[SHOW-EVAL-ERR] {e}\n{traceback.format_exc()}"
    
    # force trade test [symbol]
    m = re.fullmatch(r"(?i)force\s+trade\s+test(?:\s+([A-Za-z0-9:/\-\._]+))?", s)
    if m:
        try:
            import json
            from datetime import datetime, timezone
            
            # 1. Check ENABLE_FORCE_TRADE flag
            enable_force_trade = os.getenv("ENABLE_FORCE_TRADE", "0").strip().lower() in ("1", "true", "yes", "on")
            
            if not enable_force_trade:
                return json.dumps({
                    "ok": False,
                    "error": "ENABLE_FORCE_TRADE is not enabled in .env. Set ENABLE_FORCE_TRADE=1 to allow force trade tests."
                }, indent=2)
            
            # 2. Determine mode
            mode = get_mode_str()
            
            # 3. Get symbol (default ETH/USD)
            symbol = m.group(1).upper() if m.group(1) else "ETH/USD"
            symbol = _norm_sym(symbol)
            
            # 4. Calculate small test size ($10-15)
            current_price = _last_price(ex, symbol)
            test_notional = 12.0  # $12 test trade
            test_qty = test_notional / current_price
            
            # Apply exchange precision
            test_qty_p = _safe_float(ex.amount_to_precision(symbol, test_qty), test_qty)
            
            # Ensure minimum order size
            market = ex.market(symbol) or {}
            min_amt = _safe_float((market.get("limits") or {}).get("amount", {}).get("min"), 0)
            if min_amt and test_qty_p < min_amt:
                test_qty_p = min_amt * 1.05  # 5% above minimum
            
            # 5. Calculate bracket prices (simple 2% SL, 3% TP)
            entry_price = current_price
            sl_price = entry_price * 0.98  # 2% below
            tp_price = entry_price * 1.03  # 3% above
            
            # Apply precision
            sl_price_p = _safe_float(ex.price_to_precision(symbol, sl_price), sl_price)
            tp_price_p = _safe_float(ex.price_to_precision(symbol, tp_price), tp_price)
            
            # 6. Execute bracket order
            timestamp_utc = datetime.now(timezone.utc).isoformat()
            
            bracket_cmd = f"bracket {symbol} {test_qty_p:.6f} tp {tp_price_p} sl {sl_price_p}"
            print(f"[FORCE-TRADE-TEST] Executing: {bracket_cmd}")
            
            bracket_result = handle(bracket_cmd)
            
            # Log to evaluation_log for forensic analysis
            try:
                from evaluation_log import log_evaluation
                
                # Determine success/failure and log appropriately
                is_success = "BRACKET OK" in bracket_result
                
                if is_success:
                    log_evaluation(
                        symbol=symbol,
                        decision="FORCE_TRADE_TEST_SUCCESS",
                        reason=f"Force trade test completed successfully with full bracket (entry + TP + SL)",
                        trading_mode=mode,
                        position_size=test_qty_p,
                        price=current_price,
                        error_message=None
                    )
                else:
                    # Extract error message from bracket result
                    error_msg = bracket_result if "[BRACKET-ERR]" in bracket_result else f"Bracket result: {bracket_result}"
                    log_evaluation(
                        symbol=symbol,
                        decision="FORCE_TRADE_TEST_FAIL",
                        reason=f"Force trade test failed - bracket did not complete",
                        trading_mode=mode,
                        position_size=test_qty_p,
                        price=current_price,
                        error_message=error_msg
                    )
            except Exception as log_err:
                print(f"[FORCE-TRADE-TEST] Warning: Failed to log: {log_err}")
            
            # Parse result
            success = "BRACKET OK" in bracket_result or "ok" in bracket_result.lower()
            
            # Extract order IDs if available (simple regex)
            entry_id = "N/A"
            tp_id = "N/A"
            sl_id = "N/A"
            
            import re as regex
            entry_match = regex.search(r'Entry.*?id=([^\s\n]+)', bracket_result)
            tp_match = regex.search(r'TP.*?id=([^\s\n]+)', bracket_result)
            sl_match = regex.search(r'SL.*?id=([^\s\n]+)', bracket_result)
            
            if entry_match:
                entry_id = entry_match.group(1)
            if tp_match:
                tp_id = tp_match.group(1)
            if sl_match:
                sl_id = sl_match.group(1)
            
            result = {
                "ok": success,
                "mode": mode,
                "symbol": symbol,
                "side": "buy",
                "quantity": test_qty_p,
                "entry_price": current_price,
                "entry_order_id": entry_id,
                "take_profit_order_id": tp_id,
                "stop_loss_order_id": sl_id,
                "note": "Force trade test executed using ENABLE_FORCE_TRADE",
                "timestamp_utc": timestamp_utc,
                "full_result": bracket_result
            }
            
            return json.dumps(result, indent=2)
        
        except Exception as e:
            import traceback
            return json.dumps({
                "ok": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            }, indent=2)

    return HELP
