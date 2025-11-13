"""
candle_strategy.py - Pure indicator calculations from closed candles

This module provides technical indicator calculations (SMA, ATR) and
signal detection logic based on CLOSED candles only. No live price checks.

Key principles:
- All functions are pure (no side effects)
- Only work with historical OHLC data
- No mid-candle evaluations
- Designed for 5-minute candle strategy
"""

from typing import List, Optional, Tuple


def calculate_sma(closes: List[float], period: int = 20) -> Optional[float]:
    """
    Calculate Simple Moving Average from closed candles.
    
    Args:
        closes: List of closing prices (most recent last)
        period: Number of periods for SMA (default: 20)
    
    Returns:
        SMA value, or None if insufficient data
    
    Example:
        closes = [100, 102, 101, 103, 105]
        sma = calculate_sma(closes, period=3)  # Returns avg of last 3: (101+103+105)/3
    """
    if not closes or len(closes) < period:
        return None
    
    # Take last N closes and calculate average
    recent_closes = closes[-period:]
    return sum(recent_closes) / period


def calculate_atr(ohlcv: List[List[float]], period: int = 14) -> Optional[float]:
    """
    Calculate Average True Range from closed candles.
    
    Args:
        ohlcv: List of OHLC candles [[timestamp, open, high, low, close, volume], ...]
        period: Number of periods for ATR (default: 14)
    
    Returns:
        ATR value, or None if insufficient data
    
    Notes:
        - Requires at least period+1 candles (need previous close for first TR)
        - True Range = max(high-low, |high-prev_close|, |low-prev_close|)
        - ATR = average of True Ranges over period
    """
    if not ohlcv or len(ohlcv) < period + 1:
        return None
    
    tr_values: List[float] = []
    prev_close: Optional[float] = None
    
    # Calculate True Range for each candle
    for candle in ohlcv[-(period + 1):]:
        timestamp, open_price, high, low, close, volume = candle
        
        if prev_close is None:
            # First candle: TR = high - low
            tr = high - low
        else:
            # Subsequent candles: TR = max(high-low, |high-prev_close|, |low-prev_close|)
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
        
        tr_values.append(float(tr))
        prev_close = float(close)
    
    # Calculate average of True Ranges
    n = min(period, len(tr_values))
    return sum(tr_values[-n:]) / max(1, n)


def detect_sma_crossover(
    current_close: float,
    current_sma: float,
    prev_close: float,
    prev_sma: float
) -> Optional[str]:
    """
    Detect SMA crossover signal from two consecutive closed candles.
    
    Args:
        current_close: Close price of latest closed candle
        current_sma: SMA value at latest closed candle
        prev_close: Close price of previous closed candle
        prev_sma: SMA value at previous closed candle
    
    Returns:
        - 'long' if price crossed ABOVE SMA (bullish)
        - 'short' if price crossed BELOW SMA (bearish)
        - None if no crossover
    
    Logic:
        - LONG: prev_close <= prev_sma AND current_close > current_sma
        - SHORT: prev_close >= prev_sma AND current_close < current_sma
    """
    # Bullish crossover: price was below/at SMA, now above
    if prev_close <= prev_sma and current_close > current_sma:
        return 'long'
    
    # Bearish crossover: price was above/at SMA, now below
    if prev_close >= prev_sma and current_close < current_sma:
        return 'short'
    
    # No crossover
    return None


def is_new_candle_closed(
    last_known_timestamp: Optional[int],
    latest_candle_timestamp: int,
    timeframe_seconds: int = 300
) -> bool:
    """
    Check if a new candle has closed since last check.
    
    Args:
        last_known_timestamp: Timestamp (ms) of last processed candle (or None for first run)
        latest_candle_timestamp: Timestamp (ms) of latest closed candle from API
        timeframe_seconds: Candle timeframe in seconds (default: 300 = 5 minutes)
    
    Returns:
        True if a new candle has closed, False otherwise
    
    Logic:
        - First run (last_known_timestamp is None): Return True to process initial candle
        - Subsequent runs: Return True if latest_candle_timestamp > last_known_timestamp
    
    Example (5-minute candles):
        last = 1700000100000  # 2023-11-15 00:01:40 UTC
        latest = 1700000400000  # 2023-11-15 00:06:40 UTC (5 min later)
        is_new = is_new_candle_closed(last, latest, 300)  # Returns True
    """
    # First run: always process the first candle
    if last_known_timestamp is None:
        return True
    
    # New candle closed if timestamp advanced
    return latest_candle_timestamp > last_known_timestamp


def extract_closes(ohlcv: List[List[float]]) -> List[float]:
    """
    Extract closing prices from OHLC data.
    
    Args:
        ohlcv: List of candles [[timestamp, open, high, low, close, volume], ...]
    
    Returns:
        List of closing prices
    """
    return [candle[4] for candle in ohlcv if candle and len(candle) >= 5]


def get_latest_candle_timestamp(ohlcv: List[List[float]]) -> Optional[int]:
    """
    Get timestamp of the latest closed candle.
    
    Args:
        ohlcv: List of candles [[timestamp, open, high, low, close, volume], ...]
    
    Returns:
        Timestamp (ms) of latest candle, or None if no data
    """
    if not ohlcv or len(ohlcv) == 0:
        return None
    
    return int(ohlcv[-1][0])


def validate_candle_data(ohlcv: List[List[float]], min_candles: int = 20) -> Tuple[bool, str]:
    """
    Validate that we have sufficient candle data for indicator calculations.
    
    Args:
        ohlcv: List of candles
        min_candles: Minimum candles required (default: 20 for SMA20)
    
    Returns:
        (is_valid, reason) tuple
    """
    if not ohlcv:
        return False, "No candle data available"
    
    if len(ohlcv) < min_candles:
        return False, f"Insufficient candles: {len(ohlcv)} < {min_candles} (need {min_candles} for SMA20)"
    
    # Check for valid data in last candle
    if len(ohlcv[-1]) < 5:
        return False, "Latest candle has invalid format"
    
    closes = extract_closes(ohlcv)
    if len(closes) < min_candles:
        return False, f"Insufficient valid closes: {len(closes)} < {min_candles}"
    
    return True, "Candle data valid"
