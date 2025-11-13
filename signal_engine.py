"""
signal_engine.py - Multi-signal trade decision engine

Orchestrates all indicators and filters to make disciplined trading decisions.
Uses sequential hard filters (all must pass) for explainability and safety.

Filter Priority Order:
1. Data validation (sufficient candles)
2. Volatility filter (min ATR threshold)
3. ATR spike detection (avoid market shocks)
4. Volume filter (require minimum activity)
5. Chop detection (skip sideways markets)
6. Trend confirmation (SMA alignment)
7. RSI filter (avoid extremes)
8. Crossover signal (final entry trigger)
"""

from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass

from candle_strategy import (
    # Basic indicators
    calculate_sma, calculate_rsi, calculate_atr,
    extract_closes, validate_candle_data,
    
    # Signal detection
    detect_sma_crossover,
    
    # Market filters
    is_volume_acceptable, is_choppy_market,
    is_volatility_acceptable, detect_atr_spike,
    check_trend_strength
)

from trading_config import TradingConfig


@dataclass
class SignalResult:
    """Result of signal evaluation"""
    action: str  # 'long', 'short', or 'hold'
    reason: str  # Explanation for decision
    passed_filters: List[str]  # Filters that passed
    failed_filter: Optional[str]  # First filter that failed (if any)
    
    # Technical details
    indicators: Dict[str, Any]  # SMA, RSI, ATR values
    
    def __str__(self) -> str:
        if self.action == 'hold':
            return f"HOLD: {self.reason}"
        return f"{self.action.upper()}: {self.reason}"


class SignalEngine:
    """
    Multi-signal trading decision engine with sequential hard filters.
    
    All filters must pass for a trade signal to be generated.
    Filters are applied in priority order for safety and efficiency.
    """
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self.ind_cfg = config.indicators
        self.filter_cfg = config.filters
    
    def evaluate_signal(
        self,
        ohlcv: List[List[float]],
        prev_ohlcv: Optional[List[List[float]]] = None
    ) -> SignalResult:
        """
        Evaluate all filters and determine if trade signal exists.
        
        Args:
            ohlcv: Current OHLC candles (latest last)
            prev_ohlcv: Previous candle batch for crossover detection (optional)
        
        Returns:
            SignalResult with action ('long', 'short', 'hold') and details
        
        Filter Sequence:
            1. Data validation
            2. Volatility check
            3. ATR spike detection
            4. Volume filter
            5. Chop detection
            6. Indicator calculation
            7. Trend strength
            8. RSI filter
            9. Crossover signal
        """
        passed_filters: List[str] = []
        indicators: Dict[str, Any] = {}
        
        # =====================================================================
        # FILTER 1: Data Validation
        # =====================================================================
        min_candles = max(self.ind_cfg.sma_slow, self.ind_cfg.atr_period) + 10
        is_valid, reason = validate_candle_data(ohlcv, min_candles=min_candles)
        
        if not is_valid:
            return SignalResult(
                action='hold',
                reason=f"[DATA] {reason}",
                passed_filters=passed_filters,
                failed_filter="data_validation",
                indicators=indicators
            )
        
        passed_filters.append("data_validation")
        
        # Extract data
        closes = extract_closes(ohlcv)
        current_close = closes[-1]
        
        # =====================================================================
        # FILTER 2: Calculate Core Indicators
        # =====================================================================
        sma_fast = calculate_sma(closes, period=self.ind_cfg.sma_fast)
        sma_slow = calculate_sma(closes, period=self.ind_cfg.sma_slow)
        rsi = calculate_rsi(closes, period=self.ind_cfg.rsi_period)
        atr = calculate_atr(ohlcv, period=self.ind_cfg.atr_period)
        
        indicators.update({
            'close': current_close,
            'sma_fast': sma_fast,
            'sma_slow': sma_slow,
            'rsi': rsi,
            'atr': atr
        })
        
        if sma_fast is None or sma_slow is None or atr is None:
            return SignalResult(
                action='hold',
                reason="[INDICATORS] Cannot calculate required indicators",
                passed_filters=passed_filters,
                failed_filter="indicator_calculation",
                indicators=indicators
            )
        
        passed_filters.append("indicator_calculation")
        
        # =====================================================================
        # FILTER 3: Volatility Check
        # =====================================================================
        vol_ok, vol_msg = is_volatility_acceptable(
            current_close,
            atr,
            min_atr_pct=self.filter_cfg.min_atr_pct
        )
        
        if not vol_ok:
            return SignalResult(
                action='hold',
                reason=f"[VOLATILITY] {vol_msg}",
                passed_filters=passed_filters,
                failed_filter="volatility",
                indicators=indicators
            )
        
        passed_filters.append("volatility")
        
        # =====================================================================
        # FILTER 4: ATR Spike Detection
        # =====================================================================
        # Calculate recent ATRs for comparison
        recent_atrs = []
        for i in range(max(5, self.ind_cfg.atr_period), len(ohlcv)):
            recent_ohlcv = ohlcv[:i]
            recent_atr = calculate_atr(recent_ohlcv, period=self.ind_cfg.atr_period)
            if recent_atr:
                recent_atrs.append(recent_atr)
        
        if len(recent_atrs) >= 5:
            is_spike, spike_msg = detect_atr_spike(
                atr,
                recent_atrs[-20:],  # Use last 20 ATR values
                max_multiplier=self.filter_cfg.max_atr_spike_multiplier
            )
            
            if is_spike:
                return SignalResult(
                    action='hold',
                    reason=f"[ATR_SPIKE] {spike_msg}",
                    passed_filters=passed_filters,
                    failed_filter="atr_spike",
                    indicators=indicators
                )
        
        passed_filters.append("atr_spike")
        
        # =====================================================================
        # FILTER 5: Volume Filter
        # =====================================================================
        volume_ok, volume_msg = is_volume_acceptable(
            ohlcv,
            min_percentile=self.filter_cfg.min_volume_percentile,
            lookback=self.filter_cfg.volume_lookback
        )
        
        if not volume_ok:
            return SignalResult(
                action='hold',
                reason=f"[VOLUME] {volume_msg}",
                passed_filters=passed_filters,
                failed_filter="volume",
                indicators=indicators
            )
        
        passed_filters.append("volume")
        
        # =====================================================================
        # FILTER 6: Chop Detection
        # =====================================================================
        if self.filter_cfg.enable_chop_filter:
            is_choppy, chop_msg = is_choppy_market(
                ohlcv,
                sma_period=self.ind_cfg.sma_fast,
                slope_threshold=self.filter_cfg.chop_sma_slope_threshold,
                lookback=self.filter_cfg.chop_lookback,
                atr_range_multiplier=self.filter_cfg.chop_atr_range_multiplier
            )
            
            if is_choppy:
                return SignalResult(
                    action='hold',
                    reason=f"[CHOP] {chop_msg}",
                    passed_filters=passed_filters,
                    failed_filter="chop",
                    indicators=indicators
                )
        
        passed_filters.append("chop")
        
        # =====================================================================
        # FILTER 7 & 8: Detect Potential Signals and Apply Filters
        # =====================================================================
        
        # Need previous candle for crossover detection
        if prev_ohlcv is None or len(prev_ohlcv) < min_candles:
            return SignalResult(
                action='hold',
                reason="[CROSSOVER] First candle - no crossover detection yet",
                passed_filters=passed_filters,
                failed_filter=None,
                indicators=indicators
            )
        
        # Calculate previous indicators
        prev_closes = extract_closes(prev_ohlcv)
        prev_close = prev_closes[-1]
        prev_sma_fast = calculate_sma(prev_closes, period=self.ind_cfg.sma_fast)
        prev_sma_slow = calculate_sma(prev_closes, period=self.ind_cfg.sma_slow)
        
        if prev_sma_fast is None:
            return SignalResult(
                action='hold',
                reason="[CROSSOVER] Cannot calculate previous SMA",
                passed_filters=passed_filters,
                failed_filter="prev_indicators",
                indicators=indicators
            )
        
        # Detect crossover
        crossover_signal = detect_sma_crossover(
            current_close, sma_fast,
            prev_close, prev_sma_fast
        )
        
        if crossover_signal is None:
            return SignalResult(
                action='hold',
                reason="[CROSSOVER] No SMA20 crossover detected",
                passed_filters=passed_filters,
                failed_filter=None,
                indicators=indicators
            )
        
        passed_filters.append("crossover_detected")
        
        # Now apply directional filters based on signal
        direction = crossover_signal  # 'long' or 'short'
        
        # FILTER 7: Trend Strength
        trend_ok, trend_msg = check_trend_strength(
            current_close,
            sma_fast,
            sma_slow,
            required_direction=direction
        )
        
        if not trend_ok:
            return SignalResult(
                action='hold',
                reason=f"[TREND] {trend_msg}",
                passed_filters=passed_filters,
                failed_filter="trend_strength",
                indicators=indicators
            )
        
        passed_filters.append("trend_strength")
        
        # FILTER 8: RSI Filter
        if rsi is not None:
            if direction == 'long' and rsi > self.ind_cfg.rsi_overbought:
                return SignalResult(
                    action='hold',
                    reason=f"[RSI] Overbought (RSI={rsi:.1f} > {self.ind_cfg.rsi_overbought})",
                    passed_filters=passed_filters,
                    failed_filter="rsi",
                    indicators=indicators
                )
            
            if direction == 'short' and rsi < self.ind_cfg.rsi_oversold:
                return SignalResult(
                    action='hold',
                    reason=f"[RSI] Oversold (RSI={rsi:.1f} < {self.ind_cfg.rsi_oversold})",
                    passed_filters=passed_filters,
                    failed_filter="rsi",
                    indicators=indicators
                )
        
        passed_filters.append("rsi")
        
        # =====================================================================
        # ALL FILTERS PASSED - GENERATE SIGNAL
        # =====================================================================
        
        signal_msg = (
            f"All filters passed: {direction.upper()} on SMA{self.ind_cfg.sma_fast} crossover "
            f"(price={current_close:.2f}, SMA{self.ind_cfg.sma_fast}={sma_fast:.2f}, "
            f"SMA{self.ind_cfg.sma_slow}={sma_slow:.2f}, RSI={rsi:.1f if rsi else 'N/A'}, "
            f"ATR={atr:.2f})"
        )
        
        return SignalResult(
            action=direction,
            reason=signal_msg,
            passed_filters=passed_filters,
            failed_filter=None,
            indicators=indicators
        )
    
    def get_filter_status(self, ohlcv: List[List[float]]) -> Dict[str, Any]:
        """
        Get current status of all filters without generating a signal.
        
        Useful for debugging and status reporting.
        
        Returns:
            Dict with filter statuses
        """
        result = self.evaluate_signal(ohlcv)
        
        return {
            'action': result.action,
            'reason': result.reason,
            'passed_filters': result.passed_filters,
            'failed_filter': result.failed_filter,
            'indicators': result.indicators,
            'filter_count': len(result.passed_filters)
        }
