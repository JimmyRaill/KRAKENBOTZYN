"""
Settlement Detection System for Kraken SPOT Accounts

This module provides intelligent settlement detection to replace hardcoded delays.
After entry orders fill, Kraken SPOT accounts need time to "settle" positions before
allowing sell orders. Instead of guessing delays, this polls balance to detect when
funds are actually available.

Key Features:
- Balance polling with configurable intervals
- Exponential backoff retry for TP placement
- Asset normalization (CCXT symbol -> Kraken asset code)
- Clear logging and timeout handling
- Fallback to reconciliation on exhaustion
"""

import time
from typing import Optional, Tuple, Dict, Any
from loguru import logger


def extract_base_asset(symbol: str) -> str:
    """
    Extract base asset from CCXT symbol format and normalize for Kraken balance keys.
    
    Kraken uses X-prefixed codes for major assets in balances:
    - BTC → XXBT
    - ETH → XETH  
    - USD → ZUSD
    - EUR → ZEUR
    - Other assets usually match (ASTER, ALGO, etc.)
    
    Examples:
        "BTC/USD" -> "XXBT"
        "ETH/USD" -> "XETH"
        "ASTER/USD" -> "ASTER"
        "1INCH/USD" -> "1INCH"
    
    Args:
        symbol: Trading pair in CCXT format (e.g., "BTC/USD")
        
    Returns:
        Kraken balance key (e.g., "XXBT", "XETH", "ASTER")
    """
    base = symbol.split('/')[0]
    
    # Kraken balance normalization (X-prefixed major assets)
    kraken_map = {
        'BTC': 'XXBT',
        'ETH': 'XETH',
        'USD': 'ZUSD',
        'EUR': 'ZEUR',
        'GBP': 'ZGBP',
        'JPY': 'ZJPY',
        'CAD': 'ZCAD',
        'AUD': 'ZAUD'
    }
    
    return kraken_map.get(base, base)


def wait_for_settlement(
    symbol: str,
    side: str,
    filled_qty: float,
    fill_price: float,
    timeout: int = 30,
    poll_interval: float = 1.0
) -> Tuple[bool, str]:
    """
    Wait for Kraken to settle a filled position by polling balance.
    
    After an entry order fills, this function polls the balance to detect when
    the bought/sold asset appears in the "free" balance, indicating the position
    has settled and can be resold/rebought.
    
    Args:
        symbol: Trading pair in CCXT format (e.g., "ASTER/USD")
        side: Order side ('buy' or 'sell')
        filled_qty: Quantity that was filled
        fill_price: Average fill price
        timeout: Maximum seconds to wait (default 30s)
        poll_interval: Seconds between balance checks (default 1.0s)
        
    Returns:
        (settled, message) - True if settled within timeout, False otherwise
        
    Example:
        settled, msg = wait_for_settlement("ASTER/USD", "buy", 12.06, 1.24)
        if settled:
            place_tp_order(...)
    """
    from exchange_manager import get_exchange
    
    base_asset = extract_base_asset(symbol)
    start_time = time.time()
    attempt = 0
    
    logger.info(f"[SETTLEMENT] Waiting for {base_asset} to settle ({filled_qty:.4f} units @ ${fill_price:.4f})")
    logger.info(f"[SETTLEMENT] Polling balance every {poll_interval}s (max {timeout}s)")
    
    # For buy orders, we expect the base asset to appear in free balance
    # For sell orders, we expect the quote asset (USD) to appear
    asset_to_check = base_asset if side.lower() == 'buy' else 'USD'
    
    # Allow 1% tolerance for fees/rounding
    min_expected_qty = filled_qty * 0.99 if side.lower() == 'buy' else (filled_qty * fill_price * 0.99)
    
    while time.time() - start_time < timeout:
        attempt += 1
        elapsed = time.time() - start_time
        
        try:
            exchange = get_exchange()
            balance = exchange.fetch_balance()
            
            free_balance = balance.get('free', {})
            asset_free = free_balance.get(asset_to_check, 0.0)
            
            logger.debug(f"[SETTLEMENT] Attempt {attempt} ({elapsed:.1f}s): {asset_to_check} free={asset_free:.4f}, need={min_expected_qty:.4f}")
            
            # Check if settled (with tolerance for fees)
            if asset_free >= min_expected_qty:
                logger.success(f"[SETTLEMENT] ✅ SETTLED after {elapsed:.1f}s - {asset_to_check} available: {asset_free:.4f}")
                return True, f"Settled after {elapsed:.1f}s"
            
            # Wait before next poll
            time.sleep(poll_interval)
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[SETTLEMENT] Balance check failed: {error_msg}")
            # Don't bail immediately on API errors, retry until timeout
            time.sleep(poll_interval)
    
    # Timeout reached
    elapsed = time.time() - start_time
    logger.warning(f"[SETTLEMENT] ⚠️ TIMEOUT after {elapsed:.1f}s - {asset_to_check} not settled")
    return False, f"Settlement timeout after {elapsed:.1f}s"


def place_tp_with_retry(
    symbol: str,
    side: str,
    quantity: float,
    tp_price: float,
    fill_price: float,
    max_attempts: int = 5,
    initial_backoff: float = 1.0
) -> Tuple[bool, str, Optional[str]]:
    """
    Place take-profit order with exponential backoff retry.
    
    Attempts to place TP, retrying on "insufficient funds" errors with
    exponential backoff (1s, 2s, 4s, 8s, 16s). Re-checks settlement on
    each retry before attempting placement.
    
    Args:
        symbol: Trading pair in CCXT format
        side: TP order side ('sell' for long, 'buy' for short)
        quantity: Quantity to close (matches entry fill)
        tp_price: Take-profit limit price
        fill_price: Entry fill price (for settlement detection)
        max_attempts: Maximum retry attempts (default 5)
        initial_backoff: Initial backoff delay in seconds (default 1.0s)
        
    Returns:
        (success, message, order_id) - True if placed, False otherwise
        
    Example:
        success, msg, order_id = place_tp_with_retry(
            "ASTER/USD", "sell", 12.06, 1.27, 1.24
        )
    """
    from kraken_native_api import get_kraken_native_api
    
    native_api = get_kraken_native_api()
    entry_side = 'buy' if side.lower() == 'sell' else 'sell'
    
    for attempt in range(1, max_attempts + 1):
        logger.info(f"[TP-RETRY] Attempt {attempt}/{max_attempts}: Placing TP {side} @ ${tp_price:.4f}")
        
        # Try to place TP
        success, message, result = native_api.place_take_profit_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            take_profit_price=tp_price,
            validate=False
        )
        
        if success:
            order_id = result.get('txid', [None])[0] if result else None
            logger.success(f"[TP-RETRY] ✅ SUCCESS on attempt {attempt}: {order_id}")
            return True, f"TP placed on attempt {attempt}", order_id
        
        # Check if error is retryable (settlement or transient API/network issues)
        error_lower = message.lower()
        is_retryable = (
            "insufficient funds" in error_lower or
            "timeout" in error_lower or
            "connection" in error_lower or
            "rate limit" in error_lower or
            "service unavailable" in error_lower or
            "internal error" in error_lower or
            "etapi" in error_lower or  # Kraken API errors
            "eservice" in error_lower
        )
        
        if is_retryable:
            if attempt < max_attempts:
                # Exponential backoff: 1s, 2s, 4s, 8s, 16s
                backoff_delay = initial_backoff * (2 ** (attempt - 1))
                logger.warning(f"[TP-RETRY] ⚠️ Retryable error ({message}) - waiting {backoff_delay}s before retry {attempt + 1}")
                
                # Re-check settlement before retry (especially for "insufficient funds")
                if "insufficient funds" in error_lower:
                    settled, settle_msg = wait_for_settlement(
                        symbol=symbol,
                        side=entry_side,
                        filled_qty=quantity,
                        fill_price=fill_price,
                        timeout=int(backoff_delay),
                        poll_interval=0.5
                    )
                    
                    if not settled:
                        logger.warning(f"[TP-RETRY] Still not settled: {settle_msg}")
                else:
                    # Just wait for backoff (network/API errors)
                    time.sleep(backoff_delay)
            else:
                # Max attempts reached
                logger.error(f"[TP-RETRY] ❌ EXHAUSTED after {max_attempts} attempts - falling back to reconciliation")
                return False, f"Max retries exceeded: {message}", None
        else:
            # Non-retryable error (invalid order params, symbol not found, etc.)
            logger.error(f"[TP-RETRY] ❌ Non-retryable error: {message}")
            return False, message, None
    
    # Should never reach here, but handle gracefully
    return False, "Max retry attempts exhausted", None


def verify_settlement_ready(symbol: str, side: str, quantity: float) -> bool:
    """
    Quick check if position is likely settled (without waiting).
    
    Used for pre-flight checks before attempting TP placement.
    
    Args:
        symbol: Trading pair in CCXT format
        side: Original entry side ('buy' or 'sell')
        quantity: Filled quantity to verify
        
    Returns:
        True if balance shows settlement is likely complete
    """
    from exchange_manager import get_exchange
    
    try:
        base_asset = extract_base_asset(symbol)
        asset_to_check = base_asset if side.lower() == 'buy' else 'USD'
        
        exchange = get_exchange()
        balance = exchange.fetch_balance()
        free_balance = balance.get('free', {})
        asset_free = free_balance.get(asset_to_check, 0.0)
        
        # Check with 1% tolerance for fees
        min_expected = quantity * 0.99
        is_ready = asset_free >= min_expected
        
        if is_ready:
            logger.debug(f"[SETTLEMENT-CHECK] ✅ {asset_to_check} ready: {asset_free:.4f} >= {min_expected:.4f}")
        else:
            logger.debug(f"[SETTLEMENT-CHECK] ⏳ {asset_to_check} pending: {asset_free:.4f} < {min_expected:.4f}")
        
        return is_ready
        
    except Exception as e:
        logger.error(f"[SETTLEMENT-CHECK] Balance check failed: {e}")
        return False  # Conservative: assume not ready on error
