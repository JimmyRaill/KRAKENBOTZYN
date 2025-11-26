"""
Reconciliation Service - TP/SL Fill Monitoring System

Polls pending TP/SL orders and logs fills to executed_orders table.
Runs every 60 seconds via autopilot heartbeat.
"""

import os
from typing import Dict, List, Optional, Any
from loguru import logger
from datetime import datetime

from evaluation_log import (
    get_pending_child_orders,
    mark_pending_order_filled,
    log_order_execution,
    update_reconciliation_stats,
    get_entry_fill_state,
    update_entry_fill_progress,
    mark_bracket_initialized
)
from exchange_manager import get_exchange


def reconcile_tp_sl_fills(trading_mode: str) -> Dict[str, Any]:
    """
    Check pending TP/SL orders and log any fills.
    
    CRITICAL: Only processes TP/SL child orders, NOT entries pending TP placement.
    Entry monitoring is handled exclusively by reconcile_pending_entries().
    
    Args:
        trading_mode: "live" or "paper"
    
    Returns:
        Summary of reconciliation results
    """
    try:
        all_pending = get_pending_child_orders(trading_mode=trading_mode.lower(), status="pending")
        
        # CRITICAL: Skip entry_pending_tp orders (handled by reconcile_pending_entries)
        pending_orders = [p for p in all_pending if p['order_type'] != 'entry_pending_tp']
        
        if not pending_orders:
            logger.debug(f"[RECONCILE-{trading_mode.upper()}] No pending TP/SL orders to check")
            return {
                "pending_count": 0,
                "filled_count": 0,
                "errors": [],
                "fills_logged": []
            }
        
        logger.info(f"[RECONCILE-{trading_mode.upper()}] Checking {len(pending_orders)} pending TP/SL orders")
        
        exchange = get_exchange()
        filled_count = 0
        errors = []
        fills_logged = []
        
        for order in pending_orders:
            try:
                order_id = order['order_id']
                symbol = order['symbol']
                order_type = order['order_type']
                parent_order_id = order['parent_order_id']
                
                # Check order status via exchange
                is_filled, fill_data = _check_order_status(
                    exchange=exchange,
                    order_id=order_id,
                    symbol=symbol,
                    trading_mode=trading_mode
                )
                
                if is_filled and fill_data:
                    # Log the fill to executed_orders
                    log_order_execution(
                        symbol=symbol,
                        side=fill_data['side'],
                        quantity=fill_data['quantity'],
                        entry_price=fill_data['price'],
                        order_id=order_id,
                        trading_mode=trading_mode.lower(),
                        source="reconciliation",
                        extra_info=f"{order_type.upper()} for parent={parent_order_id}",
                        order_type=order_type,
                        parent_order_id=parent_order_id
                    )
                    
                    # Mark as filled in pending table
                    mark_pending_order_filled(order_id)
                    
                    filled_count += 1
                    fills_logged.append({
                        "order_id": order_id,
                        "symbol": symbol,
                        "type": order_type,
                        "price": fill_data['price'],
                        "quantity": fill_data['quantity']
                    })
                    
                    logger.info(
                        f"[RECONCILE-{trading_mode.upper()}] ✅ {order_type.upper()} filled: "
                        f"{symbol} {fill_data['quantity']} @ ${fill_data['price']:.2f} (order_id={order_id})"
                    )
                    
            except Exception as e:
                error_msg = f"Error checking order {order.get('order_id', 'unknown')}: {e}"
                logger.error(f"[RECONCILE-{trading_mode.upper()}] {error_msg}")
                errors.append(error_msg)
        
        # Update stats
        update_reconciliation_stats(fills_logged=filled_count)
        
        return {
            "pending_count": len(pending_orders),
            "filled_count": filled_count,
            "errors": errors,
            "fills_logged": fills_logged
        }
        
    except Exception as e:
        logger.error(f"[RECONCILE-{trading_mode.upper()}] CRITICAL: {e}")
        return {
            "pending_count": 0,
            "filled_count": 0,
            "errors": [str(e)],
            "fills_logged": []
        }


def reconcile_pending_entries(trading_mode: str) -> Dict[str, Any]:
    """
    Monitor pending ENTRY orders and place TP orders when they fill.
    
    PHASE 2B PARTIAL FILL HOTFIX:
    - Uses cumulative fill tracking to handle partial fills correctly
    - Only places TP ONCE when fill threshold (99%) is reached
    - Sets bracket_initialized=1 to prevent duplicate TP orders
    - Kraken creates SL per partial fill, so multiple SLs are expected
    
    Args:
        trading_mode: "live" or "paper"
    
    Returns:
        Summary of reconciliation results
    """
    try:
        # Get all pending entries awaiting TP placement
        all_pending = get_pending_child_orders(trading_mode=trading_mode.lower(), status="pending")
        pending_entries = [p for p in all_pending if p['order_type'] == 'entry_pending_tp']
        
        if not pending_entries:
            logger.debug(f"[RECONCILE-ENTRIES-{trading_mode.upper()}] No pending entries to check")
            return {
                "pending_count": 0,
                "filled_count": 0,
                "tp_placed_count": 0,
                "errors": [],
                "tps_placed": []
            }
        
        logger.info(f"[RECONCILE-ENTRIES-{trading_mode.upper()}] Checking {len(pending_entries)} pending entries")
        
        exchange = get_exchange()
        filled_count = 0
        tp_placed_count = 0
        errors = []
        tps_placed = []
        
        for entry in pending_entries:
            try:
                entry_order_id = entry['order_id']
                symbol = entry['symbol']
                entry_side = entry['side']
                tp_price = entry['limit_price']  # TP price stored in limit_price field
                
                # PHASE 2B: Get current fill state first
                entry_state = get_entry_fill_state(entry_order_id)
                
                if entry_state and entry_state.get('bracket_initialized') == 1:
                    # Bracket already placed - skip this entry
                    logger.debug(
                        f"[RECONCILE-ENTRIES-{trading_mode.upper()}] Skipping {entry_order_id} - "
                        f"bracket_initialized=1 (TP already placed)"
                    )
                    continue
                
                # Check current fill status with Kraken (or partial fill info)
                is_filled, fill_data, partial_fill_qty = _check_order_status_with_partials(
                    exchange=exchange,
                    order_id=entry_order_id,
                    symbol=symbol,
                    trading_mode=trading_mode
                )
                
                # Handle partial fills - update cumulative tracking
                # Note: partial_fill_qty from Kraken is already cumulative (not incremental)
                if partial_fill_qty and partial_fill_qty > 0:
                    progress = update_entry_fill_progress(
                        entry_order_id=entry_order_id,
                        kraken_filled_qty=partial_fill_qty,  # This is Kraken's cumulative filled value
                        fill_threshold_pct=0.99  # 99% fill threshold
                    )
                    
                    if progress['already_initialized']:
                        logger.debug(
                            f"[RECONCILE-ENTRIES-{trading_mode.upper()}] {entry_order_id} - "
                            f"bracket already initialized, skipping"
                        )
                        continue
                    
                    if not progress['threshold_reached']:
                        # Still accumulating fills, don't place TP yet
                        logger.info(
                            f"[RECONCILE-ENTRIES-{trading_mode.upper()}] {entry_order_id} - "
                            f"partial fill progress: {progress['fill_pct']:.1f}% (waiting for threshold)"
                        )
                        continue
                    
                    # Threshold reached - use cumulative filled qty for TP
                    filled_qty = progress['cumulative_filled']
                    fill_price = fill_data.get('price', 0) if fill_data else 0
                    is_filled = True
                elif is_filled and fill_data:
                    # Full fill detected via status='closed'
                    filled_qty = fill_data['quantity']
                    fill_price = fill_data['price']
                else:
                    # No fill detected
                    continue
                
                if is_filled:
                    filled_count += 1
                    logger.info(
                        f"[RECONCILE-ENTRIES-{trading_mode.upper()}] ✅ Entry fill threshold reached: "
                        f"{symbol} {filled_qty} @ ${fill_price:.5f} (order_id={entry_order_id})"
                    )
                    
                    # Place TP order with intelligent settlement detection + retry
                    from settlement_detector import place_tp_with_retry
                    
                    tp_side = 'sell' if entry_side.lower() == 'buy' else 'buy'
                    
                    tp_success, tp_message, tp_order_id = place_tp_with_retry(
                        symbol=symbol,
                        side=tp_side,
                        quantity=filled_qty,
                        tp_price=tp_price,
                        fill_price=fill_price,
                        max_attempts=5,
                        initial_backoff=1.0
                    )
                    
                    if tp_success:
                        tp_placed_count += 1
                        
                        # Find and store SL order ID for OCO monitoring
                        from sl_order_enrichment import enrich_and_store_sl_order_id
                        sl_order_id = enrich_and_store_sl_order_id(entry_order_id, symbol, max_attempts=3)
                        
                        tps_placed.append({
                            "entry_order_id": entry_order_id,
                            "tp_order_id": tp_order_id,
                            "sl_order_id": sl_order_id,
                            "symbol": symbol,
                            "tp_price": tp_price,
                            "quantity": filled_qty
                        })
                        
                        logger.info(
                            f"[RECONCILE-ENTRIES-{trading_mode.upper()}] 🎯 TP placed: "
                            f"{symbol} {tp_side} {filled_qty} @ ${tp_price:.5f} (tp_id={tp_order_id}, sl_id={sl_order_id})"
                        )
                        
                        # PHASE 2B: Mark bracket as initialized (prevents duplicate TP)
                        mark_bracket_initialized(entry_order_id, tp_order_id=tp_order_id, sl_order_id=sl_order_id)
                    else:
                        error_msg = f"TP placement failed for entry {entry_order_id}: {tp_message}"
                        logger.error(f"[RECONCILE-ENTRIES-{trading_mode.upper()}] {error_msg}")
                        errors.append(error_msg)
                        
            except Exception as e:
                error_msg = f"Error processing entry {entry.get('order_id', 'unknown')}: {e}"
                logger.error(f"[RECONCILE-ENTRIES-{trading_mode.upper()}] {error_msg}")
                errors.append(error_msg)
        
        return {
            "pending_count": len(pending_entries),
            "filled_count": filled_count,
            "tp_placed_count": tp_placed_count,
            "errors": errors,
            "tps_placed": tps_placed
        }
        
    except Exception as e:
        logger.error(f"[RECONCILE-ENTRIES-{trading_mode.upper()}] CRITICAL: {e}")
        return {
            "pending_count": 0,
            "filled_count": 0,
            "tp_placed_count": 0,
            "errors": [str(e)],
            "tps_placed": []
        }


def _check_order_status(
    exchange,
    order_id: str,
    symbol: str,
    trading_mode: str
) -> tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check if an order has been filled.
    
    Returns:
        (is_filled, fill_data) - fill_data contains side, quantity, price if filled
    """
    try:
        if trading_mode.upper() == "PAPER":
            # For paper mode, check paper trading system
            return _check_paper_order_status(order_id, symbol)
        else:
            # For live mode, query Kraken
            return _check_live_order_status(exchange, order_id, symbol)
            
    except Exception as e:
        logger.error(f"[ORDER-STATUS] Error checking {order_id}: {e}")
        return False, None


def _check_order_status_with_partials(
    exchange,
    order_id: str,
    symbol: str,
    trading_mode: str
) -> tuple[bool, Optional[Dict[str, Any]], Optional[float]]:
    """
    PHASE 2B PARTIAL FILL HOTFIX:
    Check order status and return partial fill info for cumulative tracking.
    
    Unlike _check_order_status which only returns True on full fill,
    this function also returns the current filled quantity even if order is still open.
    
    Returns:
        (is_fully_filled, fill_data, current_filled_qty)
        - is_fully_filled: True only if order status is 'closed'
        - fill_data: Contains side, quantity (total filled), price
        - current_filled_qty: The NEW fill quantity to add (delta from last check)
                             Note: For now, returns total filled - caller should handle idempotency
    """
    try:
        if trading_mode.upper() == "PAPER":
            # For paper mode, partial fills aren't simulated
            is_filled, fill_data = _check_paper_order_status(order_id, symbol)
            filled_qty = fill_data.get('quantity', 0) if fill_data else 0
            return is_filled, fill_data, filled_qty if is_filled else None
        else:
            # For live mode, query Kraken with partial fill awareness
            return _check_live_order_status_with_partials(exchange, order_id, symbol)
            
    except Exception as e:
        logger.error(f"[ORDER-STATUS-PARTIAL] Error checking {order_id}: {e}")
        return False, None, None


def _check_live_order_status_with_partials(
    exchange, 
    order_id: str, 
    symbol: str
) -> tuple[bool, Optional[Dict[str, Any]], Optional[float]]:
    """
    PHASE 2B PARTIAL FILL HOTFIX:
    Check Kraken order status with awareness of partial fills.
    
    Returns partial fill quantity even when order is still 'open'.
    This enables cumulative fill tracking for TP placement.
    
    Returns:
        (is_fully_filled, fill_data, filled_qty)
    """
    try:
        order_info = exchange.fetch_order(order_id, symbol)
        
        status = order_info.get('status', '').lower()
        filled_qty = float(order_info.get('filled', 0) or 0)
        remaining = float(order_info.get('remaining', 0) or 0)
        avg_price = float(order_info.get('average', 0) or order_info.get('price', 0) or 0)
        side = order_info.get('side', '').lower()
        
        # Full fill: status is closed and remaining is 0
        is_fully_filled = status in ['closed', 'filled'] and remaining == 0
        
        if is_fully_filled and filled_qty > 0 and avg_price > 0:
            logger.info(f"[ORDER-STATUS-PARTIAL] ✅ {order_id} FULLY FILLED: {filled_qty} @ ${avg_price:.5f}")
            return True, {
                "side": side,
                "quantity": filled_qty,
                "price": avg_price
            }, filled_qty
        
        # Partial fill: order still open but has some fills
        if status == 'open' and filled_qty > 0:
            logger.info(f"[ORDER-STATUS-PARTIAL] ⏳ {order_id} PARTIAL: {filled_qty} filled, {remaining} remaining @ ${avg_price:.5f}")
            return False, {
                "side": side,
                "quantity": filled_qty,
                "price": avg_price
            }, filled_qty
        
        # No fills yet
        logger.debug(f"[ORDER-STATUS-PARTIAL] {order_id} not filled (status={status}, filled={filled_qty})")
        return False, None, None
        
    except Exception as e:
        logger.error(f"[ORDER-STATUS-PARTIAL] Kraken query failed for {order_id}: {e}")
        return False, None, None


def _check_live_order_status(exchange, order_id: str, symbol: str) -> tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check order status via Kraken API.
    
    CRITICAL: Must verify order is ACTUALLY filled with concrete Kraken data.
    """
    try:
        # Query Kraken for order status
        order_info = exchange.fetch_order(order_id, symbol)
        
        status = order_info.get('status', '').lower()
        remaining = order_info.get('remaining', 0)
        
        # Only consider filled if status is 'closed'/'filled' AND remaining is 0
        is_filled = status in ['closed', 'filled'] and remaining == 0
        
        if not is_filled:
            logger.debug(f"[ORDER-STATUS] {order_id} not filled (status={status}, remaining={remaining})")
            return False, None
        
        # Extract fill data from ACTUAL Kraken response
        filled_qty = order_info.get('filled', 0)
        avg_price = order_info.get('average', 0) or order_info.get('price', 0)
        side = order_info.get('side', '').lower()
        
        if filled_qty <= 0 or avg_price <= 0:
            logger.warning(f"[ORDER-STATUS] {order_id} marked filled but missing data: qty={filled_qty}, price={avg_price}")
            return False, None
        
        return True, {
            "side": side,
            "quantity": filled_qty,
            "price": avg_price
        }
        
    except Exception as e:
        logger.error(f"[ORDER-STATUS] Kraken query failed for {order_id}: {e}")
        return False, None


def _check_paper_order_status(order_id: str, symbol: str) -> tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check order status in paper trading system.
    """
    try:
        from account_state import get_paper_ledger
        
        ledger = get_paper_ledger()
        
        # Check if order exists in paper ledger
        order_found = None
        for entry in ledger.orders:
            if entry.get('order_id') == order_id:
                order_found = entry
                break
        
        if not order_found:
            logger.debug(f"[ORDER-STATUS-PAPER] {order_id} not found in ledger")
            return False, None
        
        # Check if filled
        status = order_found.get('status', '').lower()
        is_filled = status in ['closed', 'filled']
        
        if not is_filled:
            return False, None
        
        # Extract fill data
        return True, {
            "side": order_found.get('side', '').lower(),
            "quantity": order_found.get('filled', 0),
            "price": order_found.get('average_price', 0) or order_found.get('price', 0)
        }
        
    except Exception as e:
        logger.error(f"[ORDER-STATUS-PAPER] Error checking {order_id}: {e}")
        return False, None


def reconcile_all_kraken_fills(trading_mode: str, lookback_hours: int = 48) -> Dict[str, Any]:
    """
    Comprehensive sweep: Find ALL Kraken fills (entry + TP/SL) and log missing ones.
    
    This catches TP/SL fills that weren't registered as pending orders.
    
    Args:
        trading_mode: "live" or "paper"
        lookback_hours: How far back to check (default 48h)
    
    Returns:
        Summary with newly_logged fills
    """
    import time
    from account_state import get_trade_history
    from evaluation_log import get_executed_orders
    
    if trading_mode.upper() != "LIVE":
        # Only run for LIVE mode (paper mode doesn't need this)
        return {"newly_logged": 0, "errors": []}
    
    try:
        # Get all Kraken trades from last N hours
        since = time.time() - (lookback_hours * 3600)
        kraken_trades = get_trade_history(since=since, limit=500)
        
        if not kraken_trades:
            logger.debug(f"[RECONCILE-ALL] No Kraken trades in last {lookback_hours}h")
            return {"newly_logged": 0, "errors": []}
        
        # Get all executed_orders from database
        executed = get_executed_orders(limit=500)
        executed_order_ids = {e['order_id'] for e in executed}
        
        # Find Kraken trades NOT in executed_orders
        missing_fills = []
        for trade in kraken_trades:
            order_id = trade.get('order_id', '')
            if not order_id or order_id in executed_order_ids:
                continue
            
            # This is a fill we haven't logged yet
            missing_fills.append(trade)
        
        if not missing_fills:
            logger.debug(f"[RECONCILE-ALL] All {len(kraken_trades)} Kraken trades already logged")
            return {"newly_logged": 0, "errors": []}
        
        # Log the missing fills
        logged_count = 0
        errors = []
        
        # Get all pending child orders to identify TP/SL fills by parent linkage
        from evaluation_log import get_db_connection
        pending_conn = get_db_connection()
        pending_cursor = pending_conn.cursor()
        pending_cursor.execute("""
            SELECT order_id, parent_order_id, order_type
            FROM pending_child_orders
            WHERE trading_mode = ?
        """, (trading_mode.lower(),))
        
        # Build lookup: order_id -> (parent_order_id, order_type)
        pending_orders_lookup = {}
        for row in pending_cursor.fetchall():
            pending_orders_lookup[row[0]] = {"parent": row[1], "type": row[2]}
        pending_conn.close()
        
        for fill in missing_fills:
            try:
                # CRITICAL: Determine entry vs TP/SL using PARENT ORDER LINKAGE
                # NOT just raw_type (limit entries would be misclassified)
                order_id = fill.get('order_id', '')
                order_type = "unknown"
                is_tp_sl = False
                
                # Check if this order is in pending_child_orders (TP/SL bracket child)
                if order_id in pending_orders_lookup:
                    order_type = pending_orders_lookup[order_id]['type']  # "tp" or "sl"
                    is_tp_sl = True
                # Fallback: incomplete data suggests TP/SL (Kraken omits symbol/side)
                elif fill.get('is_incomplete_data'):
                    order_type = "tp_or_sl"
                    is_tp_sl = True
                else:
                    # Complete data + not in pending = likely entry fill
                    order_type = "entry"
                
                # Get parent_order_id if available
                parent_order_id = None
                if order_id in pending_orders_lookup:
                    parent_order_id = pending_orders_lookup[order_id]['parent']
                
                # Log to executed_orders (forensic database)
                log_order_execution(
                    symbol=fill.get('symbol', 'UNKNOWN'),
                    side=fill.get('side', 'unknown'),
                    quantity=fill.get('quantity', 0),
                    entry_price=fill.get('price', 0),
                    order_id=fill['order_id'],
                    trading_mode="live",
                    source="reconciliation_sweep",
                    extra_info=f"Retroactive logging of {order_type} fill from Kraken",
                    order_type=order_type,
                    parent_order_id=parent_order_id
                )
                
                # ALSO log to telemetry trades table for 24h/7d stats
                from telemetry_db import log_trade
                
                action = "market_sell" if fill.get('side') == 'sell' else "market_buy"
                reason = f"TP/SL fill (retroactive)" if is_tp_sl else "Entry fill (retroactive)"
                
                log_trade(
                    symbol=fill.get('symbol', 'UNKNOWN'),
                    side=fill.get('side', 'unknown'),
                    action=action,
                    quantity=fill.get('quantity', 0),
                    price=fill.get('price', 0),
                    usd_amount=fill.get('cost', 0),
                    order_id=fill['order_id'],
                    reason=reason,
                    source="reconciliation_sweep",
                    mode="live"
                )
                
                logged_count += 1
                logger.info(
                    f"[RECONCILE-ALL] ✅ Logged missing fill: {fill.get('symbol', 'UNK')} "
                    f"{fill.get('side', 'unk')} @ ${fill.get('price', 0):.2f} (order_id={fill['order_id'][:20]}...)"
                )
                
            except Exception as e:
                error_msg = f"Failed to log fill {fill.get('order_id', 'unknown')}: {e}"
                errors.append(error_msg)
                logger.error(f"[RECONCILE-ALL] {error_msg}")
        
        logger.info(f"[RECONCILE-ALL] Logged {logged_count}/{len(missing_fills)} missing Kraken fills")
        
        return {
            "newly_logged": logged_count,
            "errors": errors,
            "missing_fills_found": len(missing_fills)
        }
        
    except Exception as e:
        logger.error(f"[RECONCILE-ALL] CRITICAL: {e}")
        return {
            "newly_logged": 0,
            "errors": [str(e)],
            "missing_fills_found": 0
        }


def run_reconciliation_cycle():
    """
    Run reconciliation for current trading mode.
    Called by autopilot heartbeat every 60 seconds.
    
    FOUR-PHASE APPROACH:
    1. Check pending ENTRY orders and place TPs when filled (CRITICAL for sequential brackets)
    2. Run OCO monitor to cancel opposite orders when TP/SL executes (synthetic OCO logic)
    3. Check registered TP/SL orders (fast, targeted)
    4. Sweep all Kraken fills every 10 cycles to catch unregistered fills
    """
    try:
        trading_mode = os.getenv("TRADING_MODE", "PAPER").upper()
        
        # Phase 1: CRITICAL - Check pending entries and place TPs
        entry_result = reconcile_pending_entries(trading_mode)
        
        # Phase 2: Synthetic OCO monitoring (cancel opposite when one fills)
        from oco_monitor import check_and_cancel_opposite_orders
        oco_result = check_and_cancel_opposite_orders(trading_mode)
        
        # Phase 3: Check pending TP/SL orders
        result = reconcile_tp_sl_fills(trading_mode)
        
        # Merge entry and OCO results into main result
        result['entry_pending_count'] = entry_result.get('pending_count', 0)
        result['entry_filled_count'] = entry_result.get('filled_count', 0)
        result['tp_placed_count'] = entry_result.get('tp_placed_count', 0)
        result['tps_placed'] = entry_result.get('tps_placed', [])
        result['oco_checked'] = oco_result.get('checked', 0)
        result['oco_tp_cancelled'] = oco_result.get('tp_cancelled', 0)
        result['oco_sl_cancelled'] = oco_result.get('sl_cancelled', 0)
        
        # Phase 3: Comprehensive sweep (every 10 cycles = ~10 minutes)
        # Use modulo trick with timestamp to spread load
        import time
        if trading_mode == "LIVE" and int(time.time()) % 600 < 60:  # Run once per 10min
            sweep_result = reconcile_all_kraken_fills(trading_mode, lookback_hours=48)
            result['sweep_newly_logged'] = sweep_result.get('newly_logged', 0)
            result['sweep_errors'] = sweep_result.get('errors', [])
        
        if (result['filled_count'] > 0 or 
            result.get('sweep_newly_logged', 0) > 0 or 
            result.get('tp_placed_count', 0) > 0 or
            result.get('oco_tp_cancelled', 0) > 0 or
            result.get('oco_sl_cancelled', 0) > 0):
            logger.info(
                f"[RECONCILE-{trading_mode}] Cycle complete: "
                f"{result['filled_count']}/{result['pending_count']} TP/SL filled, "
                f"{result.get('tp_placed_count', 0)} TPs placed for filled entries, "
                f"{result.get('sweep_newly_logged', 0)} retroactive logged, "
                f"OCO: {result.get('oco_tp_cancelled', 0)} TP/{result.get('oco_sl_cancelled', 0)} SL cancelled"
            )
        
        return result
        
    except Exception as e:
        logger.error(f"[RECONCILE] Cycle failed: {e}")
        return {
            "pending_count": 0,
            "filled_count": 0,
            "errors": [str(e)],
            "fills_logged": []
        }
