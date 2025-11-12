# backtest_mode.py - Backtest mode for strategy validation
from __future__ import annotations

import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict
import json


@dataclass
class BacktestTrade:
    """A single trade in backtest."""
    timestamp: str
    symbol: str
    side: str  # "buy" or "sell"
    price: float
    quantity: float
    value_usd: float
    reason: str


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    start_time: str
    end_time: str
    starting_equity: float
    ending_equity: float
    total_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_profit: float
    total_loss: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trades: List[Dict[str, Any]]


class BacktestMode:
    """
    Backtesting engine for strategy validation.
    
    Simulates trading on historical data without real orders.
    Provides comprehensive performance metrics.
    """
    
    def __init__(
        self,
        starting_equity: float = 1000.0,
        enable_backtest: bool = False
    ):
        self.enabled = enable_backtest or os.getenv("BACKTEST_MODE") == "1"
        self.starting_equity = starting_equity
        self.current_equity = starting_equity
        
        self.trades: List[BacktestTrade] = []
        self.equity_curve: List[float] = [starting_equity]
        self.positions: Dict[str, float] = {}  # {symbol: quantity}
        
        if self.enabled:
            print("[BACKTEST] 📊 Backtest mode ENABLED - No real orders")
    
    def is_enabled(self) -> bool:
        """Check if backtest mode is active."""
        return self.enabled
    
    def execute_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        value_usd: float,
        reason: str
    ) -> Dict[str, Any]:
        """
        Simulate a trade execution.
        
        Args:
            symbol: Trading pair
            side: "buy" or "sell"
            price: Execution price
            value_usd: Trade value in USD
            reason: Why the trade was made
            
        Returns:
            Simulated order result
        """
        if not self.enabled:
            return {"error": "Backtest mode not enabled"}
        
        quantity = value_usd / price
        
        # Update positions
        if side == "buy":
            self.positions[symbol] = self.positions.get(symbol, 0) + quantity
            self.current_equity -= value_usd
        else:  # sell
            self.positions[symbol] = max(0, self.positions.get(symbol, 0) - quantity)
            self.current_equity += value_usd
        
        # Record trade
        trade = BacktestTrade(
            timestamp=datetime.now().isoformat(),
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            value_usd=value_usd,
            reason=reason
        )
        self.trades.append(trade)
        
        # Calculate mark-to-market equity (cash + open positions at current price)
        position_value = sum(qty * price for sym, qty in self.positions.items())
        mtm_equity = self.current_equity + position_value
        self.equity_curve.append(mtm_equity)
        
        print(f"[BACKTEST] {side.upper()} {symbol} {quantity:.4f} @ ${price:.2f} "
              f"(${value_usd:.2f}) - {reason}")
        
        return {
            "id": f"backtest_{len(self.trades)}",
            "symbol": symbol,
            "side": side,
            "type": "market",
            "price": price,
            "amount": quantity,
            "cost": value_usd,
            "status": "closed",
            "timestamp": trade.timestamp
        }
    
    def get_position_value(self, symbol: str, current_price: float) -> float:
        """Get current value of a position."""
        quantity = self.positions.get(symbol, 0)
        return quantity * current_price
    
    def calculate_total_equity(self, current_prices: Dict[str, float]) -> float:
        """Calculate total equity including open positions."""
        position_value = sum(
            self.get_position_value(symbol, price)
            for symbol, price in current_prices.items()
            if symbol in self.positions
        )
        return self.current_equity + position_value
    
    def get_results(self, current_prices: Optional[Dict[str, float]] = None) -> BacktestResult:
        """
        Generate backtest performance report.
        
        Args:
            current_prices: Current market prices for open position valuation
        """
        if not self.trades:
            return BacktestResult(
                start_time=datetime.now().isoformat(),
                end_time=datetime.now().isoformat(),
                starting_equity=self.starting_equity,
                ending_equity=self.current_equity,
                total_return_pct=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                total_profit=0.0,
                total_loss=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                trades=[]
            )
        
        # Calculate total equity including open positions
        total_equity = self.current_equity
        if current_prices:
            total_equity = self.calculate_total_equity(current_prices)
        
        # Calculate metrics
        total_return = total_equity - self.starting_equity
        total_return_pct = (total_return / self.starting_equity) * 100
        
        # Calculate win/loss stats using proper FIFO accounting
        wins = 0
        losses = 0
        total_profit = 0.0
        total_loss = 0.0
        
        # Track trades by symbol for proper pairing
        trade_history: Dict[str, List[BacktestTrade]] = {}
        for trade in self.trades:
            if trade.symbol not in trade_history:
                trade_history[trade.symbol] = []
            trade_history[trade.symbol].append(trade)
        
        # Calculate P/L for each symbol using quantity-aware FIFO
        for symbol, symbol_trades in trade_history.items():
            # FIFO queue: [(buy_price, quantity_remaining)]
            buy_queue: List[Tuple[float, float]] = []
            
            for trade in symbol_trades:
                if trade.side == "buy":
                    # Add to FIFO queue
                    buy_queue.append((trade.price, trade.quantity))
                
                elif trade.side == "sell":
                    # Match against FIFO queue
                    sell_qty_remaining = trade.quantity
                    sell_proceeds = trade.value_usd
                    cost_basis = 0.0
                    
                    while sell_qty_remaining > 0 and buy_queue:
                        buy_price, buy_qty = buy_queue[0]
                        
                        # Match quantity
                        matched_qty = min(sell_qty_remaining, buy_qty)
                        
                        # Calculate cost for this portion
                        cost_basis += matched_qty * buy_price
                        sell_qty_remaining -= matched_qty
                        
                        # Update or remove from queue
                        if matched_qty >= buy_qty:
                            buy_queue.pop(0)  # Fully consumed
                        else:
                            buy_queue[0] = (buy_price, buy_qty - matched_qty)
                    
                    # Calculate realized P/L
                    if cost_basis > 0:
                        pnl = sell_proceeds - cost_basis
                        if pnl > 0:
                            wins += 1
                            total_profit += pnl
                        else:
                            losses += 1
                            total_loss += abs(pnl)
        
        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
        profit_factor = (total_profit / total_loss) if total_loss > 0 else 0.0
        
        # Calculate max drawdown
        peak = self.starting_equity
        max_drawdown = 0.0
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            drawdown = ((peak - equity) / peak) * 100
            max_drawdown = max(max_drawdown, drawdown)
        
        # Simple Sharpe ratio approximation
        returns = [
            (self.equity_curve[i] - self.equity_curve[i-1]) / self.equity_curve[i-1]
            for i in range(1, len(self.equity_curve))
        ]
        sharpe = 0.0
        if returns and len(returns) > 0:
            import statistics
            avg_return = statistics.mean(returns)
            std_return = statistics.stdev(returns) if len(returns) > 1 else 0.0001
            sharpe = (avg_return / std_return) * (252 ** 0.5) if std_return > 0 else 0.0
        
        return BacktestResult(
            start_time=self.trades[0].timestamp,
            end_time=self.trades[-1].timestamp,
            starting_equity=self.starting_equity,
            ending_equity=total_equity,  # Include open positions
            total_return_pct=total_return_pct,
            total_trades=total_trades,
            winning_trades=wins,
            losing_trades=losses,
            win_rate_pct=win_rate,
            total_profit=total_profit,
            total_loss=total_loss,
            profit_factor=profit_factor,
            max_drawdown_pct=max_drawdown,
            sharpe_ratio=sharpe,
            trades=[asdict(t) for t in self.trades]
        )
    
    def print_summary(self, current_prices: Optional[Dict[str, float]] = None) -> None:
        """Print backtest summary to console."""
        results = self.get_results(current_prices)
        
        print("\n" + "="*60)
        print("BACKTEST RESULTS")
        print("="*60)
        print(f"Period: {results.start_time} to {results.end_time}")
        print(f"Starting Equity: ${results.starting_equity:.2f}")
        print(f"Ending Equity: ${results.ending_equity:.2f}")
        print(f"Total Return: ${results.ending_equity - results.starting_equity:.2f} "
              f"({results.total_return_pct:+.2f}%)")
        print(f"\nTrades: {results.total_trades} total")
        print(f"  Wins: {results.winning_trades} ({results.win_rate_pct:.1f}%)")
        print(f"  Losses: {results.losing_trades}")
        print(f"  Profit Factor: {results.profit_factor:.2f}")
        print(f"\nRisk Metrics:")
        print(f"  Max Drawdown: {results.max_drawdown_pct:.2f}%")
        print(f"  Sharpe Ratio: {results.sharpe_ratio:.2f}")
        print("="*60 + "\n")


# Global backtest instance
_backtest: Optional[BacktestMode] = None


def get_backtest() -> BacktestMode:
    """Get or create global backtest instance."""
    global _backtest
    if _backtest is None:
        _backtest = BacktestMode()
    return _backtest
