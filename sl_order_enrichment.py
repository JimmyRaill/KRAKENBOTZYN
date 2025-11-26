"""
SL Order ID Enrichment for Kraken Bracket Orders

When placing bracket orders on Kraken, the SL is embedded via close[ordertype]=stop-loss.
After the entry fills, Kraken materializes the SL as a separate child order with:
- parenttxid = entry_order_id
- ordertype = stop-loss

This module finds and stores the SL order ID for complete OCO tracking.
"""

from typing import Optional, Dict, Any
from loguru import logger
from exchange_manager import get_exchange


def find_sl_order_id_from_entry(entry_order_id: str, symbol: str) -> Optional[str]:
    """
    Find the materialized SL order ID for a filled entry order.
    
    After an entry order fills on Kraken, the conditional close SL becomes
    a standalone order with parenttxid pointing to the entry. This function
    queries Kraken to find that child order.
    
    PHASE 2B PARTIAL FILL HOTFIX:
    - Kraken creates ONE SL per partial fill (e.g., 4 partials = 4 SLs)
    - This function now detects and warns about multiple SLs
    - Returns the FIRST SL found (oldest - smallest qty)
    - Multiple SLs are expected with partial fills and logged for diagnostics
    
    Args:
        entry_order_id: Entry order ID (parent order)
        symbol: Trading pair (e.g., "1INCH/USD")
        
    Returns:
        SL order ID if found (first one if multiple), None otherwise
        
    Example:
        sl_id = find_sl_order_id_from_entry("OVACX2-GOESD-IOA2ZO", "1INCH/USD")
        # Returns: "OABCD1-EFGHI-JKLMN"
    """
    try:
        exchange = get_exchange()
        
        # Fetch all open orders for the symbol
        open_orders = exchange.fetch_open_orders(symbol)
        
        logger.debug(f"[SL-ENRICHMENT] Searching {len(open_orders)} open orders for SL child of {entry_order_id}")
        
        # PHASE 2B: Collect ALL matching SL orders (partial fills create multiple)
        matching_sls = []
        
        for order in open_orders:
            order_info = order.get('info', {})
            
            # Check if this is a stop-loss order with matching parent
            order_type = order.get('type', '').lower()
            parent_txid = order_info.get('parenttxid', '')
            
            if order_type == 'stop-loss' and parent_txid == entry_order_id:
                sl_order_id = order.get('id', '')
                sl_qty = float(order.get('amount', 0) or order.get('remaining', 0) or 0)
                matching_sls.append({
                    "order_id": sl_order_id,
                    "quantity": sl_qty,
                    "stop_price": float(order.get('stopPrice', 0) or 0)
                })
        
        # PHASE 2B: Warn about multiple SLs (partial fill symptom)
        if len(matching_sls) > 1:
            total_sl_qty = sum(sl['quantity'] for sl in matching_sls)
            sl_ids = [sl['order_id'] for sl in matching_sls]
            logger.warning(
                f"[SL-ENRICHMENT] ⚠️ MULTIPLE SLs DETECTED for {symbol} entry {entry_order_id}: "
                f"{len(matching_sls)} SL orders (partial fill symptom) | "
                f"SL IDs: {sl_ids} | Total SL qty: {total_sl_qty:.8f}"
            )
            logger.warning(
                f"[SL-ENRICHMENT] This is expected with partial fills. "
                f"OCO monitor will cancel all SLs when TP fills."
            )
            
            # Return the first SL (Kraken creates them in order of fill)
            first_sl = matching_sls[0]['order_id']
            logger.info(f"[SL-ENRICHMENT] Using first SL: {first_sl}")
            return first_sl
        
        if matching_sls:
            sl_order_id = matching_sls[0]['order_id']
            logger.success(f"[SL-ENRICHMENT] ✅ Found SL order: {sl_order_id} (parent: {entry_order_id})")
            return sl_order_id
        
        # Not found in open orders, might be too soon after fill
        logger.warning(f"[SL-ENRICHMENT] ⚠️ SL order not found yet for entry {entry_order_id}")
        return None
        
    except Exception as e:
        logger.error(f"[SL-ENRICHMENT] Error finding SL order: {e}")
        return None


def enrich_bracket_with_sl_order_id(
    entry_order_id: str,
    symbol: str,
    max_attempts: int = 3,
    retry_delay: float = 2.0
) -> Optional[str]:
    """
    Enrich bracket with SL order ID, retrying if not immediately available.
    
    Kraken may take a moment to materialize the SL order after entry fills.
    This function retries multiple times before giving up.
    
    Args:
        entry_order_id: Entry order ID
        symbol: Trading pair
        max_attempts: Maximum retry attempts (default 3)
        retry_delay: Seconds between retries (default 2.0s)
        
    Returns:
        SL order ID if found, None if not found after retries
    """
    import time
    
    for attempt in range(1, max_attempts + 1):
        logger.info(f"[SL-ENRICHMENT] Attempt {attempt}/{max_attempts}: Finding SL for {entry_order_id}")
        
        sl_order_id = find_sl_order_id_from_entry(entry_order_id, symbol)
        
        if sl_order_id:
            return sl_order_id
        
        if attempt < max_attempts:
            logger.debug(f"[SL-ENRICHMENT] Waiting {retry_delay}s before retry...")
            time.sleep(retry_delay)
    
    logger.error(f"[SL-ENRICHMENT] ❌ Failed to find SL order after {max_attempts} attempts")
    return None


def store_sl_order_id(entry_order_id: str, sl_order_id: str) -> bool:
    """
    Store SL order ID in pending_child_orders table for OCO tracking.
    
    Args:
        entry_order_id: Entry order ID (primary key)
        sl_order_id: SL order ID to store
        
    Returns:
        True if stored successfully, False otherwise
    """
    from evaluation_log import _get_connection
    
    try:
        db = _get_connection()
        
        query = """
            UPDATE pending_child_orders
            SET sl_order_id = ?
            WHERE order_id = ?
              AND order_type = 'entry_pending_tp'
        """
        
        db.execute(query, (sl_order_id, entry_order_id))
        db.commit()
        db.close()
        
        logger.success(f"[SL-ENRICHMENT] ✅ Stored SL order ID: {sl_order_id} for entry {entry_order_id}")
        return True
        
    except Exception as e:
        logger.error(f"[SL-ENRICHMENT] Failed to store SL order ID: {e}")
        return False


def enrich_and_store_sl_order_id(
    entry_order_id: str,
    symbol: str,
    max_attempts: int = 3
) -> Optional[str]:
    """
    Find and store SL order ID for a filled entry order.
    
    This is the main function to call after an entry fills and TP is placed.
    It finds the materialized SL order and stores it for OCO monitoring.
    
    Args:
        entry_order_id: Entry order ID
        symbol: Trading pair
        max_attempts: Maximum retry attempts
        
    Returns:
        SL order ID if found and stored, None otherwise
    """
    # Find SL order ID
    sl_order_id = enrich_bracket_with_sl_order_id(entry_order_id, symbol, max_attempts)
    
    if not sl_order_id:
        return None
    
    # Store in database
    success = store_sl_order_id(entry_order_id, sl_order_id)
    
    if success:
        return sl_order_id
    else:
        return None
