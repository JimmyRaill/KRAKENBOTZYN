"""
multi_timeframe_context.py - Higher Timeframe Trend Context

Fetches and analyzes 15m and 1h candles to provide trend context for
the 5m primary timeframe strategy. Helps filter trades that align with
the dominant higher timeframe trend.

Key features:
- Fetches 15m and 1h OHLC from Kraken via ExchangeManager
- Calculates SMA20/50, ATR, and trend direction
- Provides HTF alignment signals for regime detection
- Caches results to minimize API calls

Design:
- Singleton pattern via get_mtf_context()
- 60-second cache for HTF data (aligns with autopilot cycle)
- Returns HTFContext dataclass with all indicators
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass
from exchange_manager import ExchangeManager
import candle_strategy as cs
from loguru import logger
import time


@dataclass
class HTFContext:
    """Higher timeframe context for trend confirmation"""
    # 15-minute indicators
    sma20_15m: Optional[float] = None
    sma50_15m: Optional[float] = None
    atr_15m: Optional[float] = None
    trend_15m: Optional[str] = None  # 'up', 'down', 'neutral'
    
    # 1-hour indicators
    sma20_1h: Optional[float] = None
    sma50_1h: Optional[float] = None
    atr_1h: Optional[float] = None
    trend_1h: Optional[str] = None  # 'up', 'down', 'neutral'
    
    # Alignment signals
    htf_aligned: bool = False  # True if 15m and 1h trends agree
    dominant_trend: Optional[str] = None  # 'up', 'down', or None
    
    # Metadata
    timestamp: float = 0.0
    symbol: str = ""


class MultiTimeframeContext:
    """
    Fetches and analyzes higher timeframe data for trend context.
    
    Usage:
        mtf = get_mtf_context()
        context = mtf.get_context("BTC/USD")
        
        if context.htf_aligned and context.dominant_trend == 'up':
            # Only take long trades when HTF is bullish
            pass
    """
    
    def __init__(self, cache_ttl: int = 60):
        """
        Initialize multi-timeframe context analyzer.
        
        Args:
            cache_ttl: Cache TTL in seconds (default: 60)
        """
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, HTFContext] = {}
        self.exchange = ExchangeManager()
        logger.info(f"MultiTimeframeContext initialized (cache_ttl={cache_ttl}s)")
    
    def get_context(self, symbol: str, force_refresh: bool = False) -> HTFContext:
        """
        Get higher timeframe context for a symbol.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            force_refresh: Force API fetch even if cached
        
        Returns:
            HTFContext with all indicators and trend signals
        """
        now = time.time()
        
        # Check cache
        if not force_refresh and symbol in self._cache:
            cached = self._cache[symbol]
            age = now - cached.timestamp
            if age < self.cache_ttl:
                logger.debug(f"HTF cache hit for {symbol} (age={age:.1f}s)")
                return cached
        
        # Fetch fresh data
        logger.info(f"Fetching HTF data for {symbol}")
        context = self._fetch_and_analyze(symbol)
        context.timestamp = now
        
        # Cache result
        self._cache[symbol] = context
        
        return context
    
    def _fetch_and_analyze(self, symbol: str) -> HTFContext:
        """
        Fetch and analyze higher timeframe data.
        
        Args:
            symbol: Trading pair
        
        Returns:
            HTFContext with calculated indicators
        """
        context = HTFContext(symbol=symbol)
        
        try:
            # Fetch 15m candles (last 100 bars = ~25 hours)
            ohlcv_15m = self.exchange.fetch_ohlc(symbol, timeframe='15m', limit=100)
            if ohlcv_15m and len(ohlcv_15m) >= 50:
                closes_15m = cs.extract_closes(ohlcv_15m)
                
                context.sma20_15m = cs.calculate_sma(closes_15m, period=20)
                context.sma50_15m = cs.calculate_sma(closes_15m, period=50)
                context.atr_15m = cs.calculate_atr(ohlcv_15m, period=14)
                context.trend_15m = self._detect_trend(
                    closes_15m[-1],
                    context.sma20_15m,
                    context.sma50_15m
                )
            else:
                logger.warning(f"Insufficient 15m data for {symbol}: {len(ohlcv_15m) if ohlcv_15m else 0} candles")
        
        except Exception as e:
            logger.error(f"Failed to fetch 15m data for {symbol}: {e}")
        
        try:
            # Fetch 1h candles (last 100 bars = ~4 days)
            ohlcv_1h = self.exchange.fetch_ohlc(symbol, timeframe='1h', limit=100)
            if ohlcv_1h and len(ohlcv_1h) >= 50:
                closes_1h = cs.extract_closes(ohlcv_1h)
                
                context.sma20_1h = cs.calculate_sma(closes_1h, period=20)
                context.sma50_1h = cs.calculate_sma(closes_1h, period=50)
                context.atr_1h = cs.calculate_atr(ohlcv_1h, period=14)
                context.trend_1h = self._detect_trend(
                    closes_1h[-1],
                    context.sma20_1h,
                    context.sma50_1h
                )
            else:
                logger.warning(f"Insufficient 1h data for {symbol}: {len(ohlcv_1h) if ohlcv_1h else 0} candles")
        
        except Exception as e:
            logger.error(f"Failed to fetch 1h data for {symbol}: {e}")
        
        # Determine alignment
        context.htf_aligned = (
            context.trend_15m is not None and
            context.trend_1h is not None and
            context.trend_15m == context.trend_1h and
            context.trend_15m != 'neutral'
        )
        
        if context.htf_aligned:
            context.dominant_trend = context.trend_15m
        else:
            context.dominant_trend = None
        
        logger.info(
            f"HTF context for {symbol}: "
            f"15m={context.trend_15m}, 1h={context.trend_1h}, "
            f"aligned={context.htf_aligned}, dominant={context.dominant_trend}"
        )
        
        return context
    
    @staticmethod
    def _detect_trend(
        price: float,
        sma20: Optional[float],
        sma50: Optional[float]
    ) -> str:
        """
        Detect trend direction from SMA alignment.
        
        Args:
            price: Current price
            sma20: 20-period SMA
            sma50: 50-period SMA
        
        Returns:
            'up', 'down', or 'neutral'
        """
        if sma20 is None or sma50 is None:
            return 'neutral'
        
        # Strong uptrend: price > SMA20 > SMA50
        if price > sma20 and sma20 > sma50:
            return 'up'
        
        # Strong downtrend: price < SMA20 < SMA50
        if price < sma20 and sma20 < sma50:
            return 'down'
        
        # Mixed or choppy
        return 'neutral'


# Singleton instance
_mtf_context: Optional[MultiTimeframeContext] = None


def get_mtf_context() -> MultiTimeframeContext:
    """
    Get singleton MultiTimeframeContext instance.
    
    Returns:
        MultiTimeframeContext instance
    """
    global _mtf_context
    if _mtf_context is None:
        _mtf_context = MultiTimeframeContext()
    return _mtf_context
