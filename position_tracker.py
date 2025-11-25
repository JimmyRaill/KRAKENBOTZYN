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
import portalocker

try:
    from dust_prevention import get_dust_prevention
    DUST_PREVENTION_ENABLED = True
except ImportError:
    DUST_PREVENTION_ENABLED = False
    logger.warning("[POSITION-TRACKER] Dust prevention not available")


POSITIONS_FILE = Path("open_positions.json")
LOCK_FILE = Path("open_positions.lock")  # Dedicated lock file for interprocess synchronization
LOCK_TIMEOUT = 10.0  # Maximum seconds to wait for file lock


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
        source: str = "autopilot",
        is_short: bool = False
    ):
        self.symbol = symbol
        self.entry_price = entry_price
        self.quantity = quantity
        self.stop_loss_price = stop_loss_price
        self.take_profit_price = take_profit_price
        self.atr = atr
        self.entry_timestamp = entry_timestamp
        self.source = source
        self.is_short = is_short
    
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
            "source": self.source,
            "is_short": self.is_short
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
            source=data.get("source", "autopilot"),
            is_short=data.get("is_short", False)
        )
    
    def __str__(self) -> str:
        return (
            f"Position({self.symbol}, entry=${self.entry_price:.4f}, "
            f"SL=${self.stop_loss_price:.4f}, TP=${self.take_profit_price:.4f}, "
            f"qty={self.quantity:.6f})"
        )


def _load_positions_locked(lock_handle) -> Dict[str, Position]:
    """
    Internal: Load positions while caller holds the lock.
    
    This function assumes the caller has already acquired the lock.
    """
    if not POSITIONS_FILE.exists():
        return {}
    
    try:
        with open(POSITIONS_FILE, 'r') as f:
            data = json.load(f)
        
        # Validate JSON structure
        if not isinstance(data, dict):
            logger.error(f"[POSITION-TRACKER] CORRUPTION DETECTED: positions file contains {type(data)}, expected dict")
            raise ValueError(f"Corrupted positions file: expected dict, got {type(data)}")
        
        positions = {}
        for symbol, pos_data in data.items():
            try:
                positions[symbol] = Position.from_dict(pos_data)
            except Exception as parse_err:
                logger.error(f"[POSITION-TRACKER] Failed to parse position {symbol}: {parse_err}")
                # Skip corrupted positions but continue loading others
                continue
        
        return positions
    
    except json.JSONDecodeError as e:
        logger.error(f"[POSITION-TRACKER] JSON CORRUPTION: {e}")
        raise ValueError(f"Corrupted positions file - cannot parse JSON: {e}")
    except Exception as e:
        logger.error(f"[POSITION-TRACKER] Failed to load positions: {e}")
        raise


def _load_positions() -> Dict[str, Position]:
    """
    Load all open positions with shared lock (multiple readers OK).
    """
    # Acquire shared lock on dedicated lock file
    with open(LOCK_FILE, 'a+') as lock_handle:
        portalocker.lock(lock_handle, portalocker.LOCK_SH)
        try:
            return _load_positions_locked(lock_handle)
        finally:
            portalocker.unlock(lock_handle)


def _save_positions_locked(positions: Dict[str, Position], lock_handle):
    """
    Internal: Save positions while caller holds the exclusive lock.
    
    This function assumes the caller has already acquired the exclusive lock.
    """
    temp_file = POSITIONS_FILE.with_suffix('.tmp')  # Initialize before try block
    
    try:
        data = {symbol: pos.to_dict() for symbol, pos in positions.items()}
        
        # Write to temp file first, then atomic rename
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())  # Force write to disk
        
        # Atomic rename (overwrites existing file safely)
        temp_file.replace(POSITIONS_FILE)
        
        logger.debug(f"[POSITION-TRACKER] Saved {len(positions)} position(s) to {POSITIONS_FILE}")
    
    except Exception as e:
        logger.error(f"[POSITION-TRACKER] Failed to save positions: {e}")
        # Clean up temp file if it exists
        if temp_file.exists():
            try:
                temp_file.unlink()
            except:
                pass
        raise


def _save_positions(positions: Dict[str, Position]):
    """
    Save all positions with exclusive lock (blocks all readers and writers).
    """
    # Acquire exclusive lock on dedicated lock file
    with open(LOCK_FILE, 'a+') as lock_handle:
        portalocker.lock(lock_handle, portalocker.LOCK_EX)
        try:
            _save_positions_locked(positions, lock_handle)
        finally:
            portalocker.unlock(lock_handle)


def add_position(
    symbol: str,
    entry_price: float,
    quantity: float,
    atr: float,
    atr_sl_multiplier: float = 3.0,  # WIDENED from 2.0 - stops outside normal noise
    atr_tp_multiplier: float = 4.5,  # INCREASED to 4.5 - ensures R:R >= 1.5 (4.5/3.0 = 1.5)
    source: str = "autopilot",
    is_short: bool = False
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
        is_short: True for short positions (inverted SL/TP), False for long positions
    
    Returns:
        Position object with calculated SL/TP
    
    Raises:
        ValueError: If position file is corrupted
        Exception: If file operations fail
    """
    # Calculate SL/TP prices with INVERTED logic for shorts
    if is_short:
        # SHORT: SL above entry (exit when price rises), TP below entry (exit when price falls)
        stop_loss_price = entry_price + (atr * atr_sl_multiplier)
        take_profit_price = entry_price - (atr * atr_tp_multiplier)
        
        # Ensure TP is positive
        take_profit_price = max(take_profit_price, entry_price * 0.5)  # Min 50% move down
    else:
        # LONG: SL below entry (exit when price falls), TP above entry (exit when price rises)
        stop_loss_price = entry_price - (atr * atr_sl_multiplier)
        take_profit_price = entry_price + (atr * atr_tp_multiplier)
        
        # Ensure SL is positive
        stop_loss_price = max(stop_loss_price, entry_price * 0.5)  # Max 50% loss
    
    position = Position(
        symbol=symbol,
        entry_price=entry_price,
        quantity=quantity,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        atr=atr,
        entry_timestamp=time.time(),
        source=source,
        is_short=is_short
    )
    
    # CRITICAL: Hold exclusive lock across entire read-modify-write cycle
    # This prevents race conditions between autopilot and command handlers
    with open(LOCK_FILE, 'a+') as lock_handle:
        portalocker.lock(lock_handle, portalocker.LOCK_EX)
        
        try:
            # Load existing positions while holding lock
            positions = _load_positions_locked(lock_handle)
            
            # Add new position (overwrites if symbol already exists)
            positions[symbol] = position
            
            # Save to disk while still holding lock
            _save_positions_locked(positions, lock_handle)
        
        except ValueError as corruption_err:
            logger.error(f"[POSITION-TRACKER] Cannot add position - file corrupted: {corruption_err}")
            logger.error(f"[POSITION-TRACKER] ⚠️  CRITICAL: Position {symbol} NOT TRACKED - manual intervention required!")
            raise
        finally:
            portalocker.unlock(lock_handle)
    
    logger.info(
        f"[POSITION-TRACKER] ✅ Added position: {symbol} | "
        f"Entry=${entry_price:.4f}, SL=${stop_loss_price:.4f} ({atr_sl_multiplier}x ATR), "
        f"TP=${take_profit_price:.4f} ({atr_tp_multiplier}x ATR), "
        f"Qty={quantity:.6f}"
    )
    
    # VALIDATION: Warn if stop is too tight (less than expected ATR multiple)
    if atr > 0:
        actual_sl_distance = abs(entry_price - stop_loss_price)
        actual_sl_atr_mult = actual_sl_distance / atr
        expected_sl_atr_mult = atr_sl_multiplier
        
        # Warn if actual stop is significantly tighter than expected
        if actual_sl_atr_mult < expected_sl_atr_mult * 0.9:
            logger.warning(
                f"[POSITION-TRACKER] ⚠️ STOP VALIDATION: {symbol} stop may be too tight! "
                f"Expected {expected_sl_atr_mult:.1f}x ATR, got {actual_sl_atr_mult:.2f}x ATR "
                f"(ATR=${atr:.4f}, SL distance=${actual_sl_distance:.4f})"
            )
        
        # Also log stop distance as percentage for visibility
        sl_pct = (actual_sl_distance / entry_price) * 100
        logger.debug(
            f"[POSITION-TRACKER] Stop distance for {symbol}: {sl_pct:.2f}% ({actual_sl_atr_mult:.2f}x ATR)"
        )
    
    return position


def remove_position(symbol: str) -> bool:
    """
    Remove position from tracker (called after exit).
    
    Args:
        symbol: Trading pair to remove
    
    Returns:
        True if position was removed, False if not found
    
    Raises:
        ValueError: If position file is corrupted
        Exception: If file operations fail
    """
    # CRITICAL: Hold exclusive lock across entire read-modify-write cycle
    with open(LOCK_FILE, 'a+') as lock_handle:
        portalocker.lock(lock_handle, portalocker.LOCK_EX)
        
        try:
            # Load positions while holding lock
            positions = _load_positions_locked(lock_handle)
            
            if symbol in positions:
                del positions[symbol]
                # Save while still holding lock
                _save_positions_locked(positions, lock_handle)
                logger.info(f"[POSITION-TRACKER] ❌ Removed position: {symbol}")
                return True
            else:
                logger.warning(f"[POSITION-TRACKER] Position not found for removal: {symbol}")
                return False
        
        except ValueError as corruption_err:
            logger.error(f"[POSITION-TRACKER] Cannot remove position - file corrupted: {corruption_err}")
            logger.error(f"[POSITION-TRACKER] ⚠️  WARNING: Position {symbol} may still be open - manual check required!")
            raise
        finally:
            portalocker.unlock(lock_handle)


def get_position(symbol: str) -> Optional[Position]:
    """
    Get position for a specific symbol.
    
    Returns None if position not found OR if file is corrupted (logged as error).
    """
    try:
        positions = _load_positions()
        return positions.get(symbol)
    except Exception as e:
        logger.error(f"[POSITION-TRACKER] Failed to get position {symbol}: {e}")
        return None


def get_all_positions() -> Dict[str, Position]:
    """
    Get all open positions.
    
    Returns empty dict if file is corrupted (logged as error).
    """
    try:
        return _load_positions()
    except Exception as e:
        logger.error(f"[POSITION-TRACKER] Failed to load positions: {e}")
        logger.error(f"[POSITION-TRACKER] ⚠️  CRITICAL: Cannot monitor positions - autopilot may miss exits!")
        return {}


def check_exit_trigger(symbol: str, current_price: float) -> Optional[str]:
    """
    Check if current price triggers SL or TP for a position.
    
    Handles both LONG and SHORT positions with inverted trigger logic:
    - LONG: SL below entry (exit on price drop), TP above entry (exit on price rise)
    - SHORT: SL above entry (exit on price rise), TP below entry (exit on price drop)
    
    Args:
        symbol: Trading pair
        current_price: Current market price
    
    Returns:
        "stop_loss", "take_profit", or None
    """
    position = get_position(symbol)
    
    if not position:
        return None
    
    # Calculate P&L percentage (positive for profit, negative for loss)
    if position.is_short:
        # SHORT: Profit when price falls, loss when price rises
        pnl_pct = ((position.entry_price - current_price) / position.entry_price) * 100
    else:
        # LONG: Profit when price rises, loss when price falls
        pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
    
    if position.is_short:
        # SHORT POSITION: Inverted trigger logic
        # SL is ABOVE entry (exit when price rises)
        if current_price >= position.stop_loss_price:
            logger.warning(
                f"[EXIT-TRIGGER] 🛑 STOP-LOSS hit on SHORT {symbol}: "
                f"Price ${current_price:.4f} >= SL ${position.stop_loss_price:.4f} "
                f"(P&L: {pnl_pct:.2f}%)"
            )
            return "stop_loss"
        
        # TP is BELOW entry (exit when price falls)
        if current_price <= position.take_profit_price:
            logger.info(
                f"[EXIT-TRIGGER] 🎯 TAKE-PROFIT hit on SHORT {symbol}: "
                f"Price ${current_price:.4f} <= TP ${position.take_profit_price:.4f} "
                f"(P&L: {pnl_pct:.2f}%)"
            )
            return "take_profit"
    else:
        # LONG POSITION: Standard trigger logic
        # SL is BELOW entry (exit when price falls)
        if current_price <= position.stop_loss_price:
            logger.warning(
                f"[EXIT-TRIGGER] 🛑 STOP-LOSS hit on LONG {symbol}: "
                f"Price ${current_price:.4f} <= SL ${position.stop_loss_price:.4f} "
                f"(P&L: {pnl_pct:.2f}%)"
            )
            return "stop_loss"
        
        # TP is ABOVE entry (exit when price rises)
        if current_price >= position.take_profit_price:
            logger.info(
                f"[EXIT-TRIGGER] 🎯 TAKE-PROFIT hit on LONG {symbol}: "
                f"Price ${current_price:.4f} >= TP ${position.take_profit_price:.4f} "
                f"(P&L: {pnl_pct:.2f}%)"
            )
            return "take_profit"
    
    return None


def check_if_dust_position(symbol: str, current_price: float) -> bool:
    """
    Check if a position has become dust (below Kraken minimum tradeable size).
    
    This can happen when:
    - Position was partially closed
    - Fees reduced the position size
    - Precision rounding errors accumulated
    
    Args:
        symbol: Trading pair
        current_price: Current market price
    
    Returns:
        True if position is dust, False otherwise
    """
    if not DUST_PREVENTION_ENABLED:
        return False
    
    position = get_position(symbol)
    if not position:
        return False
    
    try:
        dust_prevention = get_dust_prevention()
        is_dust = dust_prevention.is_dust_position(symbol, position.quantity, current_price)
        
        if is_dust:
            logger.warning(
                f"[POSITION-TRACKER] ⚠️  DUST DETECTED: {symbol} position {position.quantity:.8f} "
                f"is below Kraken minimum - cannot be sold via normal orders"
            )
            logger.warning(
                f"[POSITION-TRACKER] Manual action required: Consolidate via Kraken 'Buy Crypto' button ($1 minimum)"
            )
        
        return is_dust
        
    except Exception as e:
        logger.error(f"[POSITION-TRACKER] Failed to check dust status for {symbol}: {e}")
        return False


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
