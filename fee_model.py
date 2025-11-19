"""
fee_model.py - Real-time Kraken fee tier tracking and calculations

Fetches actual trading fees from Kraken's TradeVolume API endpoint.
Caches results to minimize API calls (fees don't change frequently).
Essential for fee-aware market-only trading strategies.
"""

import time
from typing import Dict, Optional, Tuple
from loguru import logger

from exchange_manager import get_exchange


class FeeModel:
    """
    Fetches and caches Kraken trading fees based on actual account tier.
    
    Uses Kraken's /0/private/TradeVolume endpoint to get:
    - Current maker/taker fee percentages
    - Account trading volume (for tier calculation)
    - Per-pair fee overrides (if any)
    """
    
    def __init__(self, cache_ttl_seconds: int = 3600):
        """
        Initialize fee model with caching.
        
        Args:
            cache_ttl_seconds: How long to cache fee data (default 1 hour)
        """
        self.cache_ttl = cache_ttl_seconds
        self.last_fetch_time: Optional[float] = None
        
        # Cached fee data
        self.maker_fee_pct: float = 0.0016  # 0.16% default (Kraken standard)
        self.taker_fee_pct: float = 0.0026  # 0.26% default (Kraken standard)
        self.fee_tier: str = "standard"
        self.volume_30d: float = 0.0
        
        # Per-pair overrides (if Kraken provides them)
        self.pair_fees: Dict[str, Tuple[float, float]] = {}  # symbol -> (maker, taker)
        
        logger.info(f"[FEE-MODEL] Initialized with cache TTL={cache_ttl_seconds}s")
    
    def _needs_refresh(self) -> bool:
        """Check if cached fee data needs refreshing"""
        if self.last_fetch_time is None:
            return True
        
        elapsed = time.time() - self.last_fetch_time
        return elapsed > self.cache_ttl
    
    def fetch_fees(self, force: bool = False) -> bool:
        """
        Fetch current fees from Kraken TradeVolume API.
        
        Args:
            force: Force refresh even if cache is valid
        
        Returns:
            True if fetch succeeded, False if using cached/default values
        """
        if not force and not self._needs_refresh():
            logger.debug("[FEE-MODEL] Using cached fees")
            return True
        
        try:
            logger.info("[FEE-MODEL] Fetching fees from Kraken TradeVolume API...")
            exchange = get_exchange()
            
            # Kraken TradeVolume API call
            # Note: ccxt doesn't have a direct method for this, so we use private_post_TradeVolume
            if hasattr(exchange, 'private_post_TradeVolume'):
                response = exchange.private_post_TradeVolume()
            else:
                # Fallback: try using direct API call
                logger.warning("[FEE-MODEL] TradeVolume method not available, using defaults")
                return False
            
            # Parse response
            # Kraken response structure:
            # {
            #   "error": [],
            #   "result": {
            #     "currency": "ZUSD",
            #     "volume": "12345.67",
            #     "fees": {
            #       "XXBTZUSD": {
            #         "fee": "0.20",
            #         "minfee": "0.10",
            #         "maxfee": "0.20",
            #         "nextfee": "0.18",
            #         "nextvolume": "50000.00",
            #         "tiervolume": "0.00"
            #       }
            #     },
            #     "fees_maker": {
            #       "XXBTZUSD": {
            #         "fee": "0.10",
            #         "minfee": "0.00",
            #         "maxfee": "0.10",
            #         "nextfee": "0.08",
            #         "nextvolume": "50000.00",
            #         "tiervolume": "0.00"
            #       }
            #     }
            #   }
            # }
            
            if 'error' in response and response['error']:
                logger.error(f"[FEE-MODEL] Kraken API error: {response['error']}")
                return False
            
            result = response.get('result', {})
            
            # Extract 30-day volume
            volume_str = result.get('volume', '0')
            try:
                self.volume_30d = float(volume_str)
                logger.info(f"[FEE-MODEL] 30-day volume: ${self.volume_30d:,.2f}")
            except (ValueError, TypeError):
                logger.warning(f"[FEE-MODEL] Invalid volume: {volume_str}")
            
            # Extract fees (use first pair as representative)
            # Kraken returns fees per-pair, but they're usually consistent
            fees_taker = result.get('fees', {})
            fees_maker = result.get('fees_maker', {})
            
            if fees_taker and fees_maker:
                # Get first pair's fees as default
                first_pair_taker = next(iter(fees_taker.values()), {})
                first_pair_maker = next(iter(fees_maker.values()), {})
                
                taker_fee_str = first_pair_taker.get('fee', '0.26')
                maker_fee_str = first_pair_maker.get('fee', '0.16')
                
                try:
                    # Kraken returns fees as percentages (e.g., "0.26" for 0.26%)
                    # Convert to decimal (0.0026 for 0.26%)
                    self.taker_fee_pct = float(taker_fee_str) / 100.0
                    self.maker_fee_pct = float(maker_fee_str) / 100.0
                    
                    logger.info(
                        f"[FEE-MODEL] Fees updated: "
                        f"maker={self.maker_fee_pct*100:.4f}%, "
                        f"taker={self.taker_fee_pct*100:.4f}%"
                    )
                    
                    # Determine tier based on volume
                    if self.volume_30d >= 10_000_000:
                        self.fee_tier = "vip"
                    elif self.volume_30d >= 1_000_000:
                        self.fee_tier = "high_volume"
                    elif self.volume_30d >= 100_000:
                        self.fee_tier = "intermediate"
                    else:
                        self.fee_tier = "standard"
                    
                    logger.info(f"[FEE-MODEL] Fee tier: {self.fee_tier}")
                    
                except (ValueError, TypeError) as e:
                    logger.error(f"[FEE-MODEL] Fee parsing error: {e}")
                    return False
            else:
                logger.warning("[FEE-MODEL] No fee data in response, using defaults")
                return False
            
            self.last_fetch_time = time.time()
            return True
            
        except Exception as e:
            logger.error(f"[FEE-MODEL] Failed to fetch fees: {e}")
            return False
    
    def get_taker_fee(self, symbol: Optional[str] = None) -> float:
        """
        Get taker fee for a symbol (market orders).
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD") - currently ignored, may support pair-specific fees later
        
        Returns:
            Taker fee as decimal (e.g., 0.0026 for 0.26%)
        """
        # Refresh if needed
        self.fetch_fees()
        
        # Check for pair-specific override (future enhancement)
        if symbol and symbol in self.pair_fees:
            _, taker = self.pair_fees[symbol]
            return taker
        
        return self.taker_fee_pct
    
    def get_maker_fee(self, symbol: Optional[str] = None) -> float:
        """
        Get maker fee for a symbol (limit orders that provide liquidity).
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD") - currently ignored, may support pair-specific fees later
        
        Returns:
            Maker fee as decimal (e.g., 0.0016 for 0.16%)
        """
        # Refresh if needed
        self.fetch_fees()
        
        # Check for pair-specific override (future enhancement)
        if symbol and symbol in self.pair_fees:
            maker, _ = self.pair_fees[symbol]
            return maker
        
        return self.maker_fee_pct
    
    def get_fee_info(self) -> Dict[str, any]:
        """
        Get complete fee information.
        
        Returns:
            Dictionary with all fee data
        """
        self.fetch_fees()
        
        return {
            "maker_fee_pct": self.maker_fee_pct,
            "taker_fee_pct": self.taker_fee_pct,
            "maker_fee_bps": self.maker_fee_pct * 10000,  # Basis points
            "taker_fee_bps": self.taker_fee_pct * 10000,
            "fee_tier": self.fee_tier,
            "volume_30d_usd": self.volume_30d,
            "last_updated": self.last_fetch_time,
            "cache_ttl_seconds": self.cache_ttl
        }
    
    def calculate_fee_cost(
        self,
        notional_usd: float,
        is_maker: bool = False
    ) -> float:
        """
        Calculate fee cost for a trade.
        
        Args:
            notional_usd: Trade size in USD
            is_maker: True for limit orders (maker), False for market orders (taker)
        
        Returns:
            Fee amount in USD
        """
        fee_rate = self.get_maker_fee() if is_maker else self.get_taker_fee()
        return notional_usd * fee_rate
    
    def minimum_profitable_move_pct(
        self,
        round_trip: bool = True,
        safety_margin_pct: float = 0.1
    ) -> float:
        """
        Calculate minimum price move needed to cover fees.
        
        Args:
            round_trip: If True, calculate for entry + exit (default)
            safety_margin_pct: Additional safety margin (default 0.1%)
        
        Returns:
            Minimum move as percentage (e.g., 0.62 for 0.62%)
        """
        # For market-only trading, we pay taker fees both ways
        taker_fee = self.get_taker_fee()
        
        if round_trip:
            # Entry + Exit fees
            total_fees = (taker_fee * 2) * 100  # Convert to percentage
        else:
            # Single direction
            total_fees = taker_fee * 100
        
        # Add safety margin
        min_move = total_fees + safety_margin_pct
        
        return min_move


# Singleton instance
_fee_model: Optional[FeeModel] = None


def get_fee_model(cache_ttl_seconds: int = 3600) -> FeeModel:
    """
    Get or create singleton FeeModel instance.
    
    Args:
        cache_ttl_seconds: Cache TTL (only used on first init)
    
    Returns:
        FeeModel instance
    """
    global _fee_model
    if _fee_model is None:
        _fee_model = FeeModel(cache_ttl_seconds=cache_ttl_seconds)
        logger.info("[FEE-MODEL] Singleton instance created")
    return _fee_model


# Convenience functions
def get_taker_fee(symbol: Optional[str] = None) -> float:
    """Get current taker fee (market orders)"""
    return get_fee_model().get_taker_fee(symbol)


def get_maker_fee(symbol: Optional[str] = None) -> float:
    """Get current maker fee (limit orders)"""
    return get_fee_model().get_maker_fee(symbol)


def get_minimum_edge_pct(safety_margin: float = 0.1) -> float:
    """
    Get minimum edge needed to cover fees profitably.
    
    Args:
        safety_margin: Additional safety buffer in percent (default 0.1%)
    
    Returns:
        Minimum required edge as percentage (0.0 if BYPASS_FEE_BLOCK=1)
    """
    import os
    
    # CRITICAL: Check bypass flag first
    if os.getenv('BYPASS_FEE_BLOCK', '0') == '1':
        logger.info("[FEE-MODEL] 🔓 BYPASS_FEE_BLOCK=1 detected - returning 0.0% min edge (all trades allowed)")
        return 0.0
    
    try:
        return get_fee_model().minimum_profitable_move_pct(
            round_trip=True,
            safety_margin_pct=safety_margin
        )
    except Exception as e:
        logger.warning(f"[FEE-MODEL] Failed to calculate min edge: {e} - using default 0.6%")
        return 0.6  # Conservative default (covers 0.26% * 2 + buffer)


# Safe wrappers that never crash (for autopilot imports)
def get_taker_fee_pct(symbol: Optional[str] = None) -> float:
    """
    Get taker fee percentage (SAFE - never crashes).
    
    Returns fee as decimal (e.g., 0.0026 for 0.26%)
    """
    try:
        return get_taker_fee(symbol)
    except Exception:
        return 0.0026  # Default Kraken taker fee (0.26%)


def get_maker_fee_pct(symbol: Optional[str] = None) -> float:
    """
    Get maker fee percentage (SAFE - never crashes).
    
    Returns fee as decimal (e.g., 0.0016 for 0.16%)
    """
    try:
        return get_maker_fee(symbol)
    except Exception:
        return 0.0016  # Default Kraken maker fee (0.16%)


def estimate_rollover_fee_per_day(position_usd: float, leverage: float = 1.0) -> float:
    """
    Estimate daily rollover fee for a margin position.
    
    Kraken margin rollover fees (approximate):
    - USD pairs: ~0.01% per day on borrowed amount
    - Other pairs: ~0.02% per day on borrowed amount
    
    Args:
        position_usd: Position size in USD
        leverage: Leverage multiplier (1.0-2.0)
        
    Returns:
        Estimated daily rollover fee in USD
    """
    if leverage <= 1.0:
        return 0.0  # No borrowed funds, no rollover fee
    
    borrowed_amount = position_usd * (leverage - 1.0) / leverage
    
    rollover_rate_daily = 0.02 / 100  # 0.02% per day (conservative estimate)
    
    return borrowed_amount * rollover_rate_daily


def estimate_short_total_fees(
    position_usd: float,
    leverage: float = 1.0,
    holding_days: int = 1,
    entry_fee_pct: Optional[float] = None,
    exit_fee_pct: Optional[float] = None
) -> float:
    """
    Estimate total cost for a short trade including trading fees and rollover.
    
    Args:
        position_usd: Position size in USD
        leverage: Leverage multiplier (1.0-2.0)
        holding_days: Expected holding period in days (default 1)
        entry_fee_pct: Entry fee override (uses taker fee if None)
        exit_fee_pct: Exit fee override (uses taker fee if None)
        
    Returns:
        Total estimated fees in USD
    """
    # Trading fees (entry + exit)
    entry_fee = entry_fee_pct or get_taker_fee_pct()
    exit_fee = exit_fee_pct or get_taker_fee_pct()
    
    trading_fees = position_usd * (entry_fee + exit_fee)
    
    # Rollover fees for holding period
    daily_rollover = estimate_rollover_fee_per_day(position_usd, leverage)
    total_rollover = daily_rollover * holding_days
    
    total_fees = trading_fees + total_rollover
    
    logger.debug(
        f"[FEE-MODEL] Short fee estimate: "
        f"position=${position_usd:.2f}, leverage={leverage}x, days={holding_days}, "
        f"trading_fees=${trading_fees:.4f}, rollover=${total_rollover:.4f}, "
        f"total=${total_fees:.4f}"
    )
    
    return total_fees
