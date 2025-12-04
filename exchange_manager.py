# exchange_manager.py - Centralized exchange singleton for paper/live mode safety

import os
from typing import Optional
import ccxt
from dotenv import load_dotenv
from pathlib import Path
from paper_exchange_wrapper import PaperExchangeWrapper

# Load environment (override=False so we don't stomp on os.environ set by safety checks)
ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=str(ENV_PATH), override=False)


class ExchangeManager:
    """
    Singleton exchange manager that ensures consistent paper/live mode across ALL modules.
    CRITICAL: This prevents the bug where autopilot.py and commands.py had separate exchange
    objects with potentially different validate flags.
    """
    _instance: Optional['ExchangeManager'] = None
    _exchange: Optional[ccxt.kraken] = None
    _validate_mode: bool = True
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        # Only initialize once
        if not self._initialized:
            self._reload_config()
            self._initialized = True
    
    def _reload_config(self):
        """Reload configuration from environment and reinitialize exchange"""
        # Load .env WITHOUT override - this respects any os.environ[] set by safety checks
        # CRITICAL: Safety checks in main.py/autopilot.py may set KRAKEN_VALIDATE_ONLY=1 
        # in os.environ, and we must NOT let the .env file override that.
        load_dotenv(dotenv_path=str(ENV_PATH), override=False)
        
        # Read validate mode from environment (os.environ takes precedence over .env)
        # DEFAULT IS "0" (LIVE MODE) - This matches main.py's default
        # Reserved VM should trade live unless explicitly set to validate-only
        # Dev workspace safety is handled by instance_guard.should_allow_live_trading()
        validate_str = os.getenv("KRAKEN_VALIDATE_ONLY", "0").strip().lower()
        self._validate_mode = validate_str in ("1", "true", "yes", "on")
        
        # Create exchange with validate flag
        api_key = os.getenv("KRAKEN_API_KEY", "")
        api_secret = os.getenv("KRAKEN_API_SECRET", "")
        
        config = {
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"validate": self._validate_mode},
            "nonce": lambda: ccxt.Exchange.milliseconds()
        }
        
        ccxt_exchange = ccxt.kraken(config)  # type: ignore[arg-type]
        
        # Load markets
        try:
            ccxt_exchange.load_markets()
        except Exception as e:
            print(f"[EXCHANGE-MANAGER] Warning: Failed to load markets: {e}")
        
        # Wrap with PaperExchangeWrapper in paper mode
        if self._validate_mode:
            self._exchange = PaperExchangeWrapper(ccxt_exchange, is_paper_mode=True)
            print("[EXCHANGE-MANAGER] Initialized in PAPER TRADING mode (with paper wrapper)")
        else:
            self._exchange = PaperExchangeWrapper(ccxt_exchange, is_paper_mode=False)
            print("[EXCHANGE-MANAGER] Initialized in LIVE TRADING mode (wrapper pass-through)")
    
    def get_exchange(self) -> ccxt.kraken:
        """Get the exchange instance"""
        if self._exchange is None:
            raise RuntimeError("ExchangeManager not initialized")
        return self._exchange
    
    def is_paper_mode(self) -> bool:
        """Check if in paper trading mode"""
        return self._validate_mode
    
    def is_live_mode(self) -> bool:
        """Check if in live trading mode"""
        return not self._validate_mode
    
    def get_mode_str(self) -> str:
        """Get human-readable mode string"""
        return "paper" if self._validate_mode else "live"
    
    def set_mode(self, paper_mode: bool, skip_reload_env: bool = True) -> None:
        """
        Change trading mode and reinitialize exchange.
        WARNING: This should only be called by the mode controller!
        
        Args:
            paper_mode: True for paper trading, False for live
            skip_reload_env: If True, don't reload .env (caller already updated it)
        """
        old_mode = self._validate_mode
        self._validate_mode = paper_mode
        
        # Update environment variable in memory
        os.environ["KRAKEN_VALIDATE_ONLY"] = "1" if paper_mode else "0"
        
        # Create exchange with new validate flag (DON'T reload .env)
        api_key = os.getenv("KRAKEN_API_KEY", "")
        api_secret = os.getenv("KRAKEN_API_SECRET", "")
        
        config = {
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"validate": self._validate_mode},
            "nonce": lambda: ccxt.Exchange.milliseconds()
        }
        
        ccxt_exchange = ccxt.kraken(config)  # type: ignore[arg-type]
        
        # Load markets
        try:
            ccxt_exchange.load_markets()
        except Exception as e:
            print(f"[EXCHANGE-MANAGER] Warning: Failed to load markets: {e}")
        
        # Wrap with PaperExchangeWrapper
        self._exchange = PaperExchangeWrapper(ccxt_exchange, is_paper_mode=paper_mode)
        
        mode_str = "PAPER" if paper_mode else "LIVE"
        old_str = "PAPER" if old_mode else "LIVE"
        print(f"[EXCHANGE-MANAGER] Mode changed: {old_str} -> {mode_str} (wrapper enabled)")
    
    def validate_order_allowed(self, operation: str = "trade") -> tuple[bool, str]:
        """
        Validate if an order operation is allowed.
        Returns (allowed, reason)
        """
        if self._validate_mode:
            return True, f"{operation} allowed (paper mode)"
        else:
            return True, f"{operation} allowed (live mode - REAL MONEY)"
    
    def fetch_ohlc(self, symbol: str, timeframe: str = "5m", limit: int = 100):
        """
        Fetch OHLC (candlestick) data from Kraken.
        
        Args:
            symbol: Trading pair (e.g., 'BTC/USD', 'ETH/USD')
            timeframe: Candle timeframe ('1m', '5m', '15m', '1h', '4h', '1d')
            limit: Number of candles to fetch (default: 100)
        
        Returns:
            List of OHLC candles: [[timestamp, open, high, low, close, volume], ...]
        
        Notes:
            - Kraken API rate limits: ~1 call per second (public), ~15-20/min (private)
            - This method fetches from public API (no auth required)
            - Fetching once per 5-minute interval stays 99% under rate limits
            - Minimum limit recommended: 100 (covers SMA20 + ATR14 + buffer)
        """
        if self._exchange is None:
            raise RuntimeError("ExchangeManager not initialized")
        
        # Validate inputs
        valid_timeframes = ['1m', '5m', '15m', '1h', '4h', '1d']
        if timeframe not in valid_timeframes:
            raise ValueError(f"Invalid timeframe '{timeframe}'. Must be one of: {valid_timeframes}")
        
        if limit < 20:
            raise ValueError(f"Limit must be >= 20 for indicator calculations (got {limit})")
        
        # Fetch OHLC data from Kraken
        return self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    
    def reload(self):
        """Force reload configuration from .env"""
        self._reload_config()


# Global singleton instance
_manager = ExchangeManager()


def get_exchange() -> ccxt.kraken:
    """Get the global exchange instance"""
    return _manager.get_exchange()


def is_paper_mode() -> bool:
    """Check if in paper trading mode"""
    return _manager.is_paper_mode()


def is_live_mode() -> bool:
    """Check if in live trading mode"""
    return _manager.is_live_mode()


def get_mode_str() -> str:
    """Get current trading mode as string"""
    return _manager.get_mode_str()


def set_trading_mode(paper_mode: bool) -> None:
    """Set trading mode (paper=True, live=False)"""
    _manager.set_mode(paper_mode)


def reload_exchange_config() -> None:
    """Reload exchange configuration from .env"""
    _manager.reload()


def get_manager() -> ExchangeManager:
    """Get the ExchangeManager singleton instance"""
    return _manager
