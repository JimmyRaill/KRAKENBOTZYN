# risk_manager.py - Advanced risk management and metrics
from __future__ import annotations

from typing import Dict, List, Any, Optional, Protocol
from dataclasses import dataclass
from datetime import datetime, timedelta
import math


class PositionSnapshot(Protocol):
    """Protocol for position-like objects that can calculate risk."""
    side: str  # 'long' or 'short'
    entry_price: float
    stop_loss: Optional[float]
    quantity: float  # position_size


def calculate_trade_risk(position: PositionSnapshot) -> float:
    """
    Calculate risk for a trade in account currency (USD).
    
    CRITICAL CORRECTNESS:
    - For LONG positions: risk_per_unit = entry_price - stop_loss
    - For SHORT positions: risk_per_unit = stop_loss - entry_price
    - Invalid if risk_per_unit <= 0 (bad SL placement)
    
    Args:
        position: Position object with side, entry_price, stop_loss, quantity
        
    Returns:
        Risk amount in USD
        
    Raises:
        ValueError: If stop_loss is missing or invalid
    """
    if position.stop_loss is None:
        raise ValueError(f"calculate_trade_risk: position missing stop_loss (side={position.side})")
    
    # Calculate risk per unit based on position side
    if position.side == 'long':
        risk_per_unit = position.entry_price - position.stop_loss
    elif position.side == 'short':
        risk_per_unit = position.stop_loss - position.entry_price
    else:
        raise ValueError(f"calculate_trade_risk: invalid side='{position.side}', must be 'long' or 'short'")
    
    # Validate SL placement
    if risk_per_unit <= 0:
        import sys
        print(f"[RISK-ERROR] Invalid stop-loss placement for {position.side} position:", file=sys.stderr)
        print(f"  Entry: ${position.entry_price:.2f}, SL: ${position.stop_loss:.2f}", file=sys.stderr)
        print(f"  Risk per unit: ${risk_per_unit:.4f} (MUST BE POSITIVE)", file=sys.stderr)
        raise ValueError(
            f"Invalid SL: {position.side} position entry=${position.entry_price} "
            f"stop=${position.stop_loss} results in risk_per_unit={risk_per_unit:.4f} (must be > 0)"
        )
    
    # Calculate total risk
    risk_for_trade = risk_per_unit * position.quantity
    return risk_for_trade


def get_max_active_risk(
    open_positions: List[PositionSnapshot],
    equity: float,
    max_active_risk_pct: float = 0.02
) -> Dict[str, Any]:
    """
    Calculate total active risk across all open positions and check threshold.
    
    Args:
        open_positions: List of open position objects
        equity: Current account equity in USD
        max_active_risk_pct: Maximum allowed risk as % of equity (default 0.02 = 2%)
        
    Returns:
        Dict with:
            - total_active_risk: Sum of all position risks in USD
            - max_allowed_risk: Maximum allowed risk in USD
            - risk_pct: Current risk as % of equity
            - within_limits: bool - whether risk is under threshold
            - position_risks: List of individual position risks
    """
    position_risks = []
    total_active_risk = 0.0
    
    for pos in open_positions:
        try:
            risk = calculate_trade_risk(pos)
            position_risks.append({
                'side': pos.side,
                'entry_price': pos.entry_price,
                'stop_loss': pos.stop_loss,
                'quantity': pos.quantity,
                'risk_usd': risk
            })
            total_active_risk += risk
        except ValueError as e:
            # Log but continue for other positions
            print(f"[RISK-CALC-ERR] Skipping position: {e}")
            continue
    
    max_allowed_risk = equity * max_active_risk_pct
    risk_pct = (total_active_risk / equity) * 100 if equity > 0 else 0.0
    within_limits = total_active_risk <= max_allowed_risk
    
    return {
        'total_active_risk': total_active_risk,
        'max_allowed_risk': max_allowed_risk,
        'risk_pct': risk_pct,
        'within_limits': within_limits,
        'position_risks': position_risks,
        'num_positions': len(open_positions)
    }


@dataclass
class TrailingStop:
    """Trailing stop-loss manager for a position."""
    entry_price: float
    initial_stop: float
    current_stop: float
    highest_price: float
    trailing_pct: float  # Percentage to trail (e.g., 0.02 for 2%)
    activation_profit_pct: float  # Profit % to activate trailing (e.g., 0.03 for 3%)
    activated: bool = False
    
    def update(self, current_price: float) -> tuple[float, bool]:
        """
        Update trailing stop based on current price.
        
        Returns:
            Tuple of (new_stop_price, stop_triggered)
        """
        # Track highest price since entry
        if current_price > self.highest_price:
            self.highest_price = current_price
        
        # Check if trailing should activate
        profit_pct = (current_price - self.entry_price) / self.entry_price
        if not self.activated and profit_pct >= self.activation_profit_pct:
            self.activated = True
        
        # Update trailing stop if activated
        if self.activated:
            # Calculate new stop as % below highest price
            new_stop = self.highest_price * (1 - self.trailing_pct)
            
            # Only move stop up, never down
            if new_stop > self.current_stop:
                self.current_stop = new_stop
        
        # Check if stop is hit
        stop_triggered = current_price <= self.current_stop
        
        return self.current_stop, stop_triggered


@dataclass
class PositionRiskMetrics:
    """Risk metrics for a single position."""
    entry_price: float
    current_price: float
    position_size: float
    stop_loss: float
    take_profit: float
    
    @property
    def unrealized_pnl_usd(self) -> float:
        """Unrealized P&L in USD."""
        return (self.current_price - self.entry_price) * self.position_size
    
    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized P&L as percentage."""
        return ((self.current_price - self.entry_price) / self.entry_price) * 100
    
    @property
    def risk_usd(self) -> float:
        """Risk amount in USD (distance to stop-loss)."""
        return abs(self.entry_price - self.stop_loss) * self.position_size
    
    @property
    def reward_usd(self) -> float:
        """Reward amount in USD (distance to take-profit)."""
        return abs(self.take_profit - self.entry_price) * self.position_size
    
    @property
    def risk_reward_ratio(self) -> float:
        """Risk-to-reward ratio."""
        if self.risk_usd == 0:
            return 0.0
        return self.reward_usd / self.risk_usd
    
    @property
    def stop_distance_pct(self) -> float:
        """Distance to stop-loss as percentage."""
        return abs((self.current_price - self.stop_loss) / self.current_price) * 100
    
    @property
    def profit_target_pct(self) -> float:
        """Distance to take-profit as percentage."""
        return abs((self.take_profit - self.current_price) / self.current_price) * 100


class PortfolioMetrics:
    """Calculate advanced portfolio risk metrics."""
    
    @staticmethod
    def calculate_sharpe_ratio(
        returns: List[float],
        risk_free_rate: float = 0.02,
        periods_per_year: int = 252
    ) -> float:
        """
        Calculate Sharpe Ratio.
        
        Args:
            returns: List of periodic returns (e.g., daily)
            risk_free_rate: Annual risk-free rate (default 2%)
            periods_per_year: Trading periods per year (252 for daily, 52 for weekly)
            
        Returns:
            Sharpe ratio
        """
        if len(returns) < 2:
            return 0.0
        
        avg_return = sum(returns) / len(returns)
        std_return = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / len(returns))
        
        if std_return == 0:
            return 0.0
        
        # Annualize
        annual_return = avg_return * periods_per_year
        annual_std = std_return * math.sqrt(periods_per_year)
        
        sharpe = (annual_return - risk_free_rate) / annual_std
        return sharpe
    
    @staticmethod
    def calculate_max_drawdown(equity_curve: List[float]) -> tuple[float, int, int]:
        """
        Calculate maximum drawdown and its duration.
        
        Args:
            equity_curve: List of equity values over time
            
        Returns:
            Tuple of (max_drawdown_pct, peak_index, trough_index)
        """
        if len(equity_curve) < 2:
            return 0.0, 0, 0
        
        max_dd = 0.0
        peak = equity_curve[0]
        peak_idx = 0
        trough_idx = 0
        
        for i, value in enumerate(equity_curve):
            if value > peak:
                peak = value
                peak_idx = i
            
            dd = (peak - value) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                trough_idx = i
        
        return max_dd * 100, peak_idx, trough_idx
    
    @staticmethod
    def calculate_sortino_ratio(
        returns: List[float],
        risk_free_rate: float = 0.02,
        periods_per_year: int = 252
    ) -> float:
        """
        Calculate Sortino Ratio (only considers downside volatility).
        
        Args:
            returns: List of periodic returns
            risk_free_rate: Annual risk-free rate
            periods_per_year: Trading periods per year
            
        Returns:
            Sortino ratio
        """
        if len(returns) < 2:
            return 0.0
        
        avg_return = sum(returns) / len(returns)
        
        # Calculate downside deviation (only negative returns)
        downside_returns = [r for r in returns if r < 0]
        if not downside_returns:
            return float('inf') if avg_return > 0 else 0.0
        
        downside_dev = math.sqrt(sum(r ** 2 for r in downside_returns) / len(downside_returns))
        
        if downside_dev == 0:
            return 0.0
        
        # Annualize
        annual_return = avg_return * periods_per_year
        annual_downside_dev = downside_dev * math.sqrt(periods_per_year)
        
        sortino = (annual_return - risk_free_rate) / annual_downside_dev
        return sortino
    
    @staticmethod
    def calculate_calmar_ratio(
        annual_return: float,
        max_drawdown: float
    ) -> float:
        """
        Calculate Calmar Ratio (return / max drawdown).
        
        Args:
            annual_return: Annualized return (e.g., 0.15 for 15%)
            max_drawdown: Maximum drawdown (e.g., 0.10 for 10%)
            
        Returns:
            Calmar ratio
        """
        if max_drawdown == 0:
            return 0.0
        return annual_return / max_drawdown
    
    @staticmethod
    def calculate_win_rate(trades: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Calculate win rate and related metrics.
        
        Args:
            trades: List of completed trades with 'pnl' field
            
        Returns:
            Dict with win_rate, avg_win, avg_loss, profit_factor
        """
        if not trades:
            return {
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "total_trades": 0
            }
        
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) < 0]
        
        win_rate = len(wins) / len(trades) if trades else 0.0
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
        
        total_wins = sum(t["pnl"] for t in wins)
        total_losses = abs(sum(t["pnl"] for t in losses))
        profit_factor = total_wins / total_losses if total_losses > 0 else 0.0
        
        return {
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses)
        }


class RiskOptimizer:
    """Optimize risk-to-reward ratios and position sizing."""
    
    @staticmethod
    def optimize_position_size(
        account_balance: float,
        risk_per_trade_pct: float,
        entry_price: float,
        stop_loss: float,
        min_reward_risk_ratio: float = 2.0
    ) -> Dict[str, float]:
        """
        Calculate optimal position size based on risk parameters.
        
        Args:
            account_balance: Total account equity
            risk_per_trade_pct: Risk per trade as decimal (e.g., 0.02 for 2%)
            entry_price: Entry price
            stop_loss: Stop-loss price
            min_reward_risk_ratio: Minimum acceptable R:R ratio
            
        Returns:
            Dict with position_size, risk_amount, min_target_price
        """
        # Calculate risk amount in dollars
        risk_amount = account_balance * risk_per_trade_pct
        
        # Calculate position size based on stop distance
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance == 0:
            return {"position_size": 0, "risk_amount": 0, "min_target_price": entry_price}
        
        position_size = risk_amount / stop_distance
        
        # Calculate minimum target price for R:R ratio
        reward_distance = stop_distance * min_reward_risk_ratio
        if entry_price > stop_loss:
            # Long position
            min_target_price = entry_price + reward_distance
        else:
            # Short position
            min_target_price = entry_price - reward_distance
        
        return {
            "position_size": position_size,
            "risk_amount": risk_amount,
            "min_target_price": min_target_price,
            "reward_risk_ratio": min_reward_risk_ratio,
            "stop_distance": stop_distance
        }
    
    @staticmethod
    def calculate_kelly_criterion(
        win_rate: float,
        avg_win: float,
        avg_loss: float
    ) -> float:
        """
        Calculate Kelly Criterion for optimal position sizing.
        
        Args:
            win_rate: Win rate as decimal (e.g., 0.6 for 60%)
            avg_win: Average win amount
            avg_loss: Average loss amount (positive number)
            
        Returns:
            Optimal fraction of capital to risk (e.g., 0.15 for 15%)
        """
        if avg_loss == 0 or win_rate == 0:
            return 0.0
        
        # Kelly formula: (W * win_rate - loss_rate) / W
        # where W = avg_win / avg_loss
        win_loss_ratio = avg_win / abs(avg_loss)
        loss_rate = 1 - win_rate
        
        kelly_pct = (win_loss_ratio * win_rate - loss_rate) / win_loss_ratio
        
        # Cap Kelly at 25% (full Kelly is too aggressive)
        # Most traders use 1/4 or 1/2 Kelly
        kelly_pct = max(0.0, min(kelly_pct * 0.5, 0.25))
        
        return kelly_pct


def create_trailing_stop(
    entry_price: float,
    initial_stop_pct: float = 0.02,
    trailing_pct: float = 0.015,
    activation_profit_pct: float = 0.025
) -> TrailingStop:
    """
    Create a trailing stop-loss for a position.
    
    Args:
        entry_price: Entry price of the position
        initial_stop_pct: Initial stop-loss percentage (e.g., 0.02 for 2%)
        trailing_pct: Trailing percentage (e.g., 0.015 for 1.5%)
        activation_profit_pct: Profit % to activate trailing (e.g., 0.025 for 2.5%)
        
    Returns:
        TrailingStop instance
    """
    initial_stop = entry_price * (1 - initial_stop_pct)
    
    return TrailingStop(
        entry_price=entry_price,
        initial_stop=initial_stop,
        current_stop=initial_stop,
        highest_price=entry_price,
        trailing_pct=trailing_pct,
        activation_profit_pct=activation_profit_pct,
        activated=False
    )
