# multi_timeframe.py - Multi-timeframe trend confirmation
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
import statistics


class TrendDirection(Enum):
    """Trend direction across timeframes."""
    STRONG_BULL = "strong_bull"  # All timeframes bullish
    BULL = "bull"  # Majority bullish
    NEUTRAL = "neutral"  # Mixed signals
    BEAR = "bear"  # Majority bearish
    STRONG_BEAR = "strong_bear"  # All timeframes bearish


class TimeframeTrend:
    """Analyze trend on a single timeframe."""
    
    @staticmethod
    def calculate_sma(closes: List[float], period: int) -> Optional[float]:
        """Calculate Simple Moving Average."""
        if len(closes) < period:
            return None
        return statistics.mean(closes[-period:])
    
    @staticmethod
    def calculate_ema(closes: List[float], period: int) -> Optional[float]:
        """Calculate Exponential Moving Average."""
        if len(closes) < period:
            return None
        
        multiplier = 2 / (period + 1)
        ema = closes[0]
        
        for close in closes[1:]:
            ema = (close * multiplier) + (ema * (1 - multiplier))
        
        return ema
    
    @staticmethod
    def detect_trend(
        closes: List[float],
        fast_period: int = 9,
        slow_period: int = 21
    ) -> Tuple[str, float]:
        """
        Detect trend direction using moving average crossover.
        
        Returns:
            (direction: str, strength: float)
            direction: "bullish", "bearish", "neutral"
            strength: -1.0 to 1.0 (negative = bearish, positive = bullish)
        """
        if len(closes) < slow_period:
            return ("neutral", 0.0)
        
        fast_ma = TimeframeTrend.calculate_ema(closes, fast_period)
        slow_ma = TimeframeTrend.calculate_ema(closes, slow_period)
        
        if not fast_ma or not slow_ma:
            return ("neutral", 0.0)
        
        current_price = closes[-1]
        
        # Calculate trend strength
        # Positive = bullish, Negative = bearish
        ma_diff_pct = ((fast_ma - slow_ma) / slow_ma) * 100
        price_vs_ma = ((current_price - slow_ma) / slow_ma) * 100
        
        strength = (ma_diff_pct + price_vs_ma) / 2  # Average of both signals
        strength = max(-1.0, min(1.0, strength / 2))  # Normalize to -1 to 1
        
        # Determine direction
        if strength > 0.2:
            direction = "bullish"
        elif strength < -0.2:
            direction = "bearish"
        else:
            direction = "neutral"
        
        return (direction, strength)


class MultiTimeframeAnalyzer:
    """
    Analyze trends across multiple timeframes for confirmation.
    Checks 1m, 15m, and 1h charts for alignment.
    """
    
    def __init__(self):
        self.timeframes = ["1m", "15m", "1h"]
        self.fast_periods = {"1m": 9, "15m": 9, "1h": 9}
        self.slow_periods = {"1m": 21, "15m": 21, "1h": 21}
    
    def analyze_all_timeframes(
        self,
        ohlcv_data: Dict[str, List[List[float]]]  # {timeframe: ohlcv_list}
    ) -> Dict[str, Any]:
        """
        Analyze all timeframes and provide consensus.
        
        Args:
            ohlcv_data: Dictionary of {timeframe: ohlcv_candles}
                       Each candle is [timestamp, open, high, low, close, volume]
        
        Returns:
            Dict with consensus direction, strength, and per-timeframe analysis
        """
        results = {}
        
        # Analyze each timeframe
        for tf in self.timeframes:
            if tf not in ohlcv_data or not ohlcv_data[tf]:
                results[tf] = {
                    "direction": "neutral",
                    "strength": 0.0,
                    "available": False
                }
                continue
            
            closes = [candle[4] for candle in ohlcv_data[tf]]
            direction, strength = TimeframeTrend.detect_trend(
                closes,
                self.fast_periods[tf],
                self.slow_periods[tf]
            )
            
            results[tf] = {
                "direction": direction,
                "strength": strength,
                "available": True
            }
        
        # Calculate consensus
        consensus = self._calculate_consensus(results)
        
        return {
            "timeframes": results,
            "consensus": consensus["direction"],
            "consensus_strength": consensus["strength"],
            "alignment_score": consensus["alignment"],
            "recommendation": consensus["recommendation"]
        }
    
    def _calculate_consensus(
        self,
        timeframe_results: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Calculate consensus across all timeframes."""
        available_timeframes = [
            tf for tf, data in timeframe_results.items()
            if data.get("available", False)
        ]
        
        if not available_timeframes:
            return {
                "direction": TrendDirection.NEUTRAL.value,
                "strength": 0.0,
                "alignment": 0.0,
                "recommendation": "hold"
            }
        
        # Count bullish, bearish, neutral signals
        bullish_count = sum(
            1 for tf in available_timeframes
            if timeframe_results[tf]["direction"] == "bullish"
        )
        bearish_count = sum(
            1 for tf in available_timeframes
            if timeframe_results[tf]["direction"] == "bearish"
        )
        neutral_count = len(available_timeframes) - bullish_count - bearish_count
        
        # Calculate average strength
        avg_strength = statistics.mean([
            timeframe_results[tf]["strength"]
            for tf in available_timeframes
        ])
        
        # Calculate alignment score (how well timeframes agree)
        max_count = max(bullish_count, bearish_count, neutral_count)
        alignment = max_count / len(available_timeframes)
        
        # Determine consensus direction
        if bullish_count == len(available_timeframes):
            consensus = TrendDirection.STRONG_BULL
        elif bullish_count > bearish_count and bullish_count >= neutral_count:
            consensus = TrendDirection.BULL
        elif bearish_count == len(available_timeframes):
            consensus = TrendDirection.STRONG_BEAR
        elif bearish_count > bullish_count and bearish_count >= neutral_count:
            consensus = TrendDirection.BEAR
        else:
            consensus = TrendDirection.NEUTRAL
        
        # Make recommendation
        if consensus in [TrendDirection.STRONG_BULL, TrendDirection.BULL] and alignment >= 0.66:
            recommendation = "buy"
        elif consensus in [TrendDirection.STRONG_BEAR, TrendDirection.BEAR] and alignment >= 0.66:
            recommendation = "sell"
        else:
            recommendation = "hold"
        
        return {
            "direction": consensus.value,
            "strength": avg_strength,
            "alignment": alignment,
            "recommendation": recommendation,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count
        }
    
    def get_entry_confidence(
        self,
        analysis: Dict[str, Any],
        position_side: str  # "long" or "short"
    ) -> float:
        """
        Calculate entry confidence based on multi-timeframe alignment.
        
        Args:
            analysis: Result from analyze_all_timeframes()
            position_side: Intended position ("long" or "short")
            
        Returns:
            Confidence score 0.0 to 1.0
        """
        if position_side not in ["long", "short"]:
            return 0.0
        
        consensus = analysis["consensus"]
        alignment = analysis["alignment_score"]
        strength = abs(analysis["consensus_strength"])
        
        # Check if consensus matches intended position
        if position_side == "long":
            consensus_match = consensus in ["strong_bull", "bull"]
        else:
            consensus_match = consensus in ["strong_bear", "bear"]
        
        if not consensus_match:
            return 0.0
        
        # Confidence is combination of alignment and strength
        confidence = (alignment * 0.6) + (strength * 0.4)
        
        return confidence


def fetch_multi_timeframe_data(
    exchange: Any,
    symbol: str,
    timeframes: List[str] = ["1m", "15m", "1h"]
) -> Dict[str, List[List[float]]]:
    """
    Fetch OHLCV data for multiple timeframes.
    
    Args:
        exchange: CCXT exchange instance
        symbol: Trading pair (e.g., "BTC/USD")
        timeframes: List of timeframe strings
        
    Returns:
        Dict of {timeframe: ohlcv_data}
    """
    data = {}
    
    for tf in timeframes:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=50)
            data[tf] = ohlcv
        except Exception as e:
            print(f"[MTF-ERR] Failed to fetch {symbol} {tf}: {e}")
            data[tf] = []
    
    return data
