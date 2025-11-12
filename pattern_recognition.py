# pattern_recognition.py - Chart pattern detection for technical analysis
from __future__ import annotations

from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import math


class PatternType(Enum):
    """Recognized chart patterns."""
    # Continuation patterns
    ASCENDING_TRIANGLE = "ascending_triangle"
    DESCENDING_TRIANGLE = "descending_triangle"
    SYMMETRICAL_TRIANGLE = "symmetrical_triangle"
    BULL_FLAG = "bull_flag"
    BEAR_FLAG = "bear_flag"
    WEDGE_RISING = "wedge_rising"
    WEDGE_FALLING = "wedge_falling"
    
    # Reversal patterns
    HEAD_AND_SHOULDERS = "head_and_shoulders"
    INVERSE_HEAD_AND_SHOULDERS = "inverse_head_and_shoulders"
    DOUBLE_TOP = "double_top"
    DOUBLE_BOTTOM = "double_bottom"
    TRIPLE_TOP = "triple_top"
    TRIPLE_BOTTOM = "triple_bottom"
    
    # Breakout patterns
    BREAKOUT_UP = "breakout_up"
    BREAKOUT_DOWN = "breakout_down"
    FALSE_BREAKOUT = "false_breakout"


@dataclass
class Pattern:
    """Detected chart pattern."""
    type: PatternType
    confidence: float  # 0.0 to 1.0
    start_index: int
    end_index: int
    target_price: Optional[float]
    stop_loss: Optional[float]
    description: str
    metadata: dict


class PatternDetector:
    """Detect technical chart patterns in price data."""
    
    @staticmethod
    def find_peaks_and_troughs(
        prices: List[float],
        window: int = 5
    ) -> Tuple[List[int], List[int]]:
        """
        Find local peaks (highs) and troughs (lows) in price data.
        
        Args:
            prices: Price history
            window: Window size for peak/trough detection
            
        Returns:
            Tuple of (peak_indices, trough_indices)
        """
        peaks = []
        troughs = []
        
        for i in range(window, len(prices) - window):
            # Check if it's a peak
            if all(prices[i] >= prices[i-j] for j in range(1, window+1)) and \
               all(prices[i] >= prices[i+j] for j in range(1, window+1)):
                peaks.append(i)
            
            # Check if it's a trough
            if all(prices[i] <= prices[i-j] for j in range(1, window+1)) and \
               all(prices[i] <= prices[i+j] for j in range(1, window+1)):
                troughs.append(i)
        
        return peaks, troughs
    
    @staticmethod
    def detect_triangle(
        prices: List[float],
        min_touches: int = 4
    ) -> Optional[Pattern]:
        """
        Detect triangle patterns (ascending, descending, symmetrical).
        
        Args:
            prices: Price history
            min_touches: Minimum touches of support/resistance
            
        Returns:
            Detected pattern or None
        """
        if len(prices) < 20:
            return None
        
        recent_prices = prices[-30:]
        peaks, troughs = PatternDetector.find_peaks_and_troughs(recent_prices)
        
        if len(peaks) < 2 or len(troughs) < 2:
            return None
        
        # Calculate trend lines
        peak_trend = PatternDetector._calculate_trend(
            [recent_prices[i] for i in peaks[-min_touches:]]
        )
        trough_trend = PatternDetector._calculate_trend(
            [recent_prices[i] for i in troughs[-min_touches:]]
        )
        
        # Ascending triangle: flat resistance, rising support
        if abs(peak_trend) < 0.001 and trough_trend > 0.001:
            resistance = max(recent_prices[i] for i in peaks[-3:])
            target = resistance * 1.05  # 5% above resistance
            return Pattern(
                type=PatternType.ASCENDING_TRIANGLE,
                confidence=0.7,
                start_index=len(prices) - 30,
                end_index=len(prices) - 1,
                target_price=target,
                stop_loss=min(recent_prices[i] for i in troughs[-2:]),
                description="Bullish continuation pattern - expect upward breakout",
                metadata={"resistance": resistance}
            )
        
        # Descending triangle: falling resistance, flat support
        elif peak_trend < -0.001 and abs(trough_trend) < 0.001:
            support = min(recent_prices[i] for i in troughs[-3:])
            target = support * 0.95  # 5% below support
            return Pattern(
                type=PatternType.DESCENDING_TRIANGLE,
                confidence=0.7,
                start_index=len(prices) - 30,
                end_index=len(prices) - 1,
                target_price=target,
                stop_loss=max(recent_prices[i] for i in peaks[-2:]),
                description="Bearish continuation pattern - expect downward breakout",
                metadata={"support": support}
            )
        
        # Symmetrical triangle: converging lines
        elif peak_trend < -0.001 and trough_trend > 0.001:
            current = prices[-1]
            range_size = max(recent_prices) - min(recent_prices)
            target_up = current + range_size * 0.5
            target_down = current - range_size * 0.5
            
            return Pattern(
                type=PatternType.SYMMETRICAL_TRIANGLE,
                confidence=0.6,
                start_index=len(prices) - 30,
                end_index=len(prices) - 1,
                target_price=target_up,  # Neutral, could go either way
                stop_loss=target_down,
                description="Neutral pattern - breakout direction unclear",
                metadata={"range": range_size}
            )
        
        return None
    
    @staticmethod
    def detect_head_and_shoulders(
        prices: List[float]
    ) -> Optional[Pattern]:
        """
        Detect head and shoulders pattern (bearish reversal).
        
        Args:
            prices: Price history
            
        Returns:
            Detected pattern or None
        """
        if len(prices) < 30:
            return None
        
        recent_prices = prices[-40:]
        peaks, _ = PatternDetector.find_peaks_and_troughs(recent_prices)
        
        if len(peaks) < 3:
            return None
        
        # Take last 3 peaks
        left_shoulder_idx = peaks[-3]
        head_idx = peaks[-2]
        right_shoulder_idx = peaks[-1]
        
        left_shoulder = recent_prices[left_shoulder_idx]
        head = recent_prices[head_idx]
        right_shoulder = recent_prices[right_shoulder_idx]
        
        # Check for head and shoulders pattern
        # Head should be higher than both shoulders
        # Shoulders should be roughly equal height
        shoulder_diff_pct = abs(left_shoulder - right_shoulder) / left_shoulder
        
        if head > left_shoulder and head > right_shoulder and shoulder_diff_pct < 0.05:
            # Calculate neckline (support line connecting troughs between peaks)
            neckline = min(recent_prices[left_shoulder_idx:head_idx]) * 0.98
            target = neckline - (head - neckline)  # Project down by head height
            
            return Pattern(
                type=PatternType.HEAD_AND_SHOULDERS,
                confidence=0.8,
                start_index=len(prices) - 40 + left_shoulder_idx,
                end_index=len(prices) - 1,
                target_price=target,
                stop_loss=head,
                description="Strong bearish reversal pattern - expect price drop",
                metadata={
                    "neckline": neckline,
                    "head_price": head,
                    "left_shoulder": left_shoulder,
                    "right_shoulder": right_shoulder
                }
            )
        
        # Check for inverse head and shoulders (bullish reversal)
        troughs = [i for i in range(len(recent_prices)) if recent_prices[i] == min(recent_prices[max(0,i-5):i+5])]
        if len(troughs) >= 3:
            left_trough_idx = troughs[-3]
            head_idx = troughs[-2]
            right_trough_idx = troughs[-1]
            
            left_trough = recent_prices[left_trough_idx]
            inv_head = recent_prices[head_idx]
            right_trough = recent_prices[right_trough_idx]
            
            trough_diff_pct = abs(left_trough - right_trough) / left_trough
            
            if inv_head < left_trough and inv_head < right_trough and trough_diff_pct < 0.05:
                neckline = max(recent_prices[left_trough_idx:head_idx]) * 1.02
                target = neckline + (neckline - inv_head)
                
                return Pattern(
                    type=PatternType.INVERSE_HEAD_AND_SHOULDERS,
                    confidence=0.8,
                    start_index=len(prices) - 40 + left_trough_idx,
                    end_index=len(prices) - 1,
                    target_price=target,
                    stop_loss=inv_head,
                    description="Strong bullish reversal pattern - expect price rise",
                    metadata={"neckline": neckline, "head_price": inv_head}
                )
        
        return None
    
    @staticmethod
    def detect_double_top_bottom(
        prices: List[float]
    ) -> Optional[Pattern]:
        """
        Detect double top (bearish) or double bottom (bullish) patterns.
        
        Args:
            prices: Price history
            
        Returns:
            Detected pattern or None
        """
        if len(prices) < 25:
            return None
        
        recent_prices = prices[-35:]
        peaks, troughs = PatternDetector.find_peaks_and_troughs(recent_prices)
        
        # Double top
        if len(peaks) >= 2:
            last_peak_idx = peaks[-1]
            prev_peak_idx = peaks[-2]
            
            last_peak = recent_prices[last_peak_idx]
            prev_peak = recent_prices[prev_peak_idx]
            
            # Peaks should be at similar height
            peak_diff_pct = abs(last_peak - prev_peak) / prev_peak
            
            if peak_diff_pct < 0.03:  # Within 3%
                # Find trough between peaks
                between_low = min(recent_prices[prev_peak_idx:last_peak_idx])
                support = between_low * 0.98
                target = support - (prev_peak - support)
                
                return Pattern(
                    type=PatternType.DOUBLE_TOP,
                    confidence=0.75,
                    start_index=len(prices) - 35 + prev_peak_idx,
                    end_index=len(prices) - 1,
                    target_price=target,
                    stop_loss=max(last_peak, prev_peak),
                    description="Bearish reversal - expect decline after resistance test",
                    metadata={"resistance": prev_peak, "support": support}
                )
        
        # Double bottom
        if len(troughs) >= 2:
            last_trough_idx = troughs[-1]
            prev_trough_idx = troughs[-2]
            
            last_trough = recent_prices[last_trough_idx]
            prev_trough = recent_prices[prev_trough_idx]
            
            trough_diff_pct = abs(last_trough - prev_trough) / prev_trough
            
            if trough_diff_pct < 0.03:
                # Find peak between troughs
                between_high = max(recent_prices[prev_trough_idx:last_trough_idx])
                resistance = between_high * 1.02
                target = resistance + (resistance - prev_trough)
                
                return Pattern(
                    type=PatternType.DOUBLE_BOTTOM,
                    confidence=0.75,
                    start_index=len(prices) - 35 + prev_trough_idx,
                    end_index=len(prices) - 1,
                    target_price=target,
                    stop_loss=min(last_trough, prev_trough),
                    description="Bullish reversal - expect rally after support test",
                    metadata={"support": prev_trough, "resistance": resistance}
                )
        
        return None
    
    @staticmethod
    def detect_breakout(
        prices: List[float],
        volume: Optional[List[float]] = None
    ) -> Optional[Pattern]:
        """
        Detect breakout patterns (price breaking through support/resistance).
        
        Args:
            prices: Price history
            volume: Volume data (optional, for confirmation)
            
        Returns:
            Detected pattern or None
        """
        if len(prices) < 20:
            return None
        
        current_price = prices[-1]
        recent_prices = prices[-20:-1]  # Exclude current price
        
        recent_high = max(recent_prices)
        recent_low = min(recent_prices)
        price_range = recent_high - recent_low
        
        # Upward breakout
        if current_price > recent_high:
            breakout_pct = ((current_price - recent_high) / recent_high) * 100
            
            # Check volume confirmation if available
            volume_confirmed = True
            if volume and len(volume) >= 20:
                avg_volume = sum(volume[-20:-1]) / 19
                current_volume = volume[-1]
                volume_confirmed = current_volume > avg_volume * 1.2  # 20% above average
            
            confidence = min(0.9, breakout_pct / 2 * 0.7 + (0.3 if volume_confirmed else 0))
            target = current_price + price_range
            
            return Pattern(
                type=PatternType.BREAKOUT_UP,
                confidence=confidence,
                start_index=len(prices) - 20,
                end_index=len(prices) - 1,
                target_price=target,
                stop_loss=recent_high * 0.98,  # Just below breakout level
                description="Bullish breakout - price broke above resistance",
                metadata={
                    "breakout_pct": breakout_pct,
                    "resistance": recent_high,
                    "volume_confirmed": volume_confirmed
                }
            )
        
        # Downward breakdown
        elif current_price < recent_low:
            breakdown_pct = ((recent_low - current_price) / recent_low) * 100
            
            volume_confirmed = True
            if volume and len(volume) >= 20:
                avg_volume = sum(volume[-20:-1]) / 19
                current_volume = volume[-1]
                volume_confirmed = current_volume > avg_volume * 1.2
            
            confidence = min(0.9, breakdown_pct / 2 * 0.7 + (0.3 if volume_confirmed else 0))
            target = current_price - price_range
            
            return Pattern(
                type=PatternType.BREAKOUT_DOWN,
                confidence=confidence,
                start_index=len(prices) - 20,
                end_index=len(prices) - 1,
                target_price=target,
                stop_loss=recent_low * 1.02,  # Just above breakdown level
                description="Bearish breakdown - price broke below support",
                metadata={
                    "breakdown_pct": breakdown_pct,
                    "support": recent_low,
                    "volume_confirmed": volume_confirmed
                }
            )
        
        return None
    
    @staticmethod
    def _calculate_trend(values: List[float]) -> float:
        """Calculate trend slope (positive = uptrend, negative = downtrend)."""
        if len(values) < 2:
            return 0.0
        
        n = len(values)
        x_values = list(range(n))
        
        # Linear regression slope
        x_mean = sum(x_values) / n
        y_mean = sum(values) / n
        
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, values))
        denominator = sum((x - x_mean) ** 2 for x in x_values)
        
        if denominator == 0:
            return 0.0
        
        slope = numerator / denominator
        return slope
    
    @staticmethod
    def detect_all_patterns(prices: List[float]) -> List[Pattern]:
        """
        Scan for all patterns in price data.
        
        Args:
            prices: Price history
            
        Returns:
            List of detected patterns, sorted by confidence
        """
        patterns = []
        
        # Try all pattern detection methods
        pattern_detectors = [
            PatternDetector.detect_triangle,
            PatternDetector.detect_head_and_shoulders,
            PatternDetector.detect_double_top_bottom,
            PatternDetector.detect_breakout,
        ]
        
        for detector in pattern_detectors:
            try:
                pattern = detector(prices)
                if pattern:
                    patterns.append(pattern)
            except Exception as e:
                print(f"[PATTERN-DETECT-ERROR] {detector.__name__}: {e}")
        
        # Sort by confidence
        patterns.sort(key=lambda p: p.confidence, reverse=True)
        
        return patterns
