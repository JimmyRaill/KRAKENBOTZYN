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
    update_reconciliation_stats
)
from exchange_manager import get_exchange


def reconcile_tp_sl_fills(trading_mode: str) -> Dict[str, Any]:
    """
    Check pending TP/SL orders and log any fills.
    
    Args:
        trading_mode: "live" or "paper"
    
    Returns:
        Summary of reconciliation results
    """
    try:
        pending_orders = get_pending_child_orders(trading_mode=trading_mode.lower(), status="pending")
        
        if not pending_orders:
            logger.debug(f"[RECONCILE-{trading_mode.upper()}] No pending orders to check")
            return {
                "pending_count": 0,
                "filled_count": 0,
                "errors": [],
                "fills_logged": []
            }
        
        logger.info(f"[RECONCILE-{trading_mode.upper()}] Checking {len(pending_orders)} pending orders")
        
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


def run_reconciliation_cycle():
    """
    Run reconciliation for current trading mode.
    Called by autopilot heartbeat every 60 seconds.
    """
    try:
        trading_mode = os.getenv("TRADING_MODE", "PAPER").upper()
        
        result = reconcile_tp_sl_fills(trading_mode)
        
        if result['filled_count'] > 0:
            logger.info(
                f"[RECONCILE-{trading_mode}] Cycle complete: "
                f"{result['filled_count']}/{result['pending_count']} orders filled"
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
