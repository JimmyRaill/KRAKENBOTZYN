"""
Synthetic OCO (One-Cancels-Other) Monitor for Kraken

Kraken does not support true OCO for below-market limit entries with TP/SL.
This module provides synthetic OCO logic:
- When TP executes → Cancel SL automatically
- When SL executes → Cancel TP automatically

This prevents double-fills and maintains proper bracket order semantics.
Runs as background task in reconciliation cycle (every 60 seconds).

PHASE 2B-2: Full bracket exit handling with position_tracker cleanup.
"""

from typing import List, Dict, Any, Optional
from loguru import logger
from evaluation_log import _get_connection
from position_tracker import remove_position


def check_and_cancel_opposite_orders(trading_mode: str = "LIVE") -> Dict[str, int]:
    """
    Check all active bracket orders and cancel opposite orders when one executes.
    
    This implements synthetic OCO logic:
    - Query all pending bracket orders from evaluation_log
    - Check Kraken for fills on TP or SL orders
    - When one fills, cancel the opposite order
    - Log cancellations to executed_orders table
    
    Args:
        trading_mode: "LIVE" or "PAPER"
        
    Returns:
        Stats dict: {"checked": N, "tp_cancelled": N, "sl_cancelled": N}
    """
    from exchange_manager import get_exchange
    
    stats = {
        "checked": 0,
        "tp_cancelled": 0,
        "sl_cancelled": 0,
        "errors": 0
    }
    
    try:
        db = _get_connection()
        
        # Get all active bracket orders with TP (SL might be null initially)
        query = """
            SELECT DISTINCT
                symbol,
                order_id AS entry_order_id,
                tp_order_id,
                sl_order_id,
                order_type,
                created_at
            FROM pending_child_orders
            WHERE status = 'filled'
              AND order_type = 'entry_pending_tp'
              AND tp_order_id IS NOT NULL
            ORDER BY created_at DESC
        """
        
        cursor = db.execute(query)
        active_brackets = cursor.fetchall()
        
        if not active_brackets:
            logger.debug(f"[OCO-MONITOR-{trading_mode}] No active brackets with TP+SL found")
            return stats
        
        logger.info(f"[OCO-MONITOR-{trading_mode}] Checking {len(active_brackets)} active bracket(s)")
        stats["checked"] = len(active_brackets)
        
        exchange = get_exchange()
        
        for bracket in active_brackets:
            symbol = bracket[0]
            entry_id = bracket[1]
            tp_id = bracket[2]
            sl_id = bracket[3]
            
            try:
                # Enrich missing SL order IDs on-the-fly
                if not sl_id:
                    from sl_order_enrichment import enrich_and_store_sl_order_id
                    sl_id = enrich_and_store_sl_order_id(entry_id, symbol, max_attempts=2)
                    
                    if not sl_id:
                        logger.debug(f"[OCO-MONITOR-{trading_mode}] SL ID not yet available for {symbol} (entry: {entry_id})")
                        continue  # Skip this bracket for now, will retry next cycle
                
                # PHASE 2B: Find ALL SL orders for this entry (partial fills create multiple)
                all_sl_ids = _find_all_sl_orders_for_entry(exchange, entry_id, symbol)
                
                # Fetch open orders to see which are still active
                open_orders = exchange.fetch_open_orders(symbol)
                open_order_ids = {order['id'] for order in open_orders}
                
                tp_still_open = tp_id in open_order_ids
                sl_still_open = sl_id in open_order_ids
                
                # Case 1: TP filled, SL still open → Cancel ALL SLs (partial fills create multiple)
                if not tp_still_open and sl_still_open:
                    # PHASE 2B: Cancel ALL SL orders for this entry (partial fills create multiple)
                    sls_to_cancel = all_sl_ids if all_sl_ids else [sl_id]
                    
                    logger.info(
                        f"[OCO-MONITOR-{trading_mode}] {symbol}: TP filled ({tp_id}), "
                        f"cancelling {len(sls_to_cancel)} SL(s): {sls_to_cancel}"
                    )
                    
                    cancelled_count = 0
                    for sl_to_cancel in sls_to_cancel:
                        try:
                            exchange.cancel_order(sl_to_cancel, symbol)
                            logger.success(f"[OCO-MONITOR-{trading_mode}] ✅ Cancelled SL: {sl_to_cancel}")
                            cancelled_count += 1
                            
                            # Log each cancellation to executed_orders
                            _log_oco_cancellation(
                                symbol=symbol,
                                cancelled_order_id=sl_to_cancel,
                                cancelled_type="SL",
                                reason=f"TP filled: {tp_id}",
                                trading_mode=trading_mode
                            )
                            
                        except Exception as e:
                            # Order may already be cancelled/executed - not fatal
                            logger.warning(f"[OCO-MONITOR-{trading_mode}] Could not cancel SL {sl_to_cancel}: {e}")
                    
                    stats["sl_cancelled"] += cancelled_count
                    
                    # Mark bracket as complete
                    _mark_bracket_complete(entry_id, "tp_filled", db)
                    
                    # PHASE 2B-2: Clean up position_tracker (non-blocking - bracket already marked complete above)
                    try:
                        removed = remove_position(symbol)
                        if removed:
                            logger.info(f"[OCO-MONITOR-{trading_mode}] ✅ Position closed (TP): {symbol}")
                        else:
                            logger.debug(f"[OCO-MONITOR-{trading_mode}] Position not in tracker: {symbol}")
                    except FileNotFoundError as fnf_err:
                        # Lock file may not exist yet - non-fatal, position_tracker will create on next add
                        logger.warning(f"[OCO-MONITOR-{trading_mode}] Lock file not found, skipping cleanup: {fnf_err}")
                    except Exception as pos_err:
                        logger.warning(f"[OCO-MONITOR-{trading_mode}] Failed to remove position {symbol}: {pos_err}")
                
                # Case 2: SL filled, TP still open → Cancel TP
                elif not sl_still_open and tp_still_open:
                    logger.info(f"[OCO-MONITOR-{trading_mode}] {symbol}: SL filled ({sl_id}), cancelling TP ({tp_id})")
                    
                    try:
                        exchange.cancel_order(tp_id, symbol)
                        logger.success(f"[OCO-MONITOR-{trading_mode}] ✅ Cancelled TP: {tp_id}")
                        stats["tp_cancelled"] += 1
                        
                        # Log cancellation to executed_orders
                        _log_oco_cancellation(
                            symbol=symbol,
                            cancelled_order_id=tp_id,
                            cancelled_type="TP",
                            reason=f"SL filled: {sl_id}",
                            trading_mode=trading_mode
                        )
                        
                        # Mark bracket as complete
                        _mark_bracket_complete(entry_id, "sl_filled", db)
                        
                        # PHASE 2B-2: Clean up position_tracker (non-blocking - bracket already marked complete above)
                        try:
                            removed = remove_position(symbol)
                            if removed:
                                logger.info(f"[OCO-MONITOR-{trading_mode}] ✅ Position closed (SL): {symbol}")
                            else:
                                logger.debug(f"[OCO-MONITOR-{trading_mode}] Position not in tracker: {symbol}")
                        except FileNotFoundError as fnf_err:
                            # Lock file may not exist yet - non-fatal, position_tracker will create on next add
                            logger.warning(f"[OCO-MONITOR-{trading_mode}] Lock file not found, skipping cleanup: {fnf_err}")
                        except Exception as pos_err:
                            logger.warning(f"[OCO-MONITOR-{trading_mode}] Failed to remove position {symbol}: {pos_err}")
                        
                    except Exception as e:
                        logger.error(f"[OCO-MONITOR-{trading_mode}] Failed to cancel TP {tp_id}: {e}")
                        stats["errors"] += 1
                
                # Case 3: Both closed → Mark complete (shouldn't happen, but handle gracefully)
                elif not tp_still_open and not sl_still_open:
                    logger.warning(f"[OCO-MONITOR-{trading_mode}] {symbol}: Both TP and SL closed ({tp_id}, {sl_id})")
                    _mark_bracket_complete(entry_id, "both_filled", db)
                    
                    # PHASE 2B-2: Clean up position_tracker even in edge case (non-blocking)
                    try:
                        removed = remove_position(symbol)
                        if removed:
                            logger.info(f"[OCO-MONITOR-{trading_mode}] ✅ Position closed (both): {symbol}")
                    except FileNotFoundError as fnf_err:
                        logger.warning(f"[OCO-MONITOR-{trading_mode}] Lock file not found, skipping cleanup: {fnf_err}")
                    except Exception as pos_err:
                        logger.warning(f"[OCO-MONITOR-{trading_mode}] Failed to remove position {symbol}: {pos_err}")
                
                # Case 4: Both still open → Normal state, continue monitoring
                else:
                    logger.debug(f"[OCO-MONITOR-{trading_mode}] {symbol}: TP and SL both active ({tp_id}, {sl_id})")
                    
            except Exception as e:
                logger.error(f"[OCO-MONITOR-{trading_mode}] Error checking bracket {entry_id}: {e}")
                stats["errors"] += 1
        
        if stats["tp_cancelled"] > 0 or stats["sl_cancelled"] > 0:
            logger.info(f"[OCO-MONITOR-{trading_mode}] ✅ Cancelled {stats['tp_cancelled']} TP(s), {stats['sl_cancelled']} SL(s)")
        
        return stats
        
    except Exception as e:
        logger.error(f"[OCO-MONITOR-{trading_mode}] Fatal error: {e}")
        stats["errors"] += 1
        return stats


def _log_oco_cancellation(
    symbol: str,
    cancelled_order_id: str,
    cancelled_type: str,
    reason: str,
    trading_mode: str
) -> None:
    """
    Log OCO cancellation to executed_orders table for forensic tracking.
    
    Args:
        symbol: Trading pair
        cancelled_order_id: Order ID that was cancelled
        cancelled_type: "TP" or "SL"
        reason: Why it was cancelled (e.g., "TP filled: ORDER123")
        trading_mode: "LIVE" or "PAPER"
    """
    try:
        db = _get_connection()
        
        query = """
            INSERT INTO executed_orders (
                timestamp,
                symbol,
                side,
                order_type,
                order_id,
                quantity,
                fill_price,
                status,
                reason,
                source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        import time
        db.execute(query, (
            int(time.time() * 1000),
            symbol,
            "cancel",
            cancelled_type,
            cancelled_order_id,
            0.0,
            0.0,
            "cancelled",
            f"OCO: {reason}",
            f"oco_monitor_{trading_mode.lower()}"
        ))
        db.commit()
        
        logger.debug(f"[OCO-LOG] Logged cancellation: {cancelled_order_id} ({cancelled_type})")
        
    except Exception as e:
        logger.error(f"[OCO-LOG] Failed to log cancellation: {e}")


def _find_all_sl_orders_for_entry(exchange, entry_order_id: str, symbol: str) -> List[str]:
    """
    PHASE 2B PARTIAL FILL HOTFIX:
    Find ALL SL orders for a given entry order.
    
    When entries fill in multiple partials, Kraken creates one SL per partial.
    This function returns all matching SL order IDs so we can cancel them all.
    
    Args:
        exchange: CCXT exchange instance
        entry_order_id: Entry order ID (parent of SL orders)
        symbol: Trading pair
        
    Returns:
        List of all SL order IDs for this entry (empty if none found)
    """
    try:
        open_orders = exchange.fetch_open_orders(symbol)
        matching_sls = []
        
        for order in open_orders:
            order_info = order.get('info', {})
            order_type = order.get('type', '').lower()
            parent_txid = order_info.get('parenttxid', '')
            
            if order_type == 'stop-loss' and parent_txid == entry_order_id:
                sl_order_id = order.get('id', '')
                if sl_order_id:
                    matching_sls.append(sl_order_id)
        
        if len(matching_sls) > 1:
            logger.info(
                f"[OCO-MULTI-SL] Found {len(matching_sls)} SL orders for entry {entry_order_id}: {matching_sls}"
            )
        
        return matching_sls
        
    except Exception as e:
        logger.error(f"[OCO-MULTI-SL] Error finding SL orders for {entry_order_id}: {e}")
        return []


def _mark_bracket_complete(entry_order_id: str, exit_reason: str, db) -> None:
    """
    Mark bracket order as complete in pending_child_orders table.
    
    Args:
        entry_order_id: Entry order ID
        exit_reason: "tp_filled", "sl_filled", or "both_filled"
        db: Database connection
    """
    try:
        query = """
            UPDATE pending_child_orders
            SET 
                status = 'complete',
                exit_reason = ?
            WHERE entry_order_id = ?
              AND order_type = 'entry_pending_tp'
        """
        
        db.execute(query, (exit_reason, entry_order_id))
        db.commit()
        
        logger.debug(f"[OCO-COMPLETE] Marked bracket complete: {entry_order_id} ({exit_reason})")
        
    except Exception as e:
        logger.error(f"[OCO-COMPLETE] Failed to mark complete: {e}")


def get_active_bracket_count(trading_mode: str = "LIVE") -> int:
    """
    Get count of active bracket orders (for monitoring).
    
    Args:
        trading_mode: "LIVE" or "PAPER"
        
    Returns:
        Number of active brackets with TP+SL
    """
    try:
        db = _get_connection()
        
        query = """
            SELECT COUNT(DISTINCT entry_order_id)
            FROM pending_child_orders
            WHERE status = 'filled'
              AND order_type = 'entry_pending_tp'
              AND tp_order_id IS NOT NULL
              AND sl_order_id IS NOT NULL
        """
        
        cursor = db.execute(query)
        count = cursor.fetchone()[0]
        return count
        
    except Exception as e:
        logger.error(f"[OCO-COUNT] Failed to count brackets: {e}")
        return 0
