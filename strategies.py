# strategies.py - Multi-strategy trading system with automatic selection
from __future__ import annotations

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
import numpy as np


class MarketRegime(Enum):
    """Market regime classification."""
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    VOLATILE = "volatile"
    UNKNOWN = "unknown"


class StrategyType(Enum):
    """Available trading strategies."""
    MOMENTUM = "momentum"  # Trend-following
    MEAN_REVERSION = "mean_reversion"  # Range-bound trading
    BREAKOUT = "breakout"  # Volatility breakout
    SMA_CROSSOVER = "sma_crossover"  # Classic SMA strategy (current default)


@dataclass
class Signal:
    """Trading signal from a strategy."""
    action: str  # 'buy', 'sell', 'hold'
    confidence: float  # 0.0 to 1.0
    reason: str
    strategy: StrategyType
    metadata: Dict[str, Any]


def detect_market_regime(
    prices: List[float],
    sma: float,
    atr: float,
    lookback: int = 20
) -> MarketRegime:
    """
    Detect current market regime based on price action and volatility.
    
    Args:
        prices: Recent price history (oldest to newest)
        sma: Current simple moving average
        atr: Current average true range
        lookback: Number of periods to analyze
        
    Returns:
        Detected market regime
    """
    if len(prices) < lookback:
        return MarketRegime.UNKNOWN
    
    recent_prices = prices[-lookback:]
    current_price = prices[-1]
    
    # Calculate trend strength
    price_change = (current_price - recent_prices[0]) / recent_prices[0]
    sma_distance = abs((current_price - sma) / sma)
    
    # Calculate volatility (normalized ATR)
    volatility = atr / current_price if current_price > 0 else 0
    
    # Regime detection logic
    if volatility > 0.05:  # High volatility (>5%)
        return MarketRegime.VOLATILE
    elif price_change > 0.05 and current_price > sma:  # Strong uptrend
        return MarketRegime.BULL
    elif price_change < -0.05 and current_price < sma:  # Strong downtrend
        return MarketRegime.BEAR
    elif sma_distance < 0.02:  # Tight range around SMA
        return MarketRegime.SIDEWAYS
    else:
        return MarketRegime.UNKNOWN


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculate Relative Strength Index.
    
    Args:
        prices: Price history
        period: RSI period (default 14)
        
    Returns:
        RSI value (0-100)
    """
    if len(prices) < period + 1:
        return 50.0  # Neutral
    
    deltas = np.diff(prices[-period-1:])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return float(rsi)


def momentum_strategy(
    current_price: float,
    prices: List[float],
    sma: float,
    atr: float,
    **kwargs
) -> Signal:
    """
    Momentum/trend-following strategy.
    Buys when price is trending up strongly, sells when momentum reverses.
    
    Best for: Bull markets, strong trends
    """
    if len(prices) < 20:
        return Signal("hold", 0.0, "Insufficient data", StrategyType.MOMENTUM, {})
    
    # Calculate momentum indicators
    rsi = calculate_rsi(prices)
    price_change_pct = ((current_price - prices[-10]) / prices[-10]) * 100
    
    # Strong upward momentum
    if current_price > sma and rsi > 50 and price_change_pct > 2:
        confidence = min(0.9, (price_change_pct / 10) * 0.7 + (rsi - 50) / 50 * 0.3)
        return Signal(
            "buy",
            confidence,
            f"Strong momentum: {price_change_pct:.2f}% gain, RSI {rsi:.1f}",
            StrategyType.MOMENTUM,
            {"rsi": rsi, "momentum_pct": price_change_pct}
        )
    
    # Momentum reversal - exit
    elif rsi < 40 or price_change_pct < -2:
        confidence = min(0.9, abs(price_change_pct) / 10 * 0.6 + (50 - rsi) / 50 * 0.4)
        return Signal(
            "sell",
            confidence,
            f"Momentum reversal: {price_change_pct:.2f}% drop, RSI {rsi:.1f}",
            StrategyType.MOMENTUM,
            {"rsi": rsi, "momentum_pct": price_change_pct}
        )
    
    return Signal("hold", 0.5, "Neutral momentum", StrategyType.MOMENTUM, {"rsi": rsi})


def mean_reversion_strategy(
    current_price: float,
    prices: List[float],
    sma: float,
    atr: float,
    **kwargs
) -> Signal:
    """
    Mean reversion strategy.
    Buys oversold (below SMA), sells overbought (above SMA).
    
    Best for: Sideways/ranging markets
    """
    if not sma or sma == 0:
        return Signal("hold", 0.0, "No SMA data", StrategyType.MEAN_REVERSION, {})
    
    # Calculate distance from mean
    distance_pct = ((current_price - sma) / sma) * 100
    rsi = calculate_rsi(prices) if len(prices) >= 15 else 50
    
    # Oversold - buy opportunity
    if distance_pct < -2 and rsi < 35:
        confidence = min(0.9, abs(distance_pct) / 5 * 0.6 + (35 - rsi) / 35 * 0.4)
        return Signal(
            "buy",
            confidence,
            f"Oversold: {distance_pct:.2f}% below SMA, RSI {rsi:.1f}",
            StrategyType.MEAN_REVERSION,
            {"distance_pct": distance_pct, "rsi": rsi}
        )
    
    # Overbought - sell opportunity
    elif distance_pct > 2 and rsi > 65:
        confidence = min(0.9, distance_pct / 5 * 0.6 + (rsi - 65) / 35 * 0.4)
        return Signal(
            "sell",
            confidence,
            f"Overbought: {distance_pct:.2f}% above SMA, RSI {rsi:.1f}",
            StrategyType.MEAN_REVERSION,
            {"distance_pct": distance_pct, "rsi": rsi}
        )
    
    return Signal("hold", 0.4, "Within normal range", StrategyType.MEAN_REVERSION, {})


def breakout_strategy(
    current_price: float,
    prices: List[float],
    sma: float,
    atr: float,
    **kwargs
) -> Signal:
    """
    Volatility breakout strategy.
    Buys when price breaks above recent high with volume/volatility.
    
    Best for: Volatile markets, consolidation breakouts
    """
    if len(prices) < 20:
        return Signal("hold", 0.0, "Insufficient data", StrategyType.BREAKOUT, {})
    
    # Calculate breakout levels
    recent_high = max(prices[-20:])
    recent_low = min(prices[-20:])
    price_range = recent_high - recent_low
    
    # Normalize ATR
    atr_pct = (atr / current_price) * 100 if current_price > 0 else 0
    
    # Upward breakout
    breakout_above = ((current_price - recent_high) / recent_high) * 100
    if breakout_above > 0.5 and atr_pct > 2:
        confidence = min(0.9, breakout_above / 3 * 0.7 + (atr_pct - 2) / 5 * 0.3)
        return Signal(
            "buy",
            confidence,
            f"Breakout: {breakout_above:.2f}% above high, ATR {atr_pct:.2f}%",
            StrategyType.BREAKOUT,
            {"breakout_pct": breakout_above, "atr_pct": atr_pct}
        )
    
    # Downward breakdown
    breakdown_below = ((recent_low - current_price) / recent_low) * 100
    if breakdown_below > 0.5 and atr_pct > 2:
        confidence = min(0.9, breakdown_below / 3 * 0.7 + (atr_pct - 2) / 5 * 0.3)
        return Signal(
            "sell",
            confidence,
            f"Breakdown: {breakdown_below:.2f}% below low, ATR {atr_pct:.2f}%",
            StrategyType.BREAKOUT,
            {"breakdown_pct": breakdown_below, "atr_pct": atr_pct}
        )
    
    return Signal("hold", 0.3, "No breakout detected", StrategyType.BREAKOUT, {})


def sma_crossover_strategy(
    current_price: float,
    prices: List[float],
    sma: float,
    atr: float,
    edge_pct: float = 0.0,
    **kwargs
) -> Signal:
    """
    Classic SMA crossover strategy (current autopilot default).
    Buys when price crosses above SMA + edge, sells when crosses below SMA - edge.
    
    Best for: Trending markets with clear direction
    """
    if not sma or sma == 0:
        return Signal("hold", 0.0, "No SMA data", StrategyType.SMA_CROSSOVER, {})
    
    distance_pct = ((current_price - sma) / sma) * 100
    
    # Buy signal
    if edge_pct > 0:
        confidence = min(0.9, edge_pct / 2)
        return Signal(
            "buy",
            confidence,
            f"Above SMA+edge: {edge_pct:.2f}%",
            StrategyType.SMA_CROSSOVER,
            {"edge_pct": edge_pct, "distance_pct": distance_pct}
        )
    
    # Sell signal
    elif edge_pct < 0:
        confidence = min(0.9, abs(edge_pct) / 2)
        return Signal(
            "sell",
            confidence,
            f"Below SMA-edge: {edge_pct:.2f}%",
            StrategyType.SMA_CROSSOVER,
            {"edge_pct": edge_pct, "distance_pct": distance_pct}
        )
    
    return Signal("hold", 0.5, f"Edge {edge_pct:.2f}%", StrategyType.SMA_CROSSOVER, {})


def select_best_strategy(
    regime: MarketRegime,
    enable_auto_selection: bool = True
) -> StrategyType:
    """
    Select the best strategy based on market regime.
    
    Args:
        regime: Detected market regime
        enable_auto_selection: If False, always use SMA_CROSSOVER
        
    Returns:
        Recommended strategy type
    """
    if not enable_auto_selection:
        return StrategyType.SMA_CROSSOVER
    
    strategy_map = {
        MarketRegime.BULL: StrategyType.MOMENTUM,
        MarketRegime.BEAR: StrategyType.MEAN_REVERSION,
        MarketRegime.SIDEWAYS: StrategyType.MEAN_REVERSION,
        MarketRegime.VOLATILE: StrategyType.BREAKOUT,
        MarketRegime.UNKNOWN: StrategyType.SMA_CROSSOVER,
    }
    
    return strategy_map.get(regime, StrategyType.SMA_CROSSOVER)


def execute_strategy(
    strategy_type: StrategyType,
    current_price: float,
    prices: List[float],
    sma: float,
    atr: float,
    **kwargs
) -> Signal:
    """
    Execute a specific trading strategy.
    
    Args:
        strategy_type: Strategy to execute
        current_price: Current market price
        prices: Historical prices
        sma: Simple moving average
        atr: Average true range
        **kwargs: Additional strategy parameters
        
    Returns:
        Trading signal
    """
    strategies = {
        StrategyType.MOMENTUM: momentum_strategy,
        StrategyType.MEAN_REVERSION: mean_reversion_strategy,
        StrategyType.BREAKOUT: breakout_strategy,
        StrategyType.SMA_CROSSOVER: sma_crossover_strategy,
    }
    
    strategy_func = strategies.get(strategy_type, sma_crossover_strategy)
    return strategy_func(current_price, prices, sma, atr, **kwargs)


def get_multi_strategy_consensus(
    current_price: float,
    prices: List[float],
    sma: float,
    atr: float,
    **kwargs
) -> Tuple[Signal, List[Signal]]:
    """
    Get signals from all strategies and return consensus + individual signals.
    
    Returns:
        Tuple of (consensus_signal, all_signals)
    """
    all_signals = []
    
    # Execute all strategies
    for strategy_type in StrategyType:
        signal = execute_strategy(strategy_type, current_price, prices, sma, atr, **kwargs)
        all_signals.append(signal)
    
    # Calculate consensus
    buy_votes = sum(1 for s in all_signals if s.action == "buy")
    sell_votes = sum(1 for s in all_signals if s.action == "sell")
    total_confidence = sum(s.confidence for s in all_signals if s.action != "hold")
    
    if buy_votes > sell_votes and buy_votes >= 2:
        avg_confidence = total_confidence / buy_votes if buy_votes > 0 else 0.5
        consensus = Signal(
            "buy",
            avg_confidence,
            f"Consensus BUY ({buy_votes}/{len(all_signals)} strategies agree)",
            StrategyType.SMA_CROSSOVER,  # Use default as consensus type
            {"buy_votes": buy_votes, "sell_votes": sell_votes}
        )
    elif sell_votes > buy_votes and sell_votes >= 2:
        avg_confidence = total_confidence / sell_votes if sell_votes > 0 else 0.5
        consensus = Signal(
            "sell",
            avg_confidence,
            f"Consensus SELL ({sell_votes}/{len(all_signals)} strategies agree)",
            StrategyType.SMA_CROSSOVER,
            {"buy_votes": buy_votes, "sell_votes": sell_votes}
        )
    else:
        consensus = Signal(
            "hold",
            0.5,
            f"No consensus ({buy_votes}B/{sell_votes}S)",
            StrategyType.SMA_CROSSOVER,
            {"buy_votes": buy_votes, "sell_votes": sell_votes}
        )
    
    return consensus, all_signals
