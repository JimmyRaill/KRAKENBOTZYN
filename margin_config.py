"""
margin_config.py - Central margin trading configuration and eligibility checks

Provides singleton helpers for:
- Effective leverage calculation (with hard 2.0 cap enforcement)
- Short selling eligibility verification
- Margin exposure tracking

This is the ONLY place leverage rules should be enforced.
"""

import os
import time
from typing import Optional, Tuple
from loguru import logger

# Cache for margin eligibility check (avoid repeated API calls)
_margin_eligible_cache: Optional[bool] = None
_margin_check_timestamp: float = 0
_MARGIN_CHECK_TTL = 3600  # Re-check every hour


def get_effective_leverage() -> float:
    """
    Get the effective leverage to use for margin trades.
    
    HARD CAP: Never returns > 2.0, even if config is misconfigured.
    
    Returns:
        Leverage multiplier (1.0 - 2.0)
    """
    from trading_config import get_config
    
    config = get_config()
    leverage = config.risk.max_leverage
    
    # HARD CAP ENFORCEMENT
    if leverage > 2.0:
        logger.warning(f"[MARGIN-CONFIG] Leverage {leverage} exceeds hard cap 2.0, clamping")
        return 2.0
    
    if leverage < 1.0:
        logger.warning(f"[MARGIN-CONFIG] Leverage {leverage} below minimum 1.0, using 1.0")
        return 1.0
    
    return leverage


def is_shorts_enabled() -> bool:
    """
    Check if short selling is enabled in configuration.
    
    Returns:
        True if ENABLE_SHORTS=true in config
    """
    from trading_config import get_config
    
    config = get_config()
    return config.risk.enable_shorts


def check_margin_eligibility() -> Tuple[bool, str]:
    """
    Verify account has margin trading access via Kraken API.
    
    Uses 1-hour cache to avoid repeated API calls.
    
    Returns:
        (is_eligible, message)
    """
    global _margin_eligible_cache, _margin_check_timestamp
    
    # Return cached result if still valid
    now = time.time()
    if _margin_eligible_cache is not None and (now - _margin_check_timestamp) < _MARGIN_CHECK_TTL:
        return _margin_eligible_cache, "Cached margin eligibility check"
    
    # Fresh check via Kraken API
    try:
        from kraken_native_api import KrakenNativeAPI
        
        api = KrakenNativeAPI()
        response = api._make_request('/0/private/TradeBalance', {'asset': 'ZUSD'})
        
        if response.get('error') and len(response['error']) > 0:
            error_msg = ', '.join(response['error'])
            logger.error(f"[MARGIN-CONFIG] Kraken margin check failed: {error_msg}")
            _margin_eligible_cache = False
            _margin_check_timestamp = now
            return False, f"API error: {error_msg}"
        
        result = response.get('result', {})
        
        # Check for margin-specific fields (mf = free margin)
        free_margin = result.get('mf')
        
        if free_margin is not None:
            logger.info(f"[MARGIN-CONFIG] ✅ Margin trading confirmed (free margin: {free_margin})")
            _margin_eligible_cache = True
            _margin_check_timestamp = now
            return True, f"Margin enabled, free margin: {free_margin}"
        else:
            logger.warning("[MARGIN-CONFIG] ❌ Margin trading NOT available on this account")
            _margin_eligible_cache = False
            _margin_check_timestamp = now
            return False, "Account does not have margin trading enabled"
    
    except Exception as e:
        logger.error(f"[MARGIN-CONFIG] Exception checking margin eligibility: {e}")
        _margin_eligible_cache = False
        _margin_check_timestamp = now
        return False, f"Exception: {str(e)}"


def get_current_margin_exposure() -> Tuple[float, float]:
    """
    Get current margin exposure from Kraken.
    
    Returns:
        (margin_used_usd, free_margin_usd)
    """
    try:
        from kraken_native_api import KrakenNativeAPI
        
        api = KrakenNativeAPI()
        response = api._make_request('/0/private/TradeBalance', {'asset': 'ZUSD'})
        
        if response.get('error') and len(response['error']) > 0:
            logger.error(f"[MARGIN-CONFIG] Failed to get margin exposure: {response['error']}")
            return 0.0, 0.0
        
        result = response.get('result', {})
        margin_used = float(result.get('m', 0.0))
        free_margin = float(result.get('mf', 0.0))
        
        return margin_used, free_margin
    
    except Exception as e:
        logger.error(f"[MARGIN-CONFIG] Exception getting margin exposure: {e}")
        return 0.0, 0.0


def can_open_short(equity_usd: float) -> Tuple[bool, str]:
    """
    Comprehensive pre-flight check before opening a short position.
    
    Verifies:
    1. Shorts are enabled in config
    2. Account has margin eligibility
    3. Margin exposure is within limits
    
    Args:
        equity_usd: Current total equity in USD
        
    Returns:
        (can_trade, reason)
    """
    from trading_config import get_config
    
    # Check 1: Shorts enabled?
    if not is_shorts_enabled():
        return False, "ENABLE_SHORTS is False in configuration"
    
    # Check 2: Margin eligible?
    eligible, msg = check_margin_eligibility()
    if not eligible:
        return False, f"Margin not eligible: {msg}"
    
    # Check 3: Within margin exposure limits?
    config = get_config()
    max_exposure_usd = equity_usd * config.risk.max_margin_exposure_pct
    
    margin_used, free_margin = get_current_margin_exposure()
    
    if margin_used >= max_exposure_usd:
        return False, f"Margin exposure limit reached: {margin_used:.2f} >= {max_exposure_usd:.2f} USD"
    
    return True, f"OK (margin: {margin_used:.2f}/{max_exposure_usd:.2f}, free: {free_margin:.2f})"


def invalidate_margin_cache():
    """Force re-check of margin eligibility on next call"""
    global _margin_eligible_cache, _margin_check_timestamp
    _margin_eligible_cache = None
    _margin_check_timestamp = 0
