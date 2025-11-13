# commands.py — clean, strict router for Kraken via ccxt (CENTRALIZED EXCHANGE)
import os
import re
from typing import Optional

import ccxt
from dotenv import load_dotenv
from exchange_manager import get_exchange, get_mode_str, is_paper_mode

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
    bal = ex.fetch_balance()
    lines = ["Balances (free):"]
    free = bal.get("free")
    printed = 0
    if isinstance(free, dict):
        for k, v in free.items():
            fv = _safe_float(v, 0.0)
            if fv and fv > 0:
                lines.append(f"  {k}: {fv}")
                printed += 1
    if printed == 0:
        # fall back to totals
        for k, obj in bal.items():
            if isinstance(obj, dict):
                tot = _safe_float(obj.get("total"), 0.0)
                if tot and tot > 0:
                    lines.append(f"  {k}: {tot}")
                    printed += 1
    if printed == 0:
        usd = _safe_float((bal.get("USD") or {}).get("total"), 0.0)
        lines.append(f"  USD: {usd}")
    return "\n".join(lines)

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
    orders = ex.fetch_open_orders(symbol_filter) if symbol_filter else ex.fetch_open_orders()
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
    """Native stop-market using ccxt unified create_order with stopPrice."""
    amt = _safe_float(ex.amount_to_precision(symbol, amount), None)
    stp = _safe_float(ex.price_to_precision(symbol, stop_px), None)
    if amt is None or amt <= 0 or stp is None or stp <= 0:
        raise ValueError("bad stop params")
    params = {"stopPrice": stp, "trigger": "last"}  # Kraken via ccxt
    return ex.create_order(symbol, "market", side, float(amt), None, params)

# ----------------- public router -----------------

def handle(text: str) -> str:
    if not text:
        return HELP

    s = text.strip()
    if s.lower() in ("help", "h", "?"):
        return HELP

    ex = _ex()

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
            
            # Execute bracket with rollback protection
            entry_order = None
            tp_order = None
            sl_order = None
            
            try:
                if is_long:
                    # LONG: Market buy entry
                    entry_order = ex.create_market_buy_order(sym, float(amt_p))
                    entry_id = str(entry_order.get("id") or entry_order.get("orderId") or "<no-id>")
                    side_str = "LONG"
                    
                    # Create protective orders
                    try:
                        tp_order = ex.create_limit_sell_order(sym, float(amt_p), float(tp_p))
                        sl_order = _create_stop_market(ex, sym, "sell", float(amt_p), float(sl_p))
                    except Exception as protect_err:
                        # ROLLBACK: Close position if protective orders fail
                        print(f"[BRACKET-ROLLBACK] TP/SL creation failed, closing position: {protect_err}")
                        ex.create_market_sell_order(sym, float(amt_p))
                        return f"[BRACKET-ERR] Entry executed but TP/SL failed - position closed for safety: {protect_err}"
                else:
                    # SHORT: Market sell entry
                    entry_order = ex.create_market_sell_order(sym, float(amt_p))
                    entry_id = str(entry_order.get("id") or entry_order.get("orderId") or "<no-id>")
                    side_str = "SHORT"
                    
                    # Create protective orders
                    try:
                        tp_order = ex.create_limit_buy_order(sym, float(amt_p), float(tp_p))
                        sl_order = _create_stop_market(ex, sym, "buy", float(amt_p), float(sl_p))
                    except Exception as protect_err:
                        # ROLLBACK: Close position if protective orders fail
                        print(f"[BRACKET-ROLLBACK] TP/SL creation failed, closing position: {protect_err}")
                        ex.create_market_buy_order(sym, float(amt_p))
                        return f"[BRACKET-ERR] Entry executed but TP/SL failed - position closed for safety: {protect_err}"
                
                tid = str(tp_order.get("id") or tp_order.get("orderId") or "<no-id>")
                sid = str(sl_order.get("id") or sl_order.get("orderId") or "<no-id>")
                
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

    return HELP
