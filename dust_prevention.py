"""
Dust Position Prevention System

Prevents creating positions below Kraken's minimum order sizes that cannot be sold later.

Kraken Policy (2025):
- Each symbol has unique minimum order size (base currency)
- No automatic dust cleanup - dust positions remain stuck
- Examples: BTC=0.002, ETH=0.02, varies by asset
- Can only consolidate via "Buy Crypto" button at $1 minimum

This module:
- Fetches symbol-specific minimums from Kraken API
- Caches minimums with 1-hour TTL to reduce API calls
- Provides 7% safety buffer to prevent near-dust positions
- Validates both entry and exit quantities before execution
"""

from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from loguru import logger
import json
import os


class DustPrevention:
    """
    Manages Kraken minimum order size requirements to prevent dust positions.
    """
    
    CACHE_FILE = "kraken_minimums_cache.json"
    CACHE_TTL_HOURS = 1  # Refresh cached minimums every hour
    DUST_BUFFER_PCT = 0.07  # 7% safety buffer above minimum
    
    def __init__(self, exchange=None):
        """
        Initialize dust prevention system.
        
        Args:
            exchange: ccxt exchange instance (optional, will get from ExchangeManager if None)
        """
        self.exchange = exchange
        self._cache = {}
        self._cache_timestamp = None
        self._load_cache()
    
    def _load_cache(self):
        """Load cached minimums from disk if available and fresh."""
        if not os.path.exists(self.CACHE_FILE):
            return
        
        try:
            with open(self.CACHE_FILE, 'r') as f:
                data = json.load(f)
            
            # Check if cache is still fresh
            cache_time = datetime.fromisoformat(data.get('timestamp', '1970-01-01'))
            age = datetime.now() - cache_time
            
            if age < timedelta(hours=self.CACHE_TTL_HOURS):
                self._cache = data.get('minimums', {})
                self._cache_timestamp = cache_time
                logger.info(f"[DUST-PREVENTION] Loaded {len(self._cache)} cached minimums (age: {age.seconds//60}m)")
            else:
                logger.info(f"[DUST-PREVENTION] Cache expired (age: {age.seconds//3600}h), will refresh")
        except Exception as e:
            logger.warning(f"[DUST-PREVENTION] Failed to load cache: {e}")
    
    def _save_cache(self):
        """Save minimums to disk cache."""
        try:
            data = {
                'timestamp': datetime.now().isoformat(),
                'minimums': self._cache
            }
            with open(self.CACHE_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"[DUST-PREVENTION] Saved {len(self._cache)} minimums to cache")
        except Exception as e:
            logger.warning(f"[DUST-PREVENTION] Failed to save cache: {e}")
    
    def _get_exchange(self):
        """Get exchange instance (lazy load from ExchangeManager if needed)."""
        if self.exchange is None:
            from exchange_manager import get_exchange
            self.exchange = get_exchange()
        return self.exchange
    
    def get_minimum_order_size(self, symbol: str) -> Optional[Dict[str, float]]:
        """
        Get minimum order size requirements for a symbol.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
        
        Returns:
            Dict with 'amount' (min quantity) and 'cost' (min USD value), or None on error
        """
        # Check cache first
        if symbol in self._cache:
            cache_age = (datetime.now() - self._cache_timestamp).total_seconds() if self._cache_timestamp else float('inf')
            if cache_age < self.CACHE_TTL_HOURS * 3600:
                logger.debug(f"[DUST-PREVENTION] Using cached minimums for {symbol}")
                return self._cache[symbol]
        
        # Fetch from exchange
        try:
            exchange = self._get_exchange()
            market = exchange.market(symbol)
            
            if not market:
                logger.warning(f"[DUST-PREVENTION] Market not found for {symbol}")
                return None
            
            limits = market.get('limits', {})
            amount_limits = limits.get('amount', {})
            cost_limits = limits.get('cost', {})
            
            min_amount = float(amount_limits.get('min', 0) or 0)
            min_cost = float(cost_limits.get('min', 0) or 0)
            
            minimums = {
                'amount': min_amount,
                'cost': min_cost
            }
            
            # Cache the result
            self._cache[symbol] = minimums
            self._cache_timestamp = datetime.now()
            self._save_cache()
            
            logger.info(f"[DUST-PREVENTION] Fetched minimums for {symbol}: amount={min_amount}, cost=${min_cost}")
            return minimums
            
        except Exception as e:
            logger.error(f"[DUST-PREVENTION] Failed to fetch minimums for {symbol}: {e}")
            return None
    
    def validate_order_size(
        self, 
        symbol: str, 
        quantity: float, 
        price: float,
        apply_buffer: bool = True
    ) -> Tuple[bool, str]:
        """
        Validate if order size meets Kraken minimums with safety buffer.
        
        Args:
            symbol: Trading pair
            quantity: Order quantity (base currency)
            price: Order price (USD)
            apply_buffer: Apply 7% safety buffer (default True)
        
        Returns:
            (is_valid, reason) tuple
        """
        minimums = self.get_minimum_order_size(symbol)
        
        if not minimums:
            logger.warning(f"[DUST-PREVENTION] Could not fetch minimums for {symbol}, allowing trade")
            return True, "OK (minimums unavailable)"
        
        min_amount = minimums['amount']
        min_cost = minimums['cost']
        
        # Apply safety buffer to prevent near-dust positions
        if apply_buffer:
            buffer_multiplier = 1.0 + self.DUST_BUFFER_PCT
            min_amount_with_buffer = min_amount * buffer_multiplier
            min_cost_with_buffer = min_cost * buffer_multiplier
        else:
            min_amount_with_buffer = min_amount
            min_cost_with_buffer = min_cost
        
        # Calculate actual order cost
        order_cost = quantity * price
        
        # Check amount minimum
        if min_amount > 0 and quantity < min_amount_with_buffer:
            buffer_note = f" (with {self.DUST_BUFFER_PCT*100:.0f}% buffer)" if apply_buffer else ""
            return False, f"Quantity {quantity:.8f} below minimum {min_amount:.8f}{buffer_note}"
        
        # Check cost minimum
        if min_cost > 0 and order_cost < min_cost_with_buffer:
            buffer_note = f" (with {self.DUST_BUFFER_PCT*100:.0f}% buffer)" if apply_buffer else ""
            return False, f"Cost ${order_cost:.2f} below minimum ${min_cost:.2f}{buffer_note}"
        
        return True, "OK"
    
    def is_dust_position(self, symbol: str, quantity: float, price: float) -> bool:
        """
        Check if position is dust (below minimum tradeable size).
        
        Args:
            symbol: Trading pair
            quantity: Position quantity
            price: Current price
        
        Returns:
            True if position is dust, False otherwise
        """
        is_valid, _ = self.validate_order_size(symbol, quantity, price, apply_buffer=False)
        return not is_valid
    
    def calculate_minimum_trade_size(
        self, 
        symbol: str, 
        price: float,
        apply_buffer: bool = True
    ) -> Optional[float]:
        """
        Calculate minimum quantity required for a valid trade.
        
        Args:
            symbol: Trading pair
            price: Current price
            apply_buffer: Apply 7% safety buffer (default True)
        
        Returns:
            Minimum quantity (base currency) or None on error
        """
        minimums = self.get_minimum_order_size(symbol)
        
        if not minimums:
            return None
        
        min_amount = minimums['amount']
        min_cost = minimums['cost']
        
        # Apply buffer if requested
        if apply_buffer:
            buffer_multiplier = 1.0 + self.DUST_BUFFER_PCT
            min_amount = min_amount * buffer_multiplier
            min_cost = min_cost * buffer_multiplier
        
        # Calculate minimum quantity from both constraints
        min_qty_from_amount = min_amount
        min_qty_from_cost = min_cost / price if price > 0 else 0
        
        # Take the larger of the two
        min_qty = max(min_qty_from_amount, min_qty_from_cost)
        
        return min_qty


# Singleton instance
_dust_prevention_instance = None


def get_dust_prevention() -> DustPrevention:
    """Get singleton DustPrevention instance."""
    global _dust_prevention_instance
    if _dust_prevention_instance is None:
        _dust_prevention_instance = DustPrevention()
    return _dust_prevention_instance
