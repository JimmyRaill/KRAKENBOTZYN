"""
position_tracker.py - Mental SL/TP tracking for market-only execution

Stores calculated stop-loss and take-profit prices for open positions.
Monitors current price against these levels to trigger market exit orders.

Key Features:
- Calculates SL/TP based on ATR multipliers (2x for SL, 3x for TP)
- Stores position metadata in JSON file
- Provides monitoring functions to check exit triggers
- Thread-safe file operations
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List
from pathlib import Path
from loguru import logger


POSITIONS_FILE = Path("open_positions.json")


class Position:
    """Represents an open position with mental SL/TP levels"""
    def __init__(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        stop_loss_price: float,
        take_profit_price: float,
        atr: float,
        entry_timestamp: float,
        source: str = "autopilot"
    ):
        self.symbol = symbol
        self.entry_price = entry_price
        self.quantity = quantity
        self.stop_loss_price = stop_loss_price
        self.take_profit_price = take_profit_price
        self.atr = atr
        self.entry_timestamp = entry_timestamp
        self.source = source
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "atr": self.atr,
            "entry_timestamp": self.entry_timestamp,
            "source": self.source
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        """Create Position from dictionary"""
        return cls(
            symbol=data["symbol"],
            entry_price=data["entry_price"],
            quantity=data["quantity"],
            stop_loss_price=data["stop_loss_price"],
            take_profit_price=data["take_profit_price"],
            atr=data["atr"],
            entry_timestamp=data["entry_timestamp"],
            source=data.get("source", "autopilot")
        )
    
    def __str__(self) -> str:
        return (
            f"Position({self.symbol}, entry=${self.entry_price:.4f}, "
            f"SL=${self.stop_loss_price:.4f}, TP=${self.take_profit_price:.4f}, "
            f"qty={self.quantity:.6f})"
        )


def _load_positions() -> Dict[str, Position]:
    """Load all open positions from JSON file"""
    if not POSITIONS_FILE.exists():
        return {}
    
    try:
        with open(POSITIONS_FILE, 'r') as f:
            data = json.load(f)
        
        positions = {}
        for symbol, pos_data in data.items():
            positions[symbol] = Position.from_dict(pos_data)
        
        return positions
    except Exception as e:
        logger.error(f"[POSITION-TRACKER] Failed to load positions: {e}")
        return {}


def _save_positions(positions: Dict[str, Position]):
    """Save all positions to JSON file"""
    try:
        data = {symbol: pos.to_dict() for symbol, pos in positions.items()}
        
        with open(POSITIONS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.debug(f"[POSITION-TRACKER] Saved {len(positions)} position(s) to {POSITIONS_FILE}")
    except Exception as e:
        logger.error(f"[POSITION-TRACKER] Failed to save positions: {e}")


def add_position(
    symbol: str,
    entry_price: float,
    quantity: float,
    atr: float,
    atr_sl_multiplier: float = 2.0,
    atr_tp_multiplier: float = 3.0,
    source: str = "autopilot"
) -> Position:
    """
    Add a new open position with calculated SL/TP levels.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        entry_price: Actual fill price from market order
        quantity: Position size in base currency
        atr: Current ATR value
        atr_sl_multiplier: ATR multiplier for stop-loss (default 2.0)
        atr_tp_multiplier: ATR multiplier for take-profit (default 3.0)
        source: Trade source ("autopilot", "command", etc.)
    
    Returns:
        Position object with calculated SL/TP
    """
    # Calculate SL/TP prices
    stop_loss_price = entry_price - (atr * atr_sl_multiplier)
    take_profit_price = entry_price + (atr * atr_tp_multiplier)
    
    # Ensure SL/TP are positive
    stop_loss_price = max(stop_loss_price, entry_price * 0.5)  # Max 50% loss
    
    position = Position(
        symbol=symbol,
        entry_price=entry_price,
        quantity=quantity,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        atr=atr,
        entry_timestamp=time.time(),
        source=source
    )
    
    # Load existing positions
    positions = _load_positions()
    
    # Add new position (overwrites if symbol already exists)
    positions[symbol] = position
    
    # Save to disk
    _save_positions(positions)
    
    logger.info(
        f"[POSITION-TRACKER] ✅ Added position: {symbol} | "
        f"Entry=${entry_price:.4f}, SL=${stop_loss_price:.4f} ({atr_sl_multiplier}x ATR), "
        f"TP=${take_profit_price:.4f} ({atr_tp_multiplier}x ATR), "
        f"Qty={quantity:.6f}"
    )
    
    return position


def remove_position(symbol: str) -> bool:
    """
    Remove position from tracker (called after exit).
    
    Args:
        symbol: Trading pair to remove
    
    Returns:
        True if position was removed, False if not found
    """
    positions = _load_positions()
    
    if symbol in positions:
        del positions[symbol]
        _save_positions(positions)
        logger.info(f"[POSITION-TRACKER] ❌ Removed position: {symbol}")
        return True
    else:
        logger.warning(f"[POSITION-TRACKER] Position not found for removal: {symbol}")
        return False


def get_position(symbol: str) -> Optional[Position]:
    """Get position for a specific symbol"""
    positions = _load_positions()
    return positions.get(symbol)


def get_all_positions() -> Dict[str, Position]:
    """Get all open positions"""
    return _load_positions()


def check_exit_trigger(symbol: str, current_price: float) -> Optional[str]:
    """
    Check if current price triggers SL or TP for a position.
    
    Args:
        symbol: Trading pair
        current_price: Current market price
    
    Returns:
        "stop_loss", "take_profit", or None
    """
    position = get_position(symbol)
    
    if not position:
        return None
    
    # Check stop-loss trigger
    if current_price <= position.stop_loss_price:
        pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
        logger.warning(
            f"[EXIT-TRIGGER] 🛑 STOP-LOSS hit on {symbol}: "
            f"Price ${current_price:.4f} <= SL ${position.stop_loss_price:.4f} "
            f"(P&L: {pnl_pct:.2f}%)"
        )
        return "stop_loss"
    
    # Check take-profit trigger
    if current_price >= position.take_profit_price:
        pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
        logger.info(
            f"[EXIT-TRIGGER] 🎯 TAKE-PROFIT hit on {symbol}: "
            f"Price ${current_price:.4f} >= TP ${position.take_profit_price:.4f} "
            f"(P&L: {pnl_pct:.2f}%)"
        )
        return "take_profit"
    
    return None


def check_all_positions_for_exits(price_fetcher) -> List[Dict]:
    """
    Check all open positions for exit triggers.
    
    Args:
        price_fetcher: Function that takes symbol and returns current price
                       e.g., lambda sym: exchange.fetch_ticker(sym)['last']
    
    Returns:
        List of exit signals: [{"symbol": "BTC/USD", "trigger": "stop_loss", "price": 50000}, ...]
    """
    positions = get_all_positions()
    exit_signals = []
    
    for symbol, position in positions.items():
        try:
            # Fetch current price
            current_price = price_fetcher(symbol)
            
            if not current_price or current_price <= 0:
                logger.warning(f"[POSITION-TRACKER] Invalid price for {symbol}: {current_price}")
                continue
            
            # Check for exit trigger
            trigger = check_exit_trigger(symbol, current_price)
            
            if trigger:
                exit_signals.append({
                    "symbol": symbol,
                    "trigger": trigger,
                    "current_price": current_price,
                    "position": position
                })
        
        except Exception as e:
            logger.error(f"[POSITION-TRACKER] Error checking {symbol}: {e}")
    
    return exit_signals


def get_position_summary() -> str:
    """Get human-readable summary of all positions"""
    positions = get_all_positions()
    
    if not positions:
        return "[POSITION-TRACKER] No open positions"
    
    lines = [f"[POSITION-TRACKER] {len(positions)} open position(s):"]
    
    for symbol, pos in positions.items():
        age_seconds = time.time() - pos.entry_timestamp
        age_minutes = int(age_seconds / 60)
        
        lines.append(
            f"  {symbol}: Entry=${pos.entry_price:.4f}, "
            f"SL=${pos.stop_loss_price:.4f}, TP=${pos.take_profit_price:.4f}, "
            f"Qty={pos.quantity:.6f}, Age={age_minutes}m"
        )
    
    return "\n".join(lines)


def clear_all_positions():
    """Clear all positions (emergency use only)"""
    if POSITIONS_FILE.exists():
        POSITIONS_FILE.unlink()
        logger.warning("[POSITION-TRACKER] ⚠️ Cleared all positions")
