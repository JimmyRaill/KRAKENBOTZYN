# regime_detector.py - Professional market regime detection engine
"""
Market Regime Detection System

Classifies market into exactly ONE of five regimes:
- TREND_UP: Strong uptrend with momentum
- TREND_DOWN: Strong downtrend with momentum
- RANGE: Sideways/consolidation (mean-reversion environment)
- BREAKOUT_EXPANSION: Volatility spike + range breakout
- NO_TRADE: Garbage conditions (too quiet, conflicting signals, etc.)

Uses multi-timeframe analysis (5m primary + 15m/1h context)
All decisions based on CLOSED candles only.
"""

from enum import Enum
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import statistics


class MarketRegime(Enum):
    """Market regime classification - exactly ONE active at any time"""
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    BREAKOUT_EXPANSION = "breakout_expansion"
    NO_TRADE = "no_trade"


@dataclass
class RegimeSignals:
    """Signals used for regime detection - for transparency"""
    # Trend signals
    sma20: float
    sma50: float
    price: float
    sma20_above_sma50: bool
    price_above_sma20: bool
    
    # Trend strength
    adx: float
    trending: bool  # ADX > threshold
    
    # Volatility
    atr: float
    atr_pct: float  # ATR / price
    recent_atr_avg: float
    atr_spike: bool  # ATR >> recent average
    
    # Range detection
    bb_upper: float
    bb_lower: float
    bb_width_pct: float
    price_in_range: bool
    
    # Breakout detection
    range_high: float
    range_low: float
    broke_above_range: bool
    broke_below_range: bool
    
    # Volume (if available)
    volume: Optional[float]
    volume_elevated: bool
    
    # Higher timeframe context
    htf_bullish: bool
    htf_bearish: bool


@dataclass
class RegimeResult:
    """Result of regime detection"""
    regime: MarketRegime
    confidence: float  # 0.0-1.0
    reason: str
    signals: RegimeSignals
    
    def __str__(self) -> str:
        return f"{self.regime.value.upper()} ({self.confidence:.2f}): {self.reason}"


class RegimeDetector:
    """
    Professional market regime detection engine.
    
    Configuration via trading_config.py:
    - ADX thresholds for trending vs ranging
    - ATR spike multipliers
    - Bollinger Band periods and deviations
    - Volume percentile thresholds
    """
    
    def __init__(self, config: Any):
        """
        Args:
            config: TradingConfig instance from trading_config.py
        """
        self.config = config
    
    def detect_regime(
        self,
        ohlcv_5m: List[List[float]],
        indicators_5m: Dict[str, float],
        indicators_htf: Dict[str, float]
    ) -> RegimeResult:
        """
        Detect current market regime using multi-timeframe analysis.
        
        Args:
            ohlcv_5m: 5-minute candles (primary timeframe)
            indicators_5m: Pre-calculated indicators on 5m {sma20, sma50, atr, adx, bb_upper, bb_lower}
            indicators_htf: Pre-calculated indicators on HTF {sma20, sma50}
            
        Returns:
            RegimeResult with regime classification and detailed signals
            
        Note:
            Higher timeframe context comes from indicators_htf (pre-calculated).
            This avoids redundant OHLCV fetching and keeps interface clean.
        """
        if not ohlcv_5m or len(ohlcv_5m) < 50:
            return self._no_trade_regime("Insufficient data")
        
        # Extract current price and volume
        current_candle = ohlcv_5m[-1]
        price = float(current_candle[4])  # Close
        volume = float(current_candle[5]) if len(current_candle) > 5 else None
        
        # Build signals object
        signals = self._build_signals(
            price, volume, ohlcv_5m,
            indicators_5m, indicators_htf
        )
        
        # Regime detection logic (sequential priority)
        
        # 1. NO_TRADE conditions (highest priority - filter garbage)
        if self._is_no_trade_conditions(signals):
            return RegimeResult(
                regime=MarketRegime.NO_TRADE,
                confidence=0.9,
                reason=self._get_no_trade_reason(signals),
                signals=signals
            )
        
        # 2. BREAKOUT_EXPANSION (second priority - high volatility events)
        if self._is_breakout_expansion(signals):
            direction = "upside" if signals.broke_above_range else "downside"
            return RegimeResult(
                regime=MarketRegime.BREAKOUT_EXPANSION,
                confidence=0.85,
                reason=f"Volatility spike + {direction} breakout (ATR {signals.atr_pct*100:.2f}%)",
                signals=signals
            )
        
        # 3. TREND_UP (strong uptrend with confirmation)
        if self._is_trend_up(signals):
            return RegimeResult(
                regime=MarketRegime.TREND_UP,
                confidence=0.8,
                reason=f"Strong uptrend (ADX {signals.adx:.1f}, price>{int(signals.sma20)}>{int(signals.sma50)})",
                signals=signals
            )
        
        # 4. TREND_DOWN (strong downtrend with confirmation)
        if self._is_trend_down(signals):
            return RegimeResult(
                regime=MarketRegime.TREND_DOWN,
                confidence=0.8,
                reason=f"Strong downtrend (ADX {signals.adx:.1f}, price<{int(signals.sma20)}<{int(signals.sma50)})",
                signals=signals
            )
        
        # 5. RANGE (default for low-ADX sideways markets)
        if self._is_range_market(signals):
            return RegimeResult(
                regime=MarketRegime.RANGE,
                confidence=0.75,
                reason=f"Ranging market (ADX {signals.adx:.1f}<{self.config.regime.adx_threshold}, tight BB)",
                signals=signals
            )
        
        # Fallback: conflicting signals = NO_TRADE
        return RegimeResult(
            regime=MarketRegime.NO_TRADE,
            confidence=0.7,
            reason="Conflicting signals - no clear regime",
            signals=signals
        )
    
    def _build_signals(
        self,
        price: float,
        volume: Optional[float],
        ohlcv_5m: List[List[float]],
        ind_5m: Dict[str, float],
        ind_htf: Dict[str, float]
    ) -> RegimeSignals:
        """Build comprehensive signals object"""
        
        # Extract 5m indicators
        sma20 = ind_5m.get('sma20', price)
        sma50 = ind_5m.get('sma50', price)
        atr = ind_5m.get('atr', 0.0)
        adx = ind_5m.get('adx', 0.0)
        bb_upper = ind_5m.get('bb_upper', price * 1.02)
        bb_lower = ind_5m.get('bb_lower', price * 0.98)
        
        # Calculate ATR metrics
        atr_pct = (atr / price) if price > 0 else 0.0
        
        # Calculate historical ATR values for spike detection
        recent_atr_values = self._calculate_atr_history(ohlcv_5m, period=14, lookback=20)
        recent_atr_avg = statistics.mean(recent_atr_values) if recent_atr_values else atr
        atr_spike = (atr > recent_atr_avg * self.config.regime.atr_spike_multiplier) if recent_atr_avg > 0 else False
        
        # Bollinger Band metrics
        bb_width_pct = ((bb_upper - bb_lower) / price) * 100 if price > 0 else 0.0
        price_in_range = bb_lower <= price <= bb_upper
        
        # Range detection (PRIOR 20 candles, excluding current)
        # CRITICAL: Must exclude current candle so breakout can be detected
        if len(ohlcv_5m) < 21:
            # Not enough data for range detection
            range_high = price * 1.05
            range_low = price * 0.95
        else:
            # Use PRIOR 20 candles to establish the range BEFORE current candle
            prior_candles = ohlcv_5m[-21:-1]
            range_high = max(c[2] for c in prior_candles)  # Highs
            range_low = min(c[3] for c in prior_candles)  # Lows
        
        # Breakout detection (price must break by clear margin)
        breakout_margin = atr * self.config.regime.breakout_margin_atr
        broke_above_range = price > (range_high + breakout_margin)
        broke_below_range = price < (range_low - breakout_margin)
        
        # Volume analysis (if available)
        volume_elevated = False
        if volume is not None and len(ohlcv_5m) >= 20:
            recent_volumes = [c[5] for c in ohlcv_5m[-20:] if len(c) > 5]
            if recent_volumes:
                avg_volume = statistics.mean(recent_volumes)
                volume_elevated = volume > (avg_volume * self.config.regime.volume_spike_multiplier)
        
        # Higher timeframe context
        htf_sma20 = ind_htf.get('sma20', price)
        htf_sma50 = ind_htf.get('sma50', price)
        htf_bullish = (price > htf_sma20) and (htf_sma20 > htf_sma50)
        htf_bearish = (price < htf_sma20) and (htf_sma20 < htf_sma50)
        
        return RegimeSignals(
            sma20=sma20,
            sma50=sma50,
            price=price,
            sma20_above_sma50=(sma20 > sma50),
            price_above_sma20=(price > sma20),
            adx=adx,
            trending=(adx > self.config.regime.adx_threshold),
            atr=atr,
            atr_pct=atr_pct,
            recent_atr_avg=recent_atr_avg,
            atr_spike=atr_spike,
            bb_upper=bb_upper,
            bb_lower=bb_lower,
            bb_width_pct=bb_width_pct,
            price_in_range=price_in_range,
            range_high=range_high,
            range_low=range_low,
            broke_above_range=broke_above_range,
            broke_below_range=broke_below_range,
            volume=volume,
            volume_elevated=volume_elevated,
            htf_bullish=htf_bullish,
            htf_bearish=htf_bearish
        )
    
    def _is_no_trade_conditions(self, s: RegimeSignals) -> bool:
        """Check for NO_TRADE conditions (garbage market)"""
        # Too quiet (volatility too low)
        if s.atr_pct < self.config.regime.min_volatility_pct:
            return True
        
        # Extremely low ADX (no structure)
        if s.adx < self.config.regime.min_adx:
            return True
        
        # Volume too low (if available)
        if s.volume is not None:
            if s.volume < self.config.regime.min_volume:
                return True
        
        return False
    
    def _is_breakout_expansion(self, s: RegimeSignals) -> bool:
        """Check for BREAKOUT_EXPANSION regime"""
        # Must have volatility spike
        if not s.atr_spike:
            return False
        
        # Must break out of recent range
        if not (s.broke_above_range or s.broke_below_range):
            return False
        
        # Preferably with elevated volume
        if s.volume is not None and not s.volume_elevated:
            return False
        
        return True
    
    def _is_trend_up(self, s: RegimeSignals) -> bool:
        """Check for TREND_UP regime"""
        # Must be trending (ADX)
        if not s.trending:
            return False
        
        # 5m alignment: price > SMA20 > SMA50
        if not (s.price_above_sma20 and s.sma20_above_sma50):
            return False
        
        # Higher timeframe confirmation
        if not s.htf_bullish:
            return False
        
        return True
    
    def _is_trend_down(self, s: RegimeSignals) -> bool:
        """Check for TREND_DOWN regime"""
        # Must be trending (ADX)
        if not s.trending:
            return False
        
        # 5m alignment: price < SMA20 < SMA50
        if s.price_above_sma20 or s.sma20_above_sma50:
            return False
        
        # Higher timeframe confirmation
        if not s.htf_bearish:
            return False
        
        return True
    
    def _is_range_market(self, s: RegimeSignals) -> bool:
        """Check for RANGE regime"""
        # Low ADX (not trending)
        if s.trending:
            return False
        
        # Price oscillating in narrow range
        if s.bb_width_pct > self.config.regime.max_range_width_pct:
            return False
        
        # Price within Bollinger Bands
        if not s.price_in_range:
            return False
        
        return True
    
    def _get_no_trade_reason(self, s: RegimeSignals) -> str:
        """Generate reason for NO_TRADE classification"""
        reasons = []
        
        if s.atr_pct < self.config.regime.min_volatility_pct:
            reasons.append(f"volatility too low ({s.atr_pct*100:.3f}%)")
        
        if s.adx < self.config.regime.min_adx:
            reasons.append(f"ADX too low ({s.adx:.1f})")
        
        if s.volume is not None and s.volume < self.config.regime.min_volume:
            reasons.append("volume too low")
        
        if not reasons:
            reasons.append("conflicting signals")
        
        return "NO_TRADE: " + ", ".join(reasons)
    
    def _calculate_atr_history(
        self,
        ohlcv: List[List[float]],
        period: int = 14,
        lookback: int = 20
    ) -> List[float]:
        """
        Calculate historical ATR values for spike detection.
        
        Args:
            ohlcv: OHLC candles
            period: ATR period (default 14)
            lookback: Number of historical ATR values to return
            
        Returns:
            List of recent ATR values (oldest to newest)
        """
        if len(ohlcv) < period + lookback:
            return []
        
        atr_values = []
        
        # Calculate ATR for each point in lookback window
        for i in range(lookback):
            end_idx = len(ohlcv) - lookback + i + 1
            start_idx = max(0, end_idx - period - 1)
            window = ohlcv[start_idx:end_idx]
            
            if len(window) < period + 1:
                continue
            
            # Calculate True Range for window
            tr_list = []
            for j in range(1, len(window)):
                high = window[j][2]
                low = window[j][3]
                prev_close = window[j-1][4]
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                tr_list.append(tr)
            
            # Average True Range
            if tr_list:
                atr_values.append(statistics.mean(tr_list[-period:]))
        
        return atr_values
    
    def _no_trade_regime(self, reason: str) -> RegimeResult:
        """Helper to create NO_TRADE result with empty signals"""
        empty_signals = RegimeSignals(
            sma20=0, sma50=0, price=0, sma20_above_sma50=False, price_above_sma20=False,
            adx=0, trending=False, atr=0, atr_pct=0, recent_atr_avg=0, atr_spike=False,
            bb_upper=0, bb_lower=0, bb_width_pct=0, price_in_range=False,
            range_high=0, range_low=0, broke_above_range=False, broke_below_range=False,
            volume=None, volume_elevated=False, htf_bullish=False, htf_bearish=False
        )
        
        return RegimeResult(
            regime=MarketRegime.NO_TRADE,
            confidence=0.95,
            reason=reason,
            signals=empty_signals
        )


# Singleton instance getter
_detector_instance: Optional[RegimeDetector] = None

def get_regime_detector() -> RegimeDetector:
    """Get singleton instance of RegimeDetector"""
    global _detector_instance
    if _detector_instance is None:
        from trading_config import get_config
        config = get_config()
        _detector_instance = RegimeDetector(config)
    return _detector_instance
