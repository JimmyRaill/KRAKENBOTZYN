"""
trading_config.py - Centralized trading configuration

All strategy parameters, risk settings, and filters in one place.
Configuration hierarchy: defaults → environment variables → optional JSON override
"""

import os
from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class IndicatorConfig:
    """Technical indicator settings"""
    # Primary MA periods
    sma_fast: int = 20
    sma_slow: int = 50
    
    # RSI settings
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    
    # ATR settings
    atr_period: int = 14
    atr_stop_multiplier: float = 3.0  # WIDENED from 2.0 - gives trades room to breathe
    atr_take_profit_multiplier: float = 4.5  # INCREASED to 4.5 - ensures R:R >= 1.5 (4.5/3.0 = 1.5)
    
    # Fallback percentages (when ATR unavailable)
    fallback_stop_pct: float = 0.02  # 2%
    fallback_tp_pct: float = 0.03    # 3%


@dataclass
class MarketFilters:
    """Market regime and condition filters"""
    # Volatility filter
    min_atr_pct: float = 0.001  # Min ATR as % of price (0.1%)
    max_atr_spike_multiplier: float = 3.0  # Max ATR spike vs recent average
    
    # Volume filter
    min_volume_percentile: float = 30.0  # Require volume > 30th percentile
    volume_lookback: int = 20  # Candles to calculate percentile
    
    # Chop/sideways detection
    enable_chop_filter: bool = True
    chop_sma_slope_threshold: float = 0.0005  # Max slope for "flat" SMA
    chop_lookback: int = 10  # Candles to measure slope
    chop_atr_range_multiplier: float = 1.5  # Price range threshold


@dataclass
class RiskConfig:
    """Risk management parameters"""
    # Position sizing
    risk_per_trade_pct: float = 0.02  # 2% of equity per trade (jimmy's updated risk tolerance)
    max_active_risk_pct: float = 0.02   # Max 2% total risk across all positions
    max_position_size_pct: float = 0.10  # Max 10% of equity in one position
    max_position_usd: float = 50.0  # Max position size in USD (increased for 2% risk trades)
    
    # Trade limits
    max_trades_per_day: int = 10  # Per symbol
    max_total_trades_per_day: int = 30  # Across all symbols
    
    # Cooldown (in number of 5-minute candles)
    cooldown_candles_after_trade: int = 3  # 15 minutes
    cooldown_candles_after_loss: int = 6   # 30 minutes
    
    # Kill-switch
    max_daily_loss_usd: Optional[float] = None  # Set via env var or None
    
    # R:R enforcement
    min_risk_reward_ratio: float = 1.5  # Minimum 1.5R
    
    # Margin/Short Selling Controls
    enable_shorts: bool = False  # DISABLED - Margin trading not enabled on Kraken account (requires manual activation)
    max_leverage: float = 1.0  # HARD CAP at 2.0, default 1.0 (no leverage)
    max_margin_exposure_pct: float = 0.5  # Max 50% of equity in margin positions


@dataclass
class RegimeConfig:
    """Market regime detection thresholds"""
    # ADX thresholds (AGGRESSIVE: lowered for more trading opportunities)
    adx_threshold: float = 10.0  # ADX > 10 = trending market (lowered to 10.0 to match observed ADX 10.5-11.8)
    min_adx: float = 8.0  # ADX < 8 = dead market (was 10)
    
    # Volatility thresholds (AGGRESSIVE: lowered to trade quieter markets)
    min_volatility_pct: float = 0.0005  # 0.05% minimum (was 0.08%)
    atr_spike_multiplier: float = 2.5  # ATR > 2.5x recent avg = spike
    
    # Breakout detection
    breakout_margin_atr: float = 0.5  # Price must break by 0.5 ATR
    volume_spike_multiplier: float = 1.5  # Volume > 1.5x avg for breakout
    
    # Range detection (AGGRESSIVE: wider bands = more range opportunities)
    max_range_width_pct: float = 5.0  # Bollinger Band width < 5% for range (was 4%)
    
    # Volume thresholds
    min_volume: float = 0.0  # Absolute minimum volume (0 = disabled)
    
    # Bollinger Bands
    bb_period: int = 20
    bb_std_dev: float = 2.0
    
    # Aggressive range trading thresholds
    aggressive_mode: bool = True  # Enable aggressive range trading (jimmy wants more trades)
    aggressive_bb_pct: float = 55.0  # Max BB position for LONG (55% = mid-range)
    aggressive_rsi_max: float = 60.0  # Max RSI for LONG entries
    conservative_bb_pct: float = 40.0  # Conservative BB threshold
    conservative_rsi_max: float = 45.0  # Conservative RSI threshold


@dataclass
class TradingConfig:
    """Master configuration for trading system"""
    # Sub-configs
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    filters: MarketFilters = field(default_factory=MarketFilters)
    risk: RiskConfig = field(default_factory=RiskConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    
    # Trading mode
    paper_mode: bool = False
    validate_only: bool = False
    
    # Symbol and timeframe
    symbols: list[str] = field(default_factory=lambda: ["BTC/USD", "ETH/USD", "ZEC/USD"])
    timeframe: str = "5m"
    timeframe_seconds: int = 300
    
    # Execution mode
    execution_mode: str = "MARKET_ONLY"  # "MARKET_ONLY", "BRACKET", or "LIMIT_BRACKET"
    use_brackets: bool = False  # Enable bracket orders (TP/SL)
    
    # LIMIT_BRACKET mode settings (Phase 2A - maker-friendly limit entries)
    limit_offset_pct: float = 0.002       # 0.2% below market for BUY, above for SELL
    limit_timeout_seconds: int = 120      # Seconds to wait for limit order fill
    limit_max_retries: int = 3            # Max retry attempts for limit entries
    limit_fallback_to_market: bool = False  # Fallback to market order if limit times out
    
    # Fee gate settings (Phase 3A - fee-aware minimum edge filter)
    fee_gate_enabled: bool = False         # Default OFF - set FEE_GATE_ENABLED=1 to enable
    fee_safety_multiplier: float = 1.5     # Required edge = round_trip_fees * this multiplier
    
    # Phase 3B: Regime filter settings (blocks trades in bad conditions)
    regime_filter_enabled: bool = False    # Default OFF - set REGIME_FILTER_ENABLED=1 to enable
    regime_min_atr_pct: float = 0.3        # Min ATR as % of price (0.3% = normal volatility)
    regime_min_volume_usd: float = 10000.0 # Min 24h volume in USD
    regime_trend_required: bool = False    # Require clear trend (ADX above threshold)
    
    # Phase 3B: Symbol whitelist/blacklist filtering
    symbol_whitelist: Optional[list] = None   # If set, ONLY these symbols can trade (comma-separated via env)
    symbol_blacklist: Optional[list] = None   # These symbols are blocked from trading
    
    # Phase 3B: Decision statistics tracking
    decision_stats_enabled: bool = True    # Track how many trades blocked by each filter
    
    # Feature flags
    enable_profit_target: bool = False
    enable_api_watchdog: bool = False
    enable_multi_timeframe: bool = False
    
    # Backtesting
    backtest_mode: bool = False
    
    @classmethod
    def from_env(cls) -> "TradingConfig":
        """
        Load configuration from environment variables.
        
        Environment variables override defaults:
        - PAPER_MODE: 0/1
        - KRAKEN_VALIDATE_ONLY: 0/1
        - SYMBOLS: comma-separated list
        - RISK_PER_TRADE: float (0.0025 = 0.25%)
        - MAX_DAILY_LOSS_USD: float
        - MAX_TRADES_PER_DAY: int
        - ENABLE_PROFIT_TARGET: 0/1
        - ENABLE_API_WATCHDOG: 0/1
        - ENABLE_MULTI_TIMEFRAME: 0/1
        """
        config = cls()
        
        # Trading mode
        paper_env = os.getenv("PAPER_MODE", "0")
        config.paper_mode = paper_env in ("1", "true", "True", "yes")
        
        validate_env = os.getenv("KRAKEN_VALIDATE_ONLY", "0")
        config.validate_only = validate_env in ("1", "true", "True", "yes")
        
        # Symbols
        symbols_env = os.getenv("SYMBOLS", "")
        if symbols_env:
            config.symbols = [s.strip() for s in symbols_env.split(",") if s.strip()]
        
        # Risk parameters
        risk_per_trade = os.getenv("RISK_PER_TRADE")
        if risk_per_trade:
            config.risk.risk_per_trade_pct = float(risk_per_trade)
        
        max_daily_loss = os.getenv("MAX_DAILY_LOSS_USD")
        if max_daily_loss:
            try:
                # Strip whitespace and currency symbols like "$"
                cleaned = max_daily_loss.strip().lstrip('$')
                config.risk.max_daily_loss_usd = float(cleaned)
            except (ValueError, AttributeError) as e:
                print(f"[CONFIG-WARN] MAX_DAILY_LOSS_USD invalid: '{max_daily_loss}', falling back to default 50.0")
                config.risk.max_daily_loss_usd = 50.0
        
        max_trades = os.getenv("MAX_TRADES_PER_DAY")
        if max_trades:
            config.risk.max_trades_per_day = int(max_trades)
        
        max_position = os.getenv("MAX_POSITION_USD")
        if max_position:
            try:
                # Strip whitespace and currency symbols like "$"
                cleaned = max_position.strip().lstrip('$')
                config.risk.max_position_usd = float(cleaned)
            except (ValueError, AttributeError) as e:
                print(f"[CONFIG-WARN] MAX_POSITION_USD invalid: '{max_position}', falling back to default 10.0")
                config.risk.max_position_usd = 10.0
        
        # Aggressive mode configuration
        config.regime.aggressive_mode = os.getenv("AGGRESSIVE_RANGE_TRADING", "0") == "1"
        
        aggressive_bb = os.getenv("AGGRESSIVE_BB_PCT")
        if aggressive_bb:
            config.regime.aggressive_bb_pct = float(aggressive_bb)
        
        aggressive_rsi = os.getenv("AGGRESSIVE_RSI_MAX")
        if aggressive_rsi:
            config.regime.aggressive_rsi_max = float(aggressive_rsi)
        
        # Execution mode
        # Supported values: MARKET_ONLY (default), BRACKET, LIMIT_BRACKET
        execution_mode_env = os.getenv("EXECUTION_MODE", "MARKET_ONLY")
        valid_modes = ("MARKET_ONLY", "BRACKET", "LIMIT_BRACKET")
        config.execution_mode = execution_mode_env if execution_mode_env in valid_modes else "MARKET_ONLY"
        config.use_brackets = os.getenv("USE_BRACKETS", "0") == "1" or config.execution_mode in ("BRACKET", "LIMIT_BRACKET")
        
        # LIMIT_BRACKET mode settings (Phase 2A)
        limit_offset_env = os.getenv("LIMIT_OFFSET_PCT")
        if limit_offset_env:
            try:
                config.limit_offset_pct = float(limit_offset_env)
            except (ValueError, TypeError):
                print(f"[CONFIG-WARN] LIMIT_OFFSET_PCT invalid: '{limit_offset_env}', using default 0.002")
        
        limit_timeout_env = os.getenv("LIMIT_TIMEOUT_SECONDS")
        if limit_timeout_env:
            try:
                config.limit_timeout_seconds = int(limit_timeout_env)
            except (ValueError, TypeError):
                print(f"[CONFIG-WARN] LIMIT_TIMEOUT_SECONDS invalid: '{limit_timeout_env}', using default 120")
        
        limit_retries_env = os.getenv("LIMIT_MAX_RETRIES")
        if limit_retries_env:
            try:
                config.limit_max_retries = int(limit_retries_env)
            except (ValueError, TypeError):
                print(f"[CONFIG-WARN] LIMIT_MAX_RETRIES invalid: '{limit_retries_env}', using default 3")
        
        config.limit_fallback_to_market = os.getenv("LIMIT_FALLBACK_TO_MARKET", "0") == "1"
        
        # Fee gate settings (Phase 3A)
        config.fee_gate_enabled = os.getenv("FEE_GATE_ENABLED", "0") == "1"
        
        fee_multiplier_env = os.getenv("FEE_SAFETY_MULTIPLIER")
        if fee_multiplier_env:
            try:
                config.fee_safety_multiplier = float(fee_multiplier_env)
            except (ValueError, TypeError):
                print(f"[CONFIG-WARN] FEE_SAFETY_MULTIPLIER invalid: '{fee_multiplier_env}', using default 1.5")
        
        # Phase 3B: Regime filter settings
        config.regime_filter_enabled = os.getenv("REGIME_FILTER_ENABLED", "0") == "1"
        
        regime_atr_env = os.getenv("REGIME_MIN_ATR_PCT")
        if regime_atr_env:
            try:
                config.regime_min_atr_pct = float(regime_atr_env)
            except (ValueError, TypeError):
                print(f"[CONFIG-WARN] REGIME_MIN_ATR_PCT invalid: '{regime_atr_env}', using default 0.3")
        
        regime_vol_env = os.getenv("REGIME_MIN_VOLUME_USD")
        if regime_vol_env:
            try:
                config.regime_min_volume_usd = float(regime_vol_env)
            except (ValueError, TypeError):
                print(f"[CONFIG-WARN] REGIME_MIN_VOLUME_USD invalid: '{regime_vol_env}', using default 10000.0")
        
        config.regime_trend_required = os.getenv("REGIME_TREND_REQUIRED", "0") == "1"
        
        # Phase 3B: Symbol whitelist/blacklist
        whitelist_env = os.getenv("SYMBOL_WHITELIST", "")
        if whitelist_env.strip():
            config.symbol_whitelist = [s.strip().upper() for s in whitelist_env.split(",") if s.strip()]
        
        blacklist_env = os.getenv("SYMBOL_BLACKLIST", "")
        if blacklist_env.strip():
            config.symbol_blacklist = [s.strip().upper() for s in blacklist_env.split(",") if s.strip()]
        
        # Phase 3B: Decision stats
        config.decision_stats_enabled = os.getenv("DECISION_STATS_ENABLED", "1") == "1"
        
        # Feature flags
        config.enable_profit_target = os.getenv("ENABLE_PROFIT_TARGET", "0") == "1"
        config.enable_api_watchdog = os.getenv("ENABLE_API_WATCHDOG", "0") == "1"
        config.enable_multi_timeframe = os.getenv("ENABLE_MULTI_TIMEFRAME", "0") == "1"
        
        # Margin/Short Selling Configuration
        enable_shorts_env = os.getenv("ENABLE_SHORTS", "false")  # Default OFF - margin trading not enabled on Kraken account
        config.risk.enable_shorts = enable_shorts_env in ("1", "true", "True", "yes")
        
        max_leverage_env = os.getenv("MAX_LEVERAGE", "1.0")
        try:
            leverage_value = float(max_leverage_env)
            # HARD CAP: Never allow leverage > 2.0, even if misconfigured
            if leverage_value > 2.0:
                print(f"[CONFIG-WARN] MAX_LEVERAGE={leverage_value} exceeds hard cap of 2.0, clamping to 2.0")
                leverage_value = 2.0
            elif leverage_value < 1.0:
                print(f"[CONFIG-WARN] MAX_LEVERAGE={leverage_value} below minimum 1.0, setting to 1.0")
                leverage_value = 1.0
            config.risk.max_leverage = leverage_value
        except (ValueError, AttributeError) as e:
            print(f"[CONFIG-WARN] MAX_LEVERAGE invalid: '{max_leverage_env}', falling back to default 1.0")
            config.risk.max_leverage = 1.0
        
        max_margin_exposure_env = os.getenv("MAX_MARGIN_EXPOSURE_PCT", "0.5")
        try:
            exposure = float(max_margin_exposure_env)
            if exposure < 0 or exposure > 1.0:
                print(f"[CONFIG-WARN] MAX_MARGIN_EXPOSURE_PCT={exposure} out of range [0, 1.0], clamping to 0.5")
                exposure = 0.5
            config.risk.max_margin_exposure_pct = exposure
        except (ValueError, AttributeError) as e:
            print(f"[CONFIG-WARN] MAX_MARGIN_EXPOSURE_PCT invalid: '{max_margin_exposure_env}', falling back to default 0.5")
            config.risk.max_margin_exposure_pct = 0.5
        
        return config
    
    def to_dict(self) -> dict:
        """Export config as dict for logging/debugging"""
        return {
            "mode": "paper" if self.paper_mode else "live",
            "validate_only": self.validate_only,
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "risk_per_trade_pct": self.risk.risk_per_trade_pct,
            "max_trades_per_day": self.risk.max_trades_per_day,
            "max_daily_loss_usd": self.risk.max_daily_loss_usd,
            "min_rr": self.risk.min_risk_reward_ratio,
            "features": {
                "profit_target": self.enable_profit_target,
                "api_watchdog": self.enable_api_watchdog,
                "multi_timeframe": self.enable_multi_timeframe
            }
        }
    
    def __str__(self) -> str:
        """Human-readable config summary"""
        mode = "PAPER" if self.paper_mode else "LIVE"
        return (
            f"TradingConfig({mode} mode, {len(self.symbols)} symbols, "
            f"{self.risk.risk_per_trade_pct*100:.2f}% risk/trade, "
            f"max {self.risk.max_trades_per_day} trades/day)"
        )


# Singleton instance
_config: Optional[TradingConfig] = None


def get_config() -> TradingConfig:
    """Get or create singleton config instance"""
    global _config
    if _config is None:
        _config = TradingConfig.from_env()
        print(f"[CONFIG] {_config}")
    return _config


def reload_config() -> TradingConfig:
    """Force reload config from environment"""
    global _config
    _config = TradingConfig.from_env()
    print(f"[CONFIG] Reloaded: {_config}")
    return _config


ZIN_VERSION = os.getenv("ZIN_VERSION", "ZIN_V1_DATA_2025-12")


def get_zin_version() -> str:
    """Get the current Zin version string for data logging."""
    return ZIN_VERSION


def get_config_for_logging() -> dict:
    """Get a dict of key config values suitable for logging to data vault."""
    cfg = get_config()
    return {
        "symbols": cfg.symbols,
        "paper_mode": cfg.paper_mode,
        "execution_mode": cfg.execution_mode,
        "risk_per_trade_pct": cfg.risk.risk_per_trade_pct,
        "max_active_risk_pct": cfg.risk.max_active_risk_pct,
        "max_position_usd": cfg.risk.max_position_usd,
        "max_trades_per_day": cfg.risk.max_trades_per_day,
        "max_daily_loss_usd": cfg.risk.max_daily_loss_usd,
        "min_rr": cfg.risk.min_risk_reward_ratio,
        "enable_shorts": cfg.risk.enable_shorts,
        "atr_stop_multiplier": cfg.indicators.atr_stop_multiplier,
        "atr_tp_multiplier": cfg.indicators.atr_take_profit_multiplier,
        "adx_threshold": cfg.regime.adx_threshold,
        "aggressive_mode": cfg.regime.aggressive_mode
    }
