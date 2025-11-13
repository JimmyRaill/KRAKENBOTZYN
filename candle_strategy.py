"""
candle_strategy.py - Professional indicator calculations and filters

This module provides comprehensive technical analysis for a disciplined,
regime-aware day-trading system. All calculations use CLOSED candles only.

Indicators:
- SMA (Simple Moving Average)
- RSI (Relative Strength Index)
- ATR (Average True Range)

Filters:
- Trend strength detection
- Chop/sideways market detection
- Volume filters
- Volatility filters
- ATR spike detection

Key principles:
- All functions are pure (no side effects)
- Only work with historical OHLC data
- No mid-candle evaluations
- Designed for 5-minute candle strategy with multi-signal confirmation
"""

from typing import List, Optional, Tuple, Dict, Any
import statistics


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


def calculate_adx(
    ohlcv: List[List[float]],
    period: int = 14
) -> Optional[float]:
    """
    Calculate Average Directional Index (ADX) - trend strength indicator.
    
    Args:
        ohlcv: List of OHLC candles [[timestamp, open, high, low, close, volume], ...]
        period: Lookback period (default: 14)
    
    Returns:
        ADX value (0-100), or None if insufficient data
        
    Interpretation:
        - ADX > 25: Strong trend (use trend-following strategies)
        - ADX < 20: Weak/no trend (use mean-reversion or stay out)
        - ADX < 10: Dead market (NO_TRADE)
    
    Method:
        Uses Wilder's smoothing for TR, +DM, -DM
        Calculates +DI, -DI, DX, then smoothed ADX
    """
    if not ohlcv or len(ohlcv) < period * 2:
        return None
    
    # Step 1: Calculate directional movements and true ranges
    dm_plus_list = []
    dm_minus_list = []
    tr_list = []
    
    for i in range(1, len(ohlcv)):
        curr = ohlcv[i]
        prev = ohlcv[i-1]
        
        high_curr = curr[2]
        low_curr = curr[3]
        high_prev = prev[2]
        low_prev = prev[3]
        close_prev = prev[4]
        
        # Directional movements
        dm_plus = high_curr - high_prev
        dm_minus = low_prev - low_curr
        
        # Only count if movement is positive and dominant
        if dm_plus > dm_minus and dm_plus > 0:
            dm_plus_list.append(dm_plus)
            dm_minus_list.append(0.0)
        elif dm_minus > dm_plus and dm_minus > 0:
            dm_plus_list.append(0.0)
            dm_minus_list.append(dm_minus)
        else:
            dm_plus_list.append(0.0)
            dm_minus_list.append(0.0)
        
        # True range
        tr = max(
            high_curr - low_curr,
            abs(high_curr - close_prev),
            abs(low_curr - close_prev)
        )
        tr_list.append(tr)
    
    if len(tr_list) < period:
        return None
    
    # Step 2: Wilder's smoothing
    def wilder_smooth(values: List[float], period: int) -> List[float]:
        if len(values) < period:
            return []
        smoothed = []
        current_smooth = sum(values[:period]) / period
        smoothed.append(current_smooth)
        for i in range(period, len(values)):
            current_smooth = current_smooth + (values[i] - current_smooth) / period
            smoothed.append(current_smooth)
        return smoothed
    
    smoothed_tr = wilder_smooth(tr_list, period)
    smoothed_dm_plus = wilder_smooth(dm_plus_list, period)
    smoothed_dm_minus = wilder_smooth(dm_minus_list, period)
    
    if not smoothed_tr or len(smoothed_tr) < period:
        return None
    
    # Step 3: Calculate +DI and -DI
    di_plus_list = []
    di_minus_list = []
    for i in range(len(smoothed_tr)):
        tr = smoothed_tr[i]
        if tr == 0:
            di_plus_list.append(0.0)
            di_minus_list.append(0.0)
        else:
            di_plus = (smoothed_dm_plus[i] / tr) * 100
            di_minus = (smoothed_dm_minus[i] / tr) * 100
            di_plus_list.append(di_plus)
            di_minus_list.append(di_minus)
    
    # Step 4: Calculate DX
    dx_list = []
    for i in range(len(di_plus_list)):
        di_sum = di_plus_list[i] + di_minus_list[i]
        if di_sum == 0:
            dx_list.append(0.0)
        else:
            di_diff = abs(di_plus_list[i] - di_minus_list[i])
            dx = (di_diff / di_sum) * 100
            dx_list.append(dx)
    
    if len(dx_list) < period:
        return None
    
    # Step 5: Smooth DX to get ADX
    adx_list = wilder_smooth(dx_list, period)
    if not adx_list:
        return None
    return adx_list[-1]


def calculate_bollinger_bands(
    closes: List[float],
    period: int = 20,
    std_dev: float = 2.0
) -> Optional[Tuple[float, float, float]]:
    """
    Calculate Bollinger Bands - volatility and range indicator.
    
    Args:
        closes: List of closing prices (most recent last)
        period: SMA period (default: 20)
        std_dev: Standard deviation multiplier (default: 2.0)
    
    Returns:
        Tuple of (middle, upper, lower) or None if insufficient data
        
    Interpretation:
        - Tight bands (width < 2% of price): Range market, low volatility
        - Wide bands: High volatility
        - Price at upper band: Potentially overbought
        - Price at lower band: Potentially oversold
    """
    if not closes or len(closes) < period:
        return None
    
    # Calculate middle band (SMA)
    middle = calculate_sma(closes, period)
    if middle is None:
        return None
    
    # Calculate standard deviation
    recent_closes = closes[-period:]
    try:
        std = statistics.pstdev(recent_closes)
    except Exception:
        return None
    
    # Calculate bands
    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)
    
    return (middle, upper, lower)


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


# =============================================================================
# ADVANCED INDICATORS - Professional Trading Filters
# =============================================================================

def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index from closed candles.
    
    Args:
        closes: List of closing prices (most recent last)
        period: RSI period (default: 14)
    
    Returns:
        RSI value (0-100), or None if insufficient data
    
    Formula:
        RSI = 100 - (100 / (1 + RS))
        where RS = Average Gain / Average Loss over period
    
    Example:
        rsi = calculate_rsi(closes, period=14)
        if rsi and rsi > 70:
            print("Overbought")
    """
    if not closes or len(closes) < period + 1:
        return None
    
    # Calculate price changes
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    
    # Separate gains and losses
    gains = [max(change, 0) for change in changes]
    losses = [abs(min(change, 0)) for change in changes]
    
    # Calculate initial average gain and loss
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    # Handle division by zero
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    
    # Calculate RS and RSI
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    
    return rsi


def extract_volumes(ohlcv: List[List[float]]) -> List[float]:
    """
    Extract volume data from OHLC candles.
    
    Args:
        ohlcv: List of candles [[timestamp, open, high, low, close, volume], ...]
    
    Returns:
        List of volumes
    """
    return [candle[5] for candle in ohlcv if candle and len(candle) >= 6]


def calculate_volume_percentile(
    current_volume: float,
    recent_volumes: List[float]
) -> Optional[float]:
    """
    Calculate what percentile the current volume falls into.
    
    Args:
        current_volume: Volume of latest candle
        recent_volumes: List of recent volumes for comparison
    
    Returns:
        Percentile (0-100), or None if insufficient data
    
    Example:
        percentile = calculate_volume_percentile(1000, [500, 600, 800, 1200])
        # Returns 75.0 (current volume is in 75th percentile)
    """
    if not recent_volumes or len(recent_volumes) < 10:
        return None
    
    # Count how many recent volumes are below current
    below_count = sum(1 for v in recent_volumes if v < current_volume)
    
    # Calculate percentile
    percentile = (below_count / len(recent_volumes)) * 100
    
    return percentile


def is_volume_acceptable(
    ohlcv: List[List[float]],
    min_percentile: float = 30.0,
    lookback: int = 20
) -> Tuple[bool, str]:
    """
    Check if current volume meets minimum threshold.
    
    Args:
        ohlcv: List of candles
        min_percentile: Minimum volume percentile (default: 30th)
        lookback: Candles to use for percentile calculation
    
    Returns:
        (is_acceptable, reason) tuple
    
    Example:
        ok, reason = is_volume_acceptable(ohlcv, min_percentile=30)
        if not ok:
            print(f"Low volume: {reason}")
    """
    if not ohlcv or len(ohlcv) < lookback + 1:
        return False, f"Insufficient data for volume analysis ({len(ohlcv)} < {lookback+1})"
    
    volumes = extract_volumes(ohlcv)
    if len(volumes) < lookback + 1:
        return False, "Missing volume data"
    
    current_volume = volumes[-1]
    recent_volumes = volumes[-(lookback+1):-1]  # Exclude current
    
    percentile = calculate_volume_percentile(current_volume, recent_volumes)
    
    if percentile is None:
        return False, "Cannot calculate volume percentile"
    
    if percentile < min_percentile:
        return False, f"Volume too low ({percentile:.1f}th percentile < {min_percentile})"
    
    return True, f"Volume acceptable ({percentile:.1f}th percentile)"


def calculate_sma_slope(closes: List[float], period: int = 20, lookback: int = 10) -> Optional[float]:
    """
    Calculate the slope of SMA over recent candles.
    
    Args:
        closes: List of closing prices
        period: SMA period
        lookback: Number of candles to measure slope
    
    Returns:
        Slope value (positive = upward, negative = downward, ~0 = flat)
        or None if insufficient data
    
    Logic:
        slope = (current_SMA - SMA_N_candles_ago) / N
    """
    if not closes or len(closes) < period + lookback:
        return None
    
    # Calculate current SMA
    current_sma = calculate_sma(closes, period)
    if current_sma is None:
        return None
    
    # Calculate SMA N candles ago
    past_closes = closes[:-lookback]
    past_sma = calculate_sma(past_closes, period)
    if past_sma is None:
        return None
    
    # Calculate slope
    slope = (current_sma - past_sma) / lookback
    
    return slope


def is_choppy_market(
    ohlcv: List[List[float]],
    sma_period: int = 20,
    slope_threshold: float = 0.0005,
    lookback: int = 10,
    atr_range_multiplier: float = 1.5
) -> Tuple[bool, str]:
    """
    Detect if market is choppy/sideways (not trending).
    
    Args:
        ohlcv: List of candles
        sma_period: SMA period for slope calculation
        slope_threshold: Max slope for "flat" SMA (absolute value)
        lookback: Candles to measure slope
        atr_range_multiplier: Price range threshold vs ATR
    
    Returns:
        (is_choppy, reason) tuple
    
    Chop Detection Logic:
        1. SMA slope is nearly flat (< threshold)
        2. Price is ranging (high-low range < ATR × multiplier)
    
    Example:
        choppy, reason = is_choppy_market(ohlcv)
        if choppy:
            print(f"Skip trade: {reason}")
    """
    if not ohlcv or len(ohlcv) < sma_period + lookback:
        return False, "Insufficient data for chop detection"
    
    closes = extract_closes(ohlcv)
    
    # Check SMA slope
    slope = calculate_sma_slope(closes, sma_period, lookback)
    if slope is None:
        return False, "Cannot calculate SMA slope"
    
    # Check if slope is flat
    if abs(slope) > slope_threshold:
        return False, f"SMA trending (slope={slope:.4f})"
    
    # Check price range vs ATR
    atr = calculate_atr(ohlcv[-lookback:], period=min(14, lookback))
    if atr is None or atr == 0:
        return False, "Cannot calculate ATR for range check"
    
    # Get high-low range over lookback period
    recent_candles = ohlcv[-lookback:]
    highs = [candle[2] for candle in recent_candles]
    lows = [candle[3] for candle in recent_candles]
    price_range = max(highs) - min(lows)
    
    # If range is small compared to ATR, market is choppy
    if price_range < (atr * atr_range_multiplier):
        return True, f"Choppy market (range={price_range:.2f}, ATR={atr:.2f}, flat SMA slope={slope:.4f})"
    
    return False, f"Market has movement (range={price_range:.2f} > {atr*atr_range_multiplier:.2f})"


def is_volatility_acceptable(
    current_close: float,
    atr: float,
    min_atr_pct: float = 0.001
) -> Tuple[bool, str]:
    """
    Check if volatility (ATR) meets minimum threshold.
    
    Args:
        current_close: Current closing price
        atr: Average True Range
        min_atr_pct: Minimum ATR as percentage of price (default: 0.1%)
    
    Returns:
        (is_acceptable, reason) tuple
    
    Example:
        ok, reason = is_volatility_acceptable(50000, 250, min_atr_pct=0.001)
        # ATR = 250, price = 50000, ATR/price = 0.005 = 0.5% > 0.1% ✓
    """
    if atr is None or atr <= 0:
        return False, "ATR is zero or unavailable"
    
    atr_pct = atr / current_close
    
    if atr_pct < min_atr_pct:
        return False, f"Volatility too low (ATR={atr_pct*100:.3f}% < {min_atr_pct*100:.3f}%)"
    
    return True, f"Volatility acceptable (ATR={atr_pct*100:.3f}%)"


def detect_atr_spike(
    current_atr: float,
    recent_atrs: List[float],
    max_multiplier: float = 3.0
) -> Tuple[bool, str]:
    """
    Detect if ATR has spiked abnormally (indicating market shock).
    
    Args:
        current_atr: Current ATR value
        recent_atrs: List of recent ATR values for comparison
        max_multiplier: Max allowed spike (current / average)
    
    Returns:
        (is_spike, reason) tuple
    
    Example:
        is_spike, msg = detect_atr_spike(500, [150, 160, 170], max_multiplier=3.0)
        # 500 / 160 = 3.125 > 3.0 → spike detected
    """
    if not recent_atrs or len(recent_atrs) < 5:
        return False, "Insufficient ATR history"
    
    avg_atr = statistics.mean(recent_atrs)
    
    if avg_atr == 0:
        return False, "Average ATR is zero"
    
    spike_ratio = current_atr / avg_atr
    
    if spike_ratio > max_multiplier:
        return True, f"ATR spike detected ({spike_ratio:.2f}x average, limit={max_multiplier}x)"
    
    return False, f"ATR normal ({spike_ratio:.2f}x average)"


def check_trend_strength(
    current_close: float,
    sma_fast: Optional[float],
    sma_slow: Optional[float],
    required_direction: str  # 'long' or 'short'
) -> Tuple[bool, str]:
    """
    Check if trend is strong enough in the required direction.
    
    Args:
        current_close: Current closing price
        sma_fast: Fast SMA (e.g., SMA20)
        sma_slow: Slow SMA (e.g., SMA50)
        required_direction: 'long' or 'short'
    
    Returns:
        (trend_confirmed, reason) tuple
    
    Logic:
        For LONG: price > SMA_fast AND SMA_fast > SMA_slow
        For SHORT: price < SMA_fast AND SMA_fast < SMA_slow
    """
    if sma_fast is None or sma_slow is None:
        return False, "Missing SMA data for trend check"
    
    if required_direction == 'long':
        if current_close <= sma_fast:
            return False, f"Price ({current_close:.2f}) not above SMA{len([1]*20)} ({sma_fast:.2f})"
        if sma_fast <= sma_slow:
            return False, f"SMA trend down (fast={sma_fast:.2f} <= slow={sma_slow:.2f})"
        return True, f"Strong uptrend (price={current_close:.2f} > SMA_fast={sma_fast:.2f} > SMA_slow={sma_slow:.2f})"
    
    elif required_direction == 'short':
        if current_close >= sma_fast:
            return False, f"Price ({current_close:.2f}) not below SMA{len([1]*20)} ({sma_fast:.2f})"
        if sma_fast >= sma_slow:
            return False, f"SMA trend up (fast={sma_fast:.2f} >= slow={sma_slow:.2f})"
        return True, f"Strong downtrend (price={current_close:.2f} < SMA_fast={sma_fast:.2f} < SMA_slow={sma_slow:.2f})"
    
    return False, f"Unknown direction: {required_direction}"
