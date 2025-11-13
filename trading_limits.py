"""
trading_limits.py - Global daily trade limit enforcement

Ensures trade limits apply across BOTH paper and live modes for the current trading day.
Critical: limits do NOT reset when switching modes - only on new trading day.
"""

import json
import time
from pathlib import Path
from typing import Dict, Optional, Any
from datetime import datetime, date
from dataclasses import dataclass, field, asdict


STATE_FILE = Path(__file__).parent / "daily_limits_state.json"


@dataclass
class DailyTradeLimits:
    """
    Global daily trade limit tracker that persists across mode changes.
    
    Key principle: ONE daily limit counter that applies to BOTH paper AND live trades.
    """
    current_date: str  # ISO format YYYY-MM-DD
    total_trades_today: int = 0
    trades_by_symbol: Dict[str, int] = field(default_factory=dict)
    
    # Limits (configurable)
    max_trades_per_symbol: int = 10
    max_total_trades: int = 30
    
    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "DailyTradeLimits":
        """Deserialize from dict"""
        return cls(**data)
    
    def _check_new_day(self) -> bool:
        """Check if we're on a new trading day and reset if needed"""
        today = date.today().isoformat()
        if today != self.current_date:
            print(f"[DAILY-LIMITS] New trading day: {today} (was {self.current_date})")
            self.current_date = today
            self.total_trades_today = 0
            self.trades_by_symbol = {}
            self.save()
            return True
        return False
    
    def can_open_new_trade(
        self,
        symbol: str,
        mode: str = "live"
    ) -> tuple[bool, str]:
        """
        Check if a new trade can be opened under current limits.
        
        CRITICAL: This check applies REGARDLESS of mode (paper or live).
        Daily limits are global and shared across both modes.
        
        Args:
            symbol: Trading symbol (e.g., "BTC/USD")
            mode: Trading mode ("paper" or "live") - for logging only
            
        Returns:
            Tuple of (can_trade: bool, reason: str)
        """
        # Check for new day first
        self._check_new_day()
        
        # Check total daily limit
        if self.total_trades_today >= self.max_total_trades:
            return False, (
                f"Daily total trade limit reached ({self.total_trades_today}/{self.max_total_trades}). "
                f"Wait until tomorrow."
            )
        
        # Check per-symbol limit
        symbol_count = self.trades_by_symbol.get(symbol, 0)
        if symbol_count >= self.max_trades_per_symbol:
            return False, (
                f"Daily limit for {symbol} reached ({symbol_count}/{self.max_trades_per_symbol}). "
                f"Try different symbol or wait until tomorrow."
            )
        
        return True, "Within daily trade limits"
    
    def record_trade(self, symbol: str, mode: str = "live") -> None:
        """
        Record a new trade opening.
        
        CRITICAL: This increments counters for BOTH paper and live modes.
        
        Args:
            symbol: Trading symbol
            mode: Trading mode (for logging)
        """
        # Check for new day
        self._check_new_day()
        
        # Increment counters
        self.total_trades_today += 1
        self.trades_by_symbol[symbol] = self.trades_by_symbol.get(symbol, 0) + 1
        
        print(f"[DAILY-LIMITS] Trade recorded ({mode}): {symbol} "
              f"(symbol: {self.trades_by_symbol[symbol]}/{self.max_trades_per_symbol}, "
              f"total: {self.total_trades_today}/{self.max_total_trades})")
        
        # Persist to disk
        self.save()
    
    def save(self) -> None:
        """Persist state to JSON file"""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.to_dict(), f, indent=2)
        except Exception as e:
            print(f"[DAILY-LIMITS-ERR] Failed to save state: {e}")
    
    @classmethod
    def load(cls, max_trades_per_symbol: int = 10, max_total_trades: int = 30) -> "DailyTradeLimits":
        """
        Load state from JSON file or create new if not exists.
        
        Args:
            max_trades_per_symbol: Maximum trades per symbol per day
            max_total_trades: Maximum total trades per day
        """
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    limits = cls.from_dict(data)
                    
                    # Update limits from parameters (in case they changed via env vars)
                    limits.max_trades_per_symbol = max_trades_per_symbol
                    limits.max_total_trades = max_total_trades
                    
                    # Check if new day
                    limits._check_new_day()
                    
                    print(f"[DAILY-LIMITS] Loaded state: {limits.total_trades_today} trades today "
                          f"({limits.current_date})")
                    return limits
            except Exception as e:
                print(f"[DAILY-LIMITS-WARN] Failed to load state: {e}, creating new")
        
        # Create new state
        today = date.today().isoformat()
        limits = cls(
            current_date=today,
            max_trades_per_symbol=max_trades_per_symbol,
            max_total_trades=max_total_trades
        )
        limits.save()
        print(f"[DAILY-LIMITS] Created new state for {today}")
        return limits
    
    def get_status(self) -> Dict[str, Any]:
        """Get current limit status for reporting"""
        self._check_new_day()
        
        return {
            'date': self.current_date,
            'total_trades': self.total_trades_today,
            'max_total_trades': self.max_total_trades,
            'trades_remaining': max(0, self.max_total_trades - self.total_trades_today),
            'trades_by_symbol': dict(self.trades_by_symbol),
            'max_per_symbol': self.max_trades_per_symbol
        }


# Singleton instance
_limits_instance: Optional[DailyTradeLimits] = None


def get_daily_limits(max_trades_per_symbol: int = 10, max_total_trades: int = 30) -> DailyTradeLimits:
    """
    Get or create singleton DailyTradeLimits instance.
    
    This ensures ONE shared counter across all components (paper and live).
    """
    global _limits_instance
    if _limits_instance is None:
        _limits_instance = DailyTradeLimits.load(
            max_trades_per_symbol=max_trades_per_symbol,
            max_total_trades=max_total_trades
        )
    return _limits_instance


def can_open_new_trade(symbol: str, mode: str = "live") -> tuple[bool, str]:
    """
    Convenience function to check if new trade can be opened.
    
    Uses singleton instance, ensuring global limits across paper and live.
    """
    limits = get_daily_limits()
    return limits.can_open_new_trade(symbol, mode)


def record_trade_opened(symbol: str, mode: str = "live") -> None:
    """
    Convenience function to record a new trade.
    
    Uses singleton instance, ensuring global counter across paper and live.
    """
    limits = get_daily_limits()
    limits.record_trade(symbol, mode)
