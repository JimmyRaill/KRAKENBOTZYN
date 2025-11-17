"""
rate_limiter.py - Order rate limiting for high-frequency trading safety

Prevents Kraken API rate limit violations and ensures safe order execution pacing.
Critical for market-only mode where orders fire more frequently without bracket delays.
"""

import time
from typing import Optional, List, Tuple
from collections import deque
from loguru import logger
import os


class RateLimiter:
    """
    Rate limiter for order execution with rolling window tracking.
    
    Enforces:
    - Maximum orders per minute (rolling 60s window)
    - Minimum delay between consecutive orders
    - Per-symbol rate limits (optional)
    """
    
    def __init__(
        self,
        max_orders_per_minute: int = 15,  # Conservative default (Kraken allows ~15/sec, we're much lower)
        min_delay_ms: int = 250,  # 250ms between orders (safe for most exchanges)
        window_seconds: int = 60
    ):
        """
        Initialize rate limiter.
        
        Args:
            max_orders_per_minute: Max orders allowed in rolling window
            min_delay_ms: Minimum milliseconds between consecutive orders
            window_seconds: Size of rolling window in seconds
        """
        self.max_orders_per_minute = max_orders_per_minute
        self.min_delay_seconds = min_delay_ms / 1000.0
        self.window_seconds = window_seconds
        
        # Rolling window of order timestamps
        self.order_timestamps: deque = deque()
        
        # Last order timestamp for min delay enforcement
        self.last_order_time: Optional[float] = None
        
        # Statistics
        self.total_orders = 0
        self.total_blocks = 0
        self.total_delays = 0
        
        logger.info(
            f"[RATE-LIMITER] Initialized: max_orders/min={max_orders_per_minute}, "
            f"min_delay={min_delay_ms}ms, window={window_seconds}s"
        )
    
    def _clean_old_timestamps(self, current_time: float) -> None:
        """Remove timestamps outside rolling window"""
        cutoff_time = current_time - self.window_seconds
        
        while self.order_timestamps and self.order_timestamps[0] < cutoff_time:
            self.order_timestamps.popleft()
    
    def can_execute(self, symbol: Optional[str] = None) -> Tuple[bool, str]:
        """
        Check if an order can be executed now.
        
        Args:
            symbol: Trading pair (currently unused, for future per-symbol limits)
        
        Returns:
            (can_execute: bool, reason: str)
        """
        current_time = time.time()
        
        # Clean old timestamps
        self._clean_old_timestamps(current_time)
        
        # Check 1: Minimum delay between orders
        if self.last_order_time is not None:
            time_since_last = current_time - self.last_order_time
            if time_since_last < self.min_delay_seconds:
                remaining_ms = int((self.min_delay_seconds - time_since_last) * 1000)
                return (
                    False,
                    f"Min delay not met: {remaining_ms}ms remaining (min: {int(self.min_delay_seconds*1000)}ms)"
                )
        
        # Check 2: Rolling window limit
        if len(self.order_timestamps) >= self.max_orders_per_minute:
            oldest_timestamp = self.order_timestamps[0]
            time_until_oldest_expires = (oldest_timestamp + self.window_seconds) - current_time
            return (
                False,
                f"Rate limit: {len(self.order_timestamps)}/{self.max_orders_per_minute} orders in window, "
                f"wait {int(time_until_oldest_expires)}s"
            )
        
        # All checks passed
        return (True, "OK")
    
    def record_order(self, symbol: Optional[str] = None) -> None:
        """
        Record that an order was executed.
        
        Args:
            symbol: Trading pair (for statistics/future per-symbol limits)
        """
        current_time = time.time()
        
        self.order_timestamps.append(current_time)
        self.last_order_time = current_time
        self.total_orders += 1
        
        logger.debug(
            f"[RATE-LIMITER] Order recorded: {len(self.order_timestamps)}/{self.max_orders_per_minute} "
            f"in window"
        )
    
    def wait_if_needed(self, symbol: Optional[str] = None, max_wait_seconds: float = 5.0) -> bool:
        """
        Block execution until rate limit allows, up to max_wait_seconds.
        
        Args:
            symbol: Trading pair
            max_wait_seconds: Maximum time to wait (default 5s)
        
        Returns:
            True if wait succeeded and can execute, False if timeout
        """
        start_time = time.time()
        
        while True:
            can_exec, reason = self.can_execute(symbol)
            
            if can_exec:
                return True
            
            elapsed = time.time() - start_time
            if elapsed >= max_wait_seconds:
                logger.warning(
                    f"[RATE-LIMITER] Timeout after {elapsed:.1f}s waiting for rate limit: {reason}"
                )
                self.total_blocks += 1
                return False
            
            # Calculate wait time from reason string (if possible)
            # Format: "Min delay not met: 150ms remaining (min: 250ms)"
            # or: "Rate limit: 15/15 orders in window, wait 5s"
            wait_time = 0.1  # Default 100ms
            
            if "ms remaining" in reason:
                try:
                    remaining_ms = int(reason.split("ms remaining")[0].split()[-1])
                    wait_time = (remaining_ms + 10) / 1000.0  # Add 10ms buffer
                except (ValueError, IndexError):
                    pass
            elif "wait" in reason and "s" in reason:
                try:
                    wait_seconds = int(reason.split("wait")[1].split("s")[0].strip())
                    wait_time = min(wait_seconds + 0.1, 1.0)  # Cap at 1s increments
                except (ValueError, IndexError):
                    pass
            
            logger.debug(f"[RATE-LIMITER] Waiting {wait_time*1000:.0f}ms: {reason}")
            time.sleep(wait_time)
            self.total_delays += 1
    
    def get_stats(self) -> dict:
        """Get rate limiter statistics"""
        current_time = time.time()
        self._clean_old_timestamps(current_time)
        
        return {
            "total_orders": self.total_orders,
            "total_blocks": self.total_blocks,
            "total_delays": self.total_delays,
            "current_window_count": len(self.order_timestamps),
            "max_orders_per_minute": self.max_orders_per_minute,
            "min_delay_ms": int(self.min_delay_seconds * 1000),
            "window_seconds": self.window_seconds,
            "last_order_time": self.last_order_time
        }
    
    def reset(self) -> None:
        """Reset rate limiter (for testing or emergency)"""
        self.order_timestamps.clear()
        self.last_order_time = None
        logger.info("[RATE-LIMITER] Reset - all timestamps cleared")


# Singleton instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter(
    max_orders_per_minute: Optional[int] = None,
    min_delay_ms: Optional[int] = None
) -> RateLimiter:
    """
    Get or create singleton RateLimiter instance.
    
    Args:
        max_orders_per_minute: Override default (only on first init)
        min_delay_ms: Override default (only on first init)
    
    Returns:
        RateLimiter instance
    """
    global _rate_limiter
    
    if _rate_limiter is None:
        # Read from env vars if provided
        max_orders = max_orders_per_minute or int(os.getenv("MAX_ORDERS_PER_MINUTE", "15"))
        min_delay = min_delay_ms or int(os.getenv("MIN_ORDER_DELAY_MS", "250"))
        
        _rate_limiter = RateLimiter(
            max_orders_per_minute=max_orders,
            min_delay_ms=min_delay
        )
        logger.info("[RATE-LIMITER] Singleton instance created")
    
    return _rate_limiter


# Convenience functions
def can_execute_order(symbol: Optional[str] = None) -> Tuple[bool, str]:
    """Check if an order can be executed now"""
    return get_rate_limiter().can_execute(symbol)


def record_order_executed(symbol: Optional[str] = None) -> None:
    """Record that an order was executed"""
    get_rate_limiter().record_order(symbol)


def wait_for_rate_limit(symbol: Optional[str] = None, max_wait_seconds: float = 5.0) -> bool:
    """Wait until rate limit allows execution"""
    return get_rate_limiter().wait_if_needed(symbol, max_wait_seconds)


def get_rate_limit_stats() -> dict:
    """Get rate limiter statistics"""
    return get_rate_limiter().get_stats()
