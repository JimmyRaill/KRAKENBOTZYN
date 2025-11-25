"""
strategy_orchestrator.py - Unified Regime-Aware Strategy Selection

Integrates regime detection, multi-timeframe context, and strategy selection
into a single decision engine. Replaces the basic SMA20 crossover logic in
autopilot.py with professional regime-aware trading.

Architecture:
1. Detect market regime (TREND_UP, TREND_DOWN, RANGE, BREAKOUT_EXPANSION, NO_TRADE)
2. Fetch higher timeframe context (15m/1h trend alignment)
3. Select appropriate strategy based on regime
4. Generate trade signal with comprehensive reasoning
5. Return unified signal for autopilot execution

Strategies per regime:
- TREND_UP/DOWN: Pullback entries with HTF confirmation
- RANGE: Mean reversion with Bollinger Bands
- BREAKOUT_EXPANSION: Breakout continuation with volume
- NO_TRADE: No signals (low volatility, conflicting signals)
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass
from regime_detector import get_regime_detector, MarketRegime
from multi_timeframe_context import get_mtf_context, HTFContext
from trading_config import TradingConfig
import candle_strategy as cs
from loguru import logger


@dataclass
class TradeSignal:
    """Unified trade signal from strategy orchestrator"""
    action: str  # 'long', 'short', 'hold'
    regime: MarketRegime
    confidence: float  # 0.0 to 1.0
    reason: str
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_size_multiplier: float = 1.0  # Adjust position size based on confidence
    
    # Metadata
    htf_aligned: bool = False
    dominant_trend: Optional[str] = None
    symbol: str = ""


class StrategyOrchestrator:
    """
    Regime-aware strategy orchestrator.
    
    Coordinates regime detection, HTF analysis, and strategy selection
    to generate high-probability trade signals.
    
    Usage:
        orchestrator = get_orchestrator()
        signal = orchestrator.generate_signal(
            symbol="BTC/USD",
            ohlcv_5m=candles,
            indicators_5m=indicators
        )
        
        if signal.action == 'long':
            # Execute long trade
            pass
    """
    
    def __init__(self):
        """Initialize strategy orchestrator"""
        from trading_config import get_config
        self.regime_detector = get_regime_detector()
        self.mtf_context = get_mtf_context()
        self.config = get_config()
        logger.info(f"StrategyOrchestrator initialized (aggressive_mode={self.config.regime.aggressive_mode}, "
                   f"BB_threshold={self.config.regime.aggressive_bb_pct if self.config.regime.aggressive_mode else self.config.regime.conservative_bb_pct}%, "
                   f"RSI_max={self.config.regime.aggressive_rsi_max if self.config.regime.aggressive_mode else self.config.regime.conservative_rsi_max})")
    
    def generate_signal(
        self,
        symbol: str,
        ohlcv_5m: list,
        indicators_5m: Dict[str, Any]
    ) -> TradeSignal:
        """
        Generate trade signal based on regime and HTF context.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            ohlcv_5m: 5-minute OHLCV candles (at least 50)
            indicators_5m: Pre-calculated 5m indicators
                {
                    'sma20': float,
                    'sma50': float,
                    'rsi': float,
                    'atr': float,
                    'adx': float,
                    'bb_middle': float,
                    'bb_upper': float,
                    'bb_lower': float,
                    'volume_percentile': float
                }
        
        Returns:
            TradeSignal with action, regime, confidence, reasoning
        """
        # Get current price
        price = ohlcv_5m[-1][4]
        
        # Step 1: Get HTF context for indicators_htf
        htf = self.mtf_context.get_context(symbol)
        
        # Build HTF indicators dict for regime detector
        indicators_htf = {
            'sma20_15m': htf.sma20_15m or 0.0,
            'sma50_15m': htf.sma50_15m or 0.0,
            'atr_15m': htf.atr_15m or 0.0,
            'sma20_1h': htf.sma20_1h or 0.0,
            'sma50_1h': htf.sma50_1h or 0.0,
            'atr_1h': htf.atr_1h or 0.0,
        }
        
        # Step 2: Detect regime (pass HTF dominant trend to avoid key mismatches)
        regime_result = self.regime_detector.detect_regime(
            ohlcv_5m=ohlcv_5m,
            indicators_5m=indicators_5m,
            indicators_htf=indicators_htf,
            htf_dominant_trend=htf.dominant_trend
        )
        
        logger.info(
            f"[{symbol}] Regime: {regime_result.regime.value}, "
            f"confidence={regime_result.confidence:.2f}, reason={regime_result.reason}"
        )
        
        # Step 3: Route to strategy based on regime
        if regime_result.regime == MarketRegime.NO_TRADE:
            return self._no_trade_signal(symbol, price, regime_result, htf)
        
        elif regime_result.regime == MarketRegime.TREND_UP:
            return self._trend_up_strategy(symbol, price, ohlcv_5m, indicators_5m, regime_result, htf)
        
        elif regime_result.regime == MarketRegime.TREND_DOWN:
            return self._trend_down_strategy(symbol, price, ohlcv_5m, indicators_5m, regime_result, htf)
        
        elif regime_result.regime == MarketRegime.RANGE:
            return self._range_strategy(symbol, price, ohlcv_5m, indicators_5m, regime_result, htf)
        
        elif regime_result.regime == MarketRegime.BREAKOUT_EXPANSION:
            return self._breakout_strategy(symbol, price, ohlcv_5m, indicators_5m, regime_result, htf)
        
        else:
            return self._no_trade_signal(symbol, price, regime_result, htf, "Unknown regime")
    
    def _trend_up_strategy(
        self,
        symbol: str,
        price: float,
        ohlcv_5m: list,
        indicators_5m: Dict[str, Any],
        regime_result: Any,
        htf: HTFContext
    ) -> TradeSignal:
        """
        Trend pullback strategy for TREND_UP regime.
        
        IMPROVED Logic (v2 - wider stops, real pullbacks):
        - HTF must be aligned bullish (or neutral)
        - Wait for REAL pullback: price must retrace >= 0.75 ATR from recent swing high
        - Price must be at or below SMA20 (not chasing momentum)
        - RSI < 65 (not overbought - tightened from 70)
        - Enter long with 3x ATR stop (widened from 2x)
        """
        sma20 = indicators_5m.get('sma20')
        rsi = indicators_5m.get('rsi')
        atr = indicators_5m.get('atr')
        
        # HTF filter: only trade with HTF trend or neutral
        if htf.dominant_trend == 'down':
            return TradeSignal(
                action='hold',
                regime=regime_result.regime,
                confidence=0.0,
                reason=f"TREND_UP but HTF bearish ({htf.trend_15m}/{htf.trend_1h}) - skip",
                entry_price=price,
                htf_aligned=htf.htf_aligned,
                dominant_trend=htf.dominant_trend,
                symbol=symbol
            )
        
        # Calculate recent swing high (highest high in last 10 candles)
        lookback = min(10, len(ohlcv_5m))
        recent_highs = [candle[2] for candle in ohlcv_5m[-lookback:]]  # [2] = high
        swing_high = max(recent_highs) if recent_highs else price
        
        # Calculate pullback depth from swing high
        pullback_depth = swing_high - price
        min_pullback_atr = 0.75  # Require at least 0.75 ATR pullback
        
        # REAL PULLBACK condition:
        # 1. Price has pulled back at least 0.75 ATR from recent swing high
        # 2. Price is at or below SMA20 (not chasing extended price)
        has_real_pullback = atr and pullback_depth >= (min_pullback_atr * atr)
        price_at_support = sma20 and price <= sma20 * 1.002  # At or below SMA20
        
        if has_real_pullback and price_at_support:
            # Check RSI not overbought (tightened threshold)
            if rsi and rsi < 65:
                # HTF bonus: increase confidence if aligned
                confidence = 0.7
                if htf.htf_aligned and htf.dominant_trend == 'up':
                    confidence = 0.85
                
                # Calculate stops with WIDER ATR multipliers
                atr_sl_mult = self.config.indicators.atr_stop_multiplier  # Now 3.0
                atr_tp_mult = self.config.indicators.atr_take_profit_multiplier  # Now 4.0
                stop_loss = price - (atr_sl_mult * atr) if atr else price * 0.97
                take_profit = price + (atr_tp_mult * atr) if atr else price * 1.06
                
                pullback_pct = (pullback_depth / swing_high) * 100 if swing_high > 0 else 0
                
                return TradeSignal(
                    action='long',
                    regime=regime_result.regime,
                    confidence=confidence,
                    reason=f"TREND_UP real pullback: price={price:.2f} ({pullback_pct:.1f}% from high {swing_high:.2f}), SMA20={sma20:.2f}, RSI={rsi:.1f}, HTF={htf.dominant_trend}",
                    entry_price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    position_size_multiplier=confidence,
                    htf_aligned=htf.htf_aligned,
                    dominant_trend=htf.dominant_trend,
                    symbol=symbol
                )
            else:
                rsi_str = f"{rsi:.1f}" if rsi else "N/A"
                return TradeSignal(
                    action='hold',
                    regime=regime_result.regime,
                    confidence=0.0,
                    reason=f"TREND_UP pullback but RSI too high ({rsi_str} >= 65)",
                    entry_price=price,
                    htf_aligned=htf.htf_aligned,
                    dominant_trend=htf.dominant_trend,
                    symbol=symbol
                )
        
        # No real pullback yet - explain why
        sma20_str = f"{sma20:.2f}" if sma20 is not None else "N/A"
        pullback_atr = pullback_depth / atr if atr and atr > 0 else 0
        
        if not has_real_pullback:
            reason = f"TREND_UP but no pullback (need {min_pullback_atr:.2f}x ATR, have {pullback_atr:.2f}x ATR from high {swing_high:.2f})"
        elif not price_at_support:
            reason = f"TREND_UP but price extended above SMA20 (price={price:.2f} > SMA20={sma20_str})"
        else:
            reason = f"TREND_UP but conditions not met (price={price:.2f}, SMA20={sma20_str})"
        
        return TradeSignal(
            action='hold',
            regime=regime_result.regime,
            confidence=0.0,
            reason=reason,
            entry_price=price,
            htf_aligned=htf.htf_aligned,
            dominant_trend=htf.dominant_trend,
            symbol=symbol
        )
    
    def _trend_down_strategy(
        self,
        symbol: str,
        price: float,
        ohlcv_5m: list,
        indicators_5m: Dict[str, Any],
        regime_result: Any,
        htf: HTFContext
    ) -> TradeSignal:
        """
        Trend down strategy for TREND_DOWN regime.
        
        MARGIN SHORT SELLING (if enabled):
        - Generate SHORT signals on aligned downtrends (15m + 1h both DOWN)
        - Use same quality filters as longs (volume, volatility, etc.)
        - Stop-loss placed ABOVE entry (inverted from longs)
        
        If shorts disabled:
        - Returns 'hold' (no action in spot-only mode)
        """
        from margin_config import is_shorts_enabled
        
        # HTF check: Skip if higher timeframes are bullish
        if htf.dominant_trend == 'up':
            return TradeSignal(
                action='hold',
                regime=regime_result.regime,
                confidence=0.0,
                reason=f"TREND_DOWN but HTF bullish ({htf.trend_15m}/{htf.trend_1h}) - no action",
                entry_price=price,
                htf_aligned=htf.htf_aligned,
                dominant_trend=htf.dominant_trend,
                symbol=symbol
            )
        
        # Check if shorts are enabled
        if not is_shorts_enabled():
            return TradeSignal(
                action='hold',
                regime=regime_result.regime,
                confidence=0.0,
                reason=f"TREND_DOWN regime - shorts disabled (set ENABLE_SHORTS=1 to enable)",
                entry_price=price,
                htf_aligned=htf.htf_aligned,
                dominant_trend=htf.dominant_trend,
                symbol=symbol
            )
        
        # SHORT SELLING ENABLED - Check for quality setup
        # Require aligned downtrend on HTF
        if htf.dominant_trend != 'down':
            return TradeSignal(
                action='hold',
                regime=regime_result.regime,
                confidence=0.0,
                reason=f"TREND_DOWN but HTF not aligned down ({htf.trend_15m}/{htf.trend_1h})",
                entry_price=price,
                htf_aligned=htf.htf_aligned,
                dominant_trend=htf.dominant_trend,
                symbol=symbol
            )
        
        # Get indicators for quality checks
        rsi = indicators_5m.get('rsi')
        sma20 = indicators_5m.get('sma20')  # Fixed: match autopilot's indicator key
        atr = indicators_5m.get('atr')
        volume = ohlcv_5m[-1][5] if len(ohlcv_5m) > 0 else 0
        
        # Quality filter: Avoid overbought RSI (don't short into strength)
        if rsi and rsi > 70:
            return TradeSignal(
                action='hold',
                regime=regime_result.regime,
                confidence=0.0,
                reason=f"TREND_DOWN but RSI overbought ({rsi:.1f}) - wait for weakness",
                entry_price=price,
                htf_aligned=htf.htf_aligned,
                dominant_trend=htf.dominant_trend,
                symbol=symbol
            )
        
        # Short entry: Price at or below SMA20 resistance in confirmed downtrend
        if sma20 and price <= sma20:
            # Price is at/below SMA20 resistance - good short entry zone
            atr_multiplier = self.config.indicators.atr_stop_multiplier
            stop_loss = price + (atr_multiplier * atr) if atr else price * 1.02
            
            confidence = 0.75
            if htf.htf_aligned:
                confidence = 0.85
            
            rsi_str = f"{rsi:.1f}" if rsi else "N/A"
            return TradeSignal(
                action='short',
                regime=regime_result.regime,
                confidence=confidence,
                reason=f"TREND_DOWN short: price={price:.2f} at/below SMA20={sma20:.2f}, RSI={rsi_str}, HTF={htf.dominant_trend}",
                entry_price=price,
                stop_loss=stop_loss,
                htf_aligned=htf.htf_aligned,
                dominant_trend=htf.dominant_trend,
                symbol=symbol
            )
        
        # Wait for better entry (rally to SMA20)
        sma20_str = f"{sma20:.2f}" if sma20 else "N/A"
        return TradeSignal(
            action='hold',
            regime=regime_result.regime,
            confidence=0.0,
            reason=f"TREND_DOWN but waiting for rally (price={price:.2f}, SMA20={sma20_str})",
            entry_price=price,
            htf_aligned=htf.htf_aligned,
            dominant_trend=htf.dominant_trend,
            symbol=symbol
        )
    
    def _range_strategy(
        self,
        symbol: str,
        price: float,
        ohlcv_5m: list,
        indicators_5m: Dict[str, Any],
        regime_result: Any,
        htf: HTFContext
    ) -> TradeSignal:
        """
        Mean reversion strategy for RANGE regime.
        
        SPOT TRADING ONLY:
        - Long at lower band (buy the dip)
        - NO shorts at upper band (spot trading limitation)
        - HTF filter: Prefer trades aligned with HTF or neutral
        - Tight stops outside bands
        - Target middle band
        
        AGGRESSIVE_RANGE_TRADING mode:
        - Normal: BB ≤40%, RSI <45
        - Aggressive: BB ≤50%, RSI <55
        """
        bb_upper = indicators_5m.get('bb_upper')
        bb_middle = indicators_5m.get('bb_middle')
        bb_lower = indicators_5m.get('bb_lower')
        rsi = indicators_5m.get('rsi')
        
        if not all([bb_upper, bb_middle, bb_lower]):
            return self._no_trade_signal(symbol, price, regime_result, htf, "Missing BB data")
        
        # HTF filter: Skip if strongly against us
        if htf.dominant_trend == 'down':
            return TradeSignal(
                action='hold',
                regime=regime_result.regime,
                confidence=0.0,
                reason=f"RANGE but HTF bearish ({htf.trend_15m}/{htf.trend_1h}) - skip mean reversion longs",
                entry_price=price,
                htf_aligned=htf.htf_aligned,
                dominant_trend=htf.dominant_trend,
                symbol=symbol
            )
        
        # AGGRESSIVE RANGE TRADING: Enter when price APPROACHES lower band
        # Calculate band position: 0% = lower, 50% = middle, 100% = upper
        band_range = (bb_upper - bb_lower) if (bb_upper and bb_lower) else 0
        price_position_pct = ((price - bb_lower) / band_range) * 100 if (band_range > 0 and bb_lower) else 50
        
        # Adjust thresholds based on mode (read from trading config)
        max_bb_position = self.config.regime.aggressive_bb_pct if self.config.regime.aggressive_mode else self.config.regime.conservative_bb_pct
        max_rsi = self.config.regime.aggressive_rsi_max if self.config.regime.aggressive_mode else self.config.regime.conservative_rsi_max
        
        # Long signal: price in lower X% of band
        if price_position_pct <= max_bb_position:
            # Check RSI
            if rsi and rsi < max_rsi:
                # Confidence based on how close to lower band
                base_confidence = 0.5
                if price_position_pct <= 20:  # Very close to lower band
                    base_confidence = 0.65
                if bb_lower and price <= bb_lower:  # At or below lower band
                    base_confidence = 0.75
                
                # HTF bonus
                confidence = base_confidence
                if htf.htf_aligned and htf.dominant_trend == 'up':
                    confidence = min(0.85, base_confidence + 0.15)
                
                # Calculate TP to ensure minimum 1.5 R:R
                # For aggressive range trading, target upper BB or minimum distance based on SL
                stop_loss_price = (bb_lower * 0.995) if bb_lower else (price * 0.98)
                stop_distance = abs(price - stop_loss_price)
                min_tp_distance = stop_distance * 1.5  # Minimum 1.5 R:R
                
                # Choose TP: upper BB or price + min distance, whichever is closer
                tp_upper_bb = bb_upper if bb_upper else (price * 1.03)
                tp_min_rr = price + min_tp_distance
                take_profit_price = min(tp_upper_bb, tp_min_rr) if tp_min_rr < tp_upper_bb else tp_upper_bb
                
                return TradeSignal(
                    action='long',
                    regime=regime_result.regime,
                    confidence=confidence,
                    reason=f"RANGE entry: price at {price_position_pct:.0f}% of band (price={price:.2f}, BB=[{bb_lower:.2f}, {bb_upper:.2f}]), RSI={rsi:.1f}",
                    entry_price=price,
                    stop_loss=stop_loss_price,
                    take_profit=take_profit_price,
                    position_size_multiplier=confidence,
                    htf_aligned=htf.htf_aligned,
                    dominant_trend=htf.dominant_trend,
                    symbol=symbol
                )
        
        # Approaching middle band from below (momentum play)
        # Use same aggressive mode threshold for momentum plays
        momentum_threshold = 60 if self.config.regime.aggressive_mode else 50
        if max_bb_position < price_position_pct <= momentum_threshold and rsi and rsi < (max_rsi + 5):
            # Only if HTF is bullish (safer mid-band entries)
            if htf.dominant_trend == 'up':
                confidence = 0.45  # Lower confidence for mid-band entries
                
                return TradeSignal(
                    action='long',
                    regime=regime_result.regime,
                    confidence=confidence,
                    reason=f"RANGE momentum: mid-band entry with HTF support (price at {price_position_pct:.0f}%, RSI={rsi:.1f})",
                    entry_price=price,
                    stop_loss=(bb_lower * 0.995) if bb_lower else (price * 0.98),
                    take_profit=(bb_middle * 1.01) if bb_middle else (price * 1.02),
                    position_size_multiplier=confidence,
                    htf_aligned=htf.htf_aligned,
                    dominant_trend=htf.dominant_trend,
                    symbol=symbol
                )
        
        # No signal - price too high in range or RSI not favorable
        rsi_display = f"{rsi:.1f}" if rsi else "N/A"
        return TradeSignal(
            action='hold',
            regime=regime_result.regime,
            confidence=0.0,
            reason=f"RANGE but no setup (price at {price_position_pct:.0f}% of band, RSI={rsi_display})",
            entry_price=price,
            htf_aligned=htf.htf_aligned,
            dominant_trend=htf.dominant_trend,
            symbol=symbol
        )
    
    def _breakout_strategy(
        self,
        symbol: str,
        price: float,
        ohlcv_5m: list,
        indicators_5m: Dict[str, Any],
        regime_result: Any,
        htf: HTFContext
    ) -> TradeSignal:
        """
        Breakout continuation strategy for BREAKOUT_EXPANSION regime.
        
        SPOT TRADING ONLY:
        - Trade UPSIDE breakouts only (long positions)
        - Ignore downside breakouts (no short selling in spot)
        - Require volume confirmation
        - HTF filter: Skip if strongly bearish
        - Wider stops to avoid shakeouts
        """
        # Determine breakout direction from regime signals
        signals = regime_result.signals
        broke_above = signals.get('broke_above_range', False)
        broke_below = signals.get('broke_below_range', False)
        volume_spike = signals.get('volume_spike', False)
        atr = indicators_5m.get('atr')
        
        # Downside breakout: Skip (no shorts in spot)
        if broke_below and not broke_above:
            return self._no_trade_signal(symbol, price, regime_result, htf, "BREAKOUT downside - no shorts in spot trading")
        
        # Upside breakout
        if broke_above:
            # HTF filter: Skip if strongly bearish
            if htf.dominant_trend == 'down':
                return TradeSignal(
                    action='hold',
                    regime=regime_result.regime,
                    confidence=0.0,
                    reason=f"BREAKOUT upside but HTF bearish ({htf.trend_15m}/{htf.trend_1h}) - skip",
                    entry_price=price,
                    htf_aligned=htf.htf_aligned,
                    dominant_trend=htf.dominant_trend,
                    symbol=symbol
                )
            
            confidence = 0.75 if volume_spike else 0.6
            # HTF bonus
            if htf.dominant_trend == 'up':
                confidence = min(confidence + 0.1, 0.9)
            
            atr_sl_mult = self.config.indicators.atr_stop_multiplier  # Now 3.0
            atr_tp_mult = self.config.indicators.atr_take_profit_multiplier  # Now 4.0
            
            return TradeSignal(
                action='long',
                regime=regime_result.regime,
                confidence=confidence,
                reason=f"BREAKOUT_EXPANSION upside: volume_spike={volume_spike}, HTF={htf.dominant_trend}",
                entry_price=price,
                stop_loss=price - (atr_sl_mult * atr) if atr else price * 0.97,
                take_profit=price + (atr_tp_mult * atr) if atr else price * 1.06,
                position_size_multiplier=confidence,
                htf_aligned=htf.htf_aligned,
                dominant_trend=htf.dominant_trend,
                symbol=symbol
            )
        
        return self._no_trade_signal(symbol, price, regime_result, htf, "BREAKOUT but no clear upside direction")
    
    def _no_trade_signal(
        self,
        symbol: str,
        price: float,
        regime_result: Any,
        htf: HTFContext,
        reason: str = "NO_TRADE regime"
    ) -> TradeSignal:
        """Generate hold signal for NO_TRADE conditions"""
        return TradeSignal(
            action='hold',
            regime=regime_result.regime,
            confidence=0.0,
            reason=reason,
            entry_price=price,
            htf_aligned=htf.htf_aligned,
            dominant_trend=htf.dominant_trend,
            symbol=symbol
        )


# Singleton instance
_orchestrator: Optional[StrategyOrchestrator] = None


def get_orchestrator() -> StrategyOrchestrator:
    """
    Get singleton StrategyOrchestrator instance.
    
    Returns:
        StrategyOrchestrator instance
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = StrategyOrchestrator()
    return _orchestrator
