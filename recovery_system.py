# recovery_system.py - Self-correcting loss recovery and profit reinvestment
from __future__ import annotations

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class RecoveryMode(Enum):
    """Loss recovery strategies."""
    NONE = "none"  # No recovery active
    CONSERVATIVE = "conservative"  # Reduce position sizes
    CAUTIOUS = "cautious"  # Wait for high-confidence signals only
    AGGRESSIVE_RECOVERY = "aggressive_recovery"  # Increase position sizes carefully
    PAUSE = "pause"  # Stop trading temporarily


@dataclass
class RecoveryState:
    """Current recovery state."""
    mode: RecoveryMode
    max_loss_usd: float
    current_loss_usd: float
    recovery_target_usd: float
    trades_since_activation: int
    activation_time: str
    position_size_multiplier: float  # Adjust position sizes
    min_signal_confidence: float  # Minimum confidence to trade
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "max_loss": self.max_loss_usd,
            "current_loss": self.current_loss_usd,
            "recovery_target": self.recovery_target_usd,
            "trades_since_activation": self.trades_since_activation,
            "activation_time": self.activation_time,
            "position_size_multiplier": self.position_size_multiplier,
            "min_signal_confidence": self.min_signal_confidence
        }


class LossRecoverySystem:
    """
    Intelligent loss recovery system that adapts trading behavior after losses.
    """
    
    def __init__(
        self,
        max_daily_loss_usd: float = 25.0,
        max_consecutive_losses: int = 3,
        recovery_multiplier: float = 1.5
    ):
        self.max_daily_loss = max_daily_loss_usd
        self.max_consecutive_losses = max_consecutive_losses
        self.recovery_multiplier = recovery_multiplier
        
        self.current_state: Optional[RecoveryState] = None
        self.loss_history: List[Dict[str, Any]] = []
    
    def update_loss(
        self,
        loss_usd: float,
        current_equity: float,
        win_rate: float = 0.5
    ) -> RecoveryState:
        """
        Update recovery system with new loss and determine recovery mode.
        
        Args:
            loss_usd: Loss amount in USD (positive number)
            current_equity: Current account equity
            win_rate: Recent win rate (0.0 to 1.0)
            
        Returns:
            Updated recovery state
        """
        self.loss_history.append({
            "loss_usd": loss_usd,
            "timestamp": datetime.now().isoformat(),
            "equity": current_equity
        })
        
        # Calculate recent losses
        recent_losses = self._get_recent_losses(hours=24)
        total_recent_loss = sum(l["loss_usd"] for l in recent_losses)
        consecutive_losses = self._count_consecutive_losses()
        
        # Determine recovery mode
        mode = RecoveryMode.NONE
        position_multiplier = 1.0
        min_confidence = 0.5
        
        # Pause if max daily loss reached
        if total_recent_loss >= self.max_daily_loss:
            mode = RecoveryMode.PAUSE
            position_multiplier = 0.0
            min_confidence = 1.0  # Impossible threshold
        
        # Conservative mode after consecutive losses
        elif consecutive_losses >= self.max_consecutive_losses:
            mode = RecoveryMode.CONSERVATIVE
            position_multiplier = 0.5  # Half position size
            min_confidence = 0.7  # Higher confidence required
        
        # Cautious mode after significant loss
        elif total_recent_loss > self.max_daily_loss * 0.5:
            mode = RecoveryMode.CAUTIOUS
            position_multiplier = 0.75
            min_confidence = 0.65
        
        # Aggressive recovery if close to daily loss limit but win rate is good
        elif total_recent_loss > self.max_daily_loss * 0.3 and win_rate > 0.6:
            mode = RecoveryMode.AGGRESSIVE_RECOVERY
            position_multiplier = 1.25  # Slightly larger positions
            min_confidence = 0.6
        
        # Create recovery target
        recovery_target = total_recent_loss * self.recovery_multiplier
        
        self.current_state = RecoveryState(
            mode=mode,
            max_loss_usd=self.max_daily_loss,
            current_loss_usd=total_recent_loss,
            recovery_target_usd=recovery_target,
            trades_since_activation=0 if not self.current_state else self.current_state.trades_since_activation + 1,
            activation_time=datetime.now().isoformat(),
            position_size_multiplier=position_multiplier,
            min_signal_confidence=min_confidence
        )
        
        return self.current_state
    
    def should_trade(self, signal_confidence: float) -> bool:
        """
        Check if trading should proceed based on recovery state.
        
        Args:
            signal_confidence: Trading signal confidence (0.0 to 1.0)
            
        Returns:
            True if trade should proceed
        """
        if not self.current_state or self.current_state.mode == RecoveryMode.NONE:
            return True
        
        if self.current_state.mode == RecoveryMode.PAUSE:
            return False
        
        return signal_confidence >= self.current_state.min_signal_confidence
    
    def adjust_position_size(self, base_size: float) -> float:
        """
        Adjust position size based on recovery mode.
        
        Args:
            base_size: Base position size
            
        Returns:
            Adjusted position size
        """
        if not self.current_state:
            return base_size
        
        return base_size * self.current_state.position_size_multiplier
    
    def _get_recent_losses(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get losses from recent time period."""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        return [
            l for l in self.loss_history
            if datetime.fromisoformat(l["timestamp"]) > cutoff
        ]
    
    def _count_consecutive_losses(self) -> int:
        """Count consecutive losses from most recent trades."""
        count = 0
        for loss in reversed(self.loss_history):
            if loss["loss_usd"] > 0:
                count += 1
            else:
                break
        return count
    
    def reset_recovery(self):
        """Reset recovery state (call after successful recovery)."""
        if self.current_state and self.current_state.current_loss_usd <= 0:
            self.current_state = None
            self.loss_history.clear()


class ProfitReinvestmentSystem:
    """
    Automated profit reinvestment system that compounds gains.
    """
    
    def __init__(
        self,
        reinvest_pct: float = 0.5,  # Reinvest 50% of profits
        min_profit_threshold_usd: float = 10.0,  # Only reinvest if profit > $10
        max_equity_pct: float = 0.9  # Never risk more than 90% of equity
    ):
        self.reinvest_pct = reinvest_pct
        self.min_profit_threshold = min_profit_threshold_usd
        self.max_equity_pct = max_equity_pct
        
        self.total_reinvested = 0.0
        self.reinvestment_history: List[Dict[str, Any]] = []
    
    def calculate_reinvestment(
        self,
        profit_usd: float,
        current_equity: float,
        base_position_size_usd: float
    ) -> Dict[str, float]:
        """
        Calculate how much profit to reinvest and new position size.
        
        Args:
            profit_usd: Recent profit amount
            current_equity: Current account equity
            base_position_size_usd: Base position size
            
        Returns:
            Dict with reinvest_amount, new_position_size, new_equity
        """
        # Only reinvest if profit exceeds threshold
        if profit_usd < self.min_profit_threshold:
            return {
                "reinvest_amount": 0.0,
                "new_position_size": base_position_size_usd,
                "new_equity": current_equity,
                "reinvested": False
            }
        
        # Calculate reinvestment amount
        reinvest_amount = profit_usd * self.reinvest_pct
        
        # Calculate new equity with reinvestment
        new_equity = current_equity + reinvest_amount
        
        # Calculate new position size (proportional to equity growth)
        equity_growth_factor = new_equity / current_equity
        new_position_size = base_position_size_usd * equity_growth_factor
        
        # Cap at max equity percentage
        max_position = new_equity * self.max_equity_pct
        new_position_size = min(new_position_size, max_position)
        
        # Record reinvestment
        self.total_reinvested += reinvest_amount
        self.reinvestment_history.append({
            "amount": reinvest_amount,
            "profit": profit_usd,
            "equity_before": current_equity,
            "equity_after": new_equity,
            "timestamp": datetime.now().isoformat()
        })
        
        return {
            "reinvest_amount": reinvest_amount,
            "new_position_size": new_position_size,
            "new_equity": new_equity,
            "reinvested": True,
            "total_reinvested": self.total_reinvested
        }
    
    def get_compounding_stats(self) -> Dict[str, Any]:
        """Get statistics about profit compounding."""
        if not self.reinvestment_history:
            return {
                "total_reinvested": 0.0,
                "reinvestment_count": 0,
                "avg_reinvestment": 0.0,
                "compounding_factor": 1.0
            }
        
        first_equity = self.reinvestment_history[0]["equity_before"]
        last_equity = self.reinvestment_history[-1]["equity_after"]
        
        compounding_factor = last_equity / first_equity if first_equity > 0 else 1.0
        
        return {
            "total_reinvested": self.total_reinvested,
            "reinvestment_count": len(self.reinvestment_history),
            "avg_reinvestment": self.total_reinvested / len(self.reinvestment_history),
            "compounding_factor": compounding_factor,
            "equity_growth_pct": (compounding_factor - 1) * 100
        }


class PortfolioRebalancer:
    """
    Automatically rebalance portfolio across multiple symbols.
    """
    
    def __init__(
        self,
        target_allocations: Dict[str, float] = None,
        rebalance_threshold_pct: float = 0.10  # Rebalance if >10% off target
    ):
        # Default equal allocation if not specified
        self.target_allocations = target_allocations or {}
        self.rebalance_threshold = rebalance_threshold_pct
    
    def calculate_rebalancing(
        self,
        current_positions: Dict[str, float],  # {symbol: value_usd}
        total_equity: float
    ) -> Dict[str, Dict[str, float]]:
        """
        Calculate rebalancing trades needed.
        
        Args:
            current_positions: Current position values by symbol
            total_equity: Total account equity
            
        Returns:
            Dict of {symbol: {current_pct, target_pct, action, amount_usd}}
        """
        # Calculate current allocations
        total_invested = sum(current_positions.values())
        current_allocations = {
            symbol: value / total_equity if total_equity > 0 else 0
            for symbol, value in current_positions.items()
        }
        
        # If no target allocations set, use equal weight
        if not self.target_allocations:
            num_symbols = len(current_positions)
            self.target_allocations = {
                symbol: 1.0 / num_symbols
                for symbol in current_positions.keys()
            }
        
        # Calculate rebalancing actions
        rebalancing_plan = {}
        
        for symbol in current_positions.keys():
            current_pct = current_allocations.get(symbol, 0.0)
            target_pct = self.target_allocations.get(symbol, 0.0)
            
            deviation = abs(current_pct - target_pct)
            
            # Determine if rebalancing needed
            if deviation > self.rebalance_threshold:
                target_value = total_equity * target_pct
                current_value = current_positions[symbol]
                adjustment = target_value - current_value
                
                action = "buy" if adjustment > 0 else "sell"
                
                rebalancing_plan[symbol] = {
                    "current_pct": current_pct * 100,
                    "target_pct": target_pct * 100,
                    "deviation_pct": deviation * 100,
                    "action": action,
                    "amount_usd": abs(adjustment),
                    "needs_rebalancing": True
                }
            else:
                rebalancing_plan[symbol] = {
                    "current_pct": current_pct * 100,
                    "target_pct": target_pct * 100,
                    "deviation_pct": deviation * 100,
                    "needs_rebalancing": False
                }
        
        return rebalancing_plan
    
    def set_target_allocation(self, allocations: Dict[str, float]):
        """
        Set target allocation percentages.
        
        Args:
            allocations: Dict of {symbol: target_percentage (as decimal)}
        """
        total = sum(allocations.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Allocations must sum to 1.0, got {total}")
        
        self.target_allocations = allocations
