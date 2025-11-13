"""
paper_trading.py - Complete paper trading simulation

Provides realistic trade simulation with:
- Fill tracking and position management
- Realistic slippage (bid-ask spread simulation)
- Trading fees
- Bracket order simulation (SL/TP)
- P&L calculation

Designed to be plugged into exchange_manager as an adapter.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import json


@dataclass
class PaperPosition:
    """Represents a paper trading position"""
    symbol: str
    side: str  # 'long' or 'short'
    entry_price: float
    quantity: float
    entry_time: float
    entry_slippage: float = 0.0
    entry_fee: float = 0.0
    
    # Bracket orders
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    # Realized P&L (when closed)
    exit_price: Optional[float] = None
    exit_time: Optional[float] = None
    exit_slippage: float = 0.0
    exit_fee: float = 0.0
    realized_pnl: Optional[float] = None
    
    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization"""
        return {
            'symbol': self.symbol,
            'side': self.side,
            'entry_price': self.entry_price,
            'quantity': self.quantity,
            'entry_time': self.entry_time,
            'entry_slippage': self.entry_slippage,
            'entry_fee': self.entry_fee,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'exit_price': self.exit_price,
            'exit_time': self.exit_time,
            'exit_slippage': self.exit_slippage,
            'exit_fee': self.exit_fee,
            'realized_pnl': self.realized_pnl
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PaperPosition":
        """Create from dict"""
        return cls(**data)
    
    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized P&L at current price"""
        if self.side == 'long':
            return (current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - current_price) * self.quantity
    
    def should_trigger_stop_loss(self, current_low: float, current_high: float) -> bool:
        """Check if stop loss should trigger based on candle high/low"""
        if self.stop_loss is None:
            return False
        
        if self.side == 'long' and current_low <= self.stop_loss:
            return True
        
        if self.side == 'short' and current_high >= self.stop_loss:
            return True
        
        return False
    
    def should_trigger_take_profit(self, current_low: float, current_high: float) -> bool:
        """Check if take profit should trigger based on candle high/low"""
        if self.take_profit is None:
            return False
        
        if self.side == 'long' and current_high >= self.take_profit:
            return True
        
        if self.side == 'short' and current_low <= self.take_profit:
            return True
        
        return False


class PaperTradingSimulator:
    """
    Realistic paper trading simulator with fill tracking and P&L.
    
    Features:
    - Bid-ask spread simulation (slippage)
    - Trading fees (Kraken: 0.16% maker, 0.26% taker)
    - Bracket order management (SL/TP auto-execution)
    - Position tracking and P&L calculation
    - State persistence
    """
    
    def __init__(
        self,
        starting_balance: float = 10000.0,
        maker_fee_pct: float = 0.0016,  # 0.16%
        taker_fee_pct: float = 0.0026,  # 0.26%
        slippage_bps: float = 5.0,  # 5 basis points (0.05%)
        state_file: str = "paper_trading_state.json"
    ):
        self.starting_balance = starting_balance
        self.maker_fee = maker_fee_pct
        self.taker_fee = taker_fee_pct
        self.slippage = slippage_bps / 10000.0  # Convert basis points to decimal
        self.state_file = state_file
        
        # State
        self.balance: float = starting_balance
        self.equity: float = starting_balance
        self.open_positions: Dict[str, PaperPosition] = {}
        self.closed_positions: List[PaperPosition] = []
        self.total_fees_paid: float = 0.0
        self.total_slippage: float = 0.0
        
        # Load persisted state
        self.load_state()
    
    def save_state(self):
        """Persist state to JSON file"""
        state = {
            'starting_balance': self.starting_balance,
            'balance': self.balance,
            'equity': self.equity,
            'total_fees_paid': self.total_fees_paid,
            'total_slippage': self.total_slippage,
            'open_positions': {
                k: v.to_dict() for k, v in self.open_positions.items()
            },
            'closed_positions': [p.to_dict() for p in self.closed_positions]
        }
        
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def load_state(self):
        """Load state from JSON file"""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            self.starting_balance = state.get('starting_balance', self.starting_balance)
            self.balance = state.get('balance', self.starting_balance)
            self.equity = state.get('equity', self.starting_balance)
            self.total_fees_paid = state.get('total_fees_paid', 0.0)
            self.total_slippage = state.get('total_slippage', 0.0)
            
            self.open_positions = {
                k: PaperPosition.from_dict(v)
                for k, v in state.get('open_positions', {}).items()
            }
            
            self.closed_positions = [
                PaperPosition.from_dict(p)
                for p in state.get('closed_positions', [])
            ]
            
            print(f"[PAPER] Loaded state: ${self.balance:.2f} balance, "
                  f"{len(self.open_positions)} open, {len(self.closed_positions)} closed")
        
        except FileNotFoundError:
            print(f"[PAPER] No saved state found - starting fresh with ${self.starting_balance:.2f}")
        except Exception as e:
            print(f"[PAPER] Error loading state: {e} - starting fresh")
    
    def calculate_fill_price(
        self,
        market_price: float,
        side: str,
        is_maker: bool = False
    ) -> Tuple[float, float]:
        """
        Calculate realistic fill price with slippage.
        
        Args:
            market_price: Current market price
            side: 'buy' or 'sell'
            is_maker: True for limit orders (lower slippage), False for market orders
        
        Returns:
            (fill_price, slippage_amount)
        """
        # Taker orders (market orders) get worse fills due to spread
        # Maker orders (limit orders) get better fills
        slippage_mult = self.slippage if not is_maker else (self.slippage * 0.5)
        
        if side == 'buy':
            # Buying: pay a bit more (slippage against you)
            fill_price = market_price * (1 + slippage_mult)
            slippage_amount = fill_price - market_price
        else:
            # Selling: get a bit less
            fill_price = market_price * (1 - slippage_mult)
            slippage_amount = market_price - fill_price
        
        return fill_price, slippage_amount
    
    def open_position(
        self,
        symbol: str,
        side: str,  # 'long' or 'short'
        quantity: float,
        market_price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        is_maker: bool = False
    ) -> Tuple[bool, str, Optional[PaperPosition]]:
        """
        Open a new paper position.
        
        Args:
            symbol: Trading symbol
            side: 'long' or 'short'
            quantity: Position size
            market_price: Current market price
            stop_loss: Stop loss price (optional)
            take_profit: Take profit price (optional)
            is_maker: True for limit orders
        
        Returns:
            (success, message, position)
        """
        # Check if position already exists
        if symbol in self.open_positions:
            return False, f"Position already open for {symbol}", None
        
        # Calculate fill price with slippage
        order_side = 'buy' if side == 'long' else 'sell'
        fill_price, slippage_amount = self.calculate_fill_price(
            market_price, order_side, is_maker
        )
        
        # Calculate cost
        position_cost = fill_price * quantity
        
        # Calculate fee (use taker fee for market orders)
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        fee = position_cost * fee_rate
        
        # Check if enough balance
        total_cost = position_cost + fee
        if total_cost > self.balance:
            return False, f"Insufficient balance (need ${total_cost:.2f}, have ${self.balance:.2f})", None
        
        # Deduct from balance
        self.balance -= total_cost
        self.total_fees_paid += fee
        self.total_slippage += slippage_amount * quantity
        
        # Create position
        position = PaperPosition(
            symbol=symbol,
            side=side,
            entry_price=fill_price,
            quantity=quantity,
            entry_time=time.time(),
            entry_slippage=slippage_amount * quantity,
            entry_fee=fee,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        self.open_positions[symbol] = position
        self.update_equity()
        self.save_state()
        
        msg = (
            f"[PAPER] Opened {side.upper()} {quantity} {symbol} @ ${fill_price:.2f} "
            f"(market=${market_price:.2f}, slippage=${slippage_amount:.2f}, fee=${fee:.2f}) "
            f"SL=${stop_loss:.2f if stop_loss else 'None'} TP=${take_profit:.2f if take_profit else 'None'}"
        )
        
        return True, msg, position
    
    def close_position(
        self,
        symbol: str,
        market_price: float,
        reason: str = "manual",
        is_maker: bool = False
    ) -> Tuple[bool, str, Optional[float]]:
        """
        Close an open paper position.
        
        Args:
            symbol: Trading symbol
            market_price: Current market price
            reason: Reason for closing ('manual', 'stop_loss', 'take_profit')
            is_maker: True for limit orders
        
        Returns:
            (success, message, realized_pnl)
        """
        position = self.open_positions.get(symbol)
        if not position:
            return False, f"No open position for {symbol}", None
        
        # Calculate fill price with slippage
        order_side = 'sell' if position.side == 'long' else 'buy'
        fill_price, slippage_amount = self.calculate_fill_price(
            market_price, order_side, is_maker
        )
        
        # Calculate proceeds
        proceeds = fill_price * position.quantity
        
        # Calculate fee
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        fee = proceeds * fee_rate
        
        # Add to balance (proceeds minus fee)
        self.balance += (proceeds - fee)
        self.total_fees_paid += fee
        self.total_slippage += slippage_amount * position.quantity
        
        # Calculate realized P&L
        if position.side == 'long':
            realized_pnl = (fill_price - position.entry_price) * position.quantity - position.entry_fee - fee
        else:
            realized_pnl = (position.entry_price - fill_price) * position.quantity - position.entry_fee - fee
        
        # Update position
        position.exit_price = fill_price
        position.exit_time = time.time()
        position.exit_slippage = slippage_amount * position.quantity
        position.exit_fee = fee
        position.realized_pnl = realized_pnl
        
        # Move to closed positions
        self.closed_positions.append(position)
        del self.open_positions[symbol]
        
        self.update_equity()
        self.save_state()
        
        pnl_str = f"+${realized_pnl:.2f}" if realized_pnl >= 0 else f"-${abs(realized_pnl):.2f}"
        msg = (
            f"[PAPER] Closed {position.side.upper()} {position.quantity} {symbol} @ ${fill_price:.2f} "
            f"(entry=${position.entry_price:.2f}, reason={reason}) "
            f"P&L: {pnl_str} (fee=${fee:.2f})"
        )
        
        return True, msg, realized_pnl
    
    def check_bracket_triggers(
        self,
        symbol: str,
        candle_low: float,
        candle_high: float,
        candle_close: float
    ) -> Optional[str]:
        """
        Check if SL or TP should trigger for a position.
        
        CONSERVATIVE BOTH-BREACH HANDLING:
        If both SL and TP breach in same candle, we assume worst-case ordering:
        - LONG positions: SL triggered first (less profitable outcome)
        - SHORT positions: SL triggered first (less profitable outcome)
        
        This prevents over-reporting profits in paper trading when actual
        execution order is unknown. For longs, SL is always checked first since
        it represents the losing trade. The system only supports longs currently.
        
        Args:
            symbol: Trading symbol
            candle_low: Low of current candle
            candle_high: High of current candle
            candle_close: Close of current candle
        
        Returns:
            'stop_loss', 'take_profit', or None
        """
        position = self.open_positions.get(symbol)
        if not position:
            return None
        
        sl_breached = position.should_trigger_stop_loss(candle_low, candle_high)
        tp_breached = position.should_trigger_take_profit(candle_low, candle_high)
        
        # CONSERVATIVE: For longs, always favor SL in both-breach scenario
        # (system only supports long positions in spot trading)
        if position.side == 'long':
            if sl_breached and position.stop_loss is not None:
                self.close_position(symbol, position.stop_loss, reason="stop_loss", is_maker=True)
                return 'stop_loss'
            elif tp_breached and position.take_profit is not None:
                self.close_position(symbol, position.take_profit, reason="take_profit", is_maker=True)
                return 'take_profit'
        
        # Future: For shorts (if implemented), check TP first for conservative fills
        # elif position.side == 'short':
        #     if tp_breached:
        #         self.close_position(symbol, position.take_profit, reason="take_profit", is_maker=True)
        #         return 'take_profit'
        #     elif sl_breached:
        #         self.close_position(symbol, position.stop_loss, reason="stop_loss", is_maker=True)
        #         return 'stop_loss'
        
        return None
    
    def update_equity(self, current_prices: Optional[Dict[str, float]] = None):
        """
        Update total equity (balance + unrealized P&L).
        
        Args:
            current_prices: Optional dict of {symbol: price} for accurate unrealized P&L
        """
        unrealized_pnl = 0.0
        
        if current_prices:
            for symbol, position in self.open_positions.items():
                if symbol in current_prices:
                    unrealized_pnl += position.calculate_unrealized_pnl(current_prices[symbol])
        
        self.equity = self.balance + unrealized_pnl
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics"""
        total_trades = len(self.closed_positions)
        
        if total_trades == 0:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'total_fees': self.total_fees_paid,
                'total_slippage': self.total_slippage,
                'balance': self.balance,
                'equity': self.equity,
                'return_pct': 0.0
            }
        
        wins = sum(1 for p in self.closed_positions if p.realized_pnl and p.realized_pnl > 0)
        losses = sum(1 for p in self.closed_positions if p.realized_pnl and p.realized_pnl < 0)
        total_pnl = sum(p.realized_pnl for p in self.closed_positions if p.realized_pnl)
        
        return {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': (wins / total_trades * 100) if total_trades > 0 else 0,
            'total_pnl': total_pnl,
            'total_fees': self.total_fees_paid,
            'total_slippage': self.total_slippage,
            'balance': self.balance,
            'equity': self.equity,
            'return_pct': ((self.equity - self.starting_balance) / self.starting_balance * 100)
        }
