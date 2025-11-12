# profit_target.py - Daily profit target system with smart pause
from __future__ import annotations

import json
import os
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict


@dataclass
class DailyTarget:
    """Daily profit target configuration and state."""
    date: str  # ISO date (YYYY-MM-DD)
    target_pct: float  # Target profit % for the day
    starting_equity: float  # Equity at start of day
    current_equity: float  # Current equity
    target_reached: bool  # Whether target was hit
    target_reached_at: Optional[str]  # ISO timestamp when target hit
    pause_until: Optional[str]  # ISO timestamp when trading can resume
    trades_today: int  # Number of trades executed
    profit_today: float  # Profit/loss in USD today


class ProfitTargetSystem:
    """
    Daily profit target system with smart pause.
    
    - Sets daily profit goal (e.g., 0.035-0.038% of equity)
    - Pauses trading for 6 hours after hitting target
    - Tracks progress and provides status
    """
    
    def __init__(
        self,
        target_pct_min: float = 0.035,  # 0.035% daily minimum target
        target_pct_max: float = 0.038,  # 0.038% daily maximum target
        pause_hours: float = 6.0,  # Pause 6 hours after hitting target
        state_file: str = "profit_target_state.json"
    ):
        self.target_min = target_pct_min / 100  # Convert to decimal
        self.target_max = target_pct_max / 100
        self.pause_duration = timedelta(hours=pause_hours)
        self.state_file = Path(state_file)
        
        self.state: Optional[DailyTarget] = None
        self.load_state()
    
    def load_state(self) -> None:
        """Load state from disk or initialize new day."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.state = DailyTarget(**data)
                    
                    # Check if it's a new day
                    if self.state.date != datetime.now().date().isoformat():
                        self.state = None  # Reset for new day
            except Exception as e:
                print(f"[TARGET-LOAD-ERR] {e}")
                self.state = None
    
    def save_state(self) -> None:
        """Save state to disk."""
        if self.state:
            try:
                with open(self.state_file, 'w') as f:
                    json.dump(asdict(self.state), f, indent=2)
            except Exception as e:
                print(f"[TARGET-SAVE-ERR] {e}")
    
    def initialize_day(self, starting_equity: float) -> DailyTarget:
        """Initialize new trading day with equity-based target."""
        import random
        
        # Randomize target within range for variety
        target_pct = random.uniform(self.target_min, self.target_max)
        
        self.state = DailyTarget(
            date=datetime.now().date().isoformat(),
            target_pct=target_pct,
            starting_equity=starting_equity,
            current_equity=starting_equity,
            target_reached=False,
            target_reached_at=None,
            pause_until=None,
            trades_today=0,
            profit_today=0.0
        )
        
        self.save_state()
        
        print(f"[TARGET] New day initialized: ${starting_equity:.2f} equity, "
              f"{target_pct*100:.3f}% target (${starting_equity * target_pct:.2f})")
        
        return self.state
    
    def update_equity(self, current_equity: float) -> None:
        """Update current equity and check if target reached."""
        if not self.state:
            self.initialize_day(current_equity)
            return
        
        if not self.state:
            return
        
        self.state.current_equity = current_equity
        self.state.profit_today = current_equity - self.state.starting_equity
        
        # Check if target reached
        profit_pct = self.state.profit_today / self.state.starting_equity
        
        if not self.state.target_reached and profit_pct >= self.state.target_pct:
            self.state.target_reached = True
            self.state.target_reached_at = datetime.now().isoformat()
            self.state.pause_until = (
                datetime.now() + self.pause_duration
            ).isoformat()
            
            print(f"[TARGET] 🎯 DAILY TARGET HIT! "
                  f"+${self.state.profit_today:.2f} ({profit_pct*100:.3f}%)")
            print(f"[TARGET] 😴 Pausing trading until "
                  f"{datetime.fromisoformat(self.state.pause_until).strftime('%I:%M %p')}")
        
        self.save_state()
    
    def record_trade(self, profit_usd: float) -> None:
        """Record a trade execution and update profit tracking."""
        if not self.state:
            return
        
        self.state.trades_today += 1
        self.state.profit_today += profit_usd
        self.state.current_equity += profit_usd
        
        # Check if target reached after this trade
        profit_pct = self.state.profit_today / self.state.starting_equity if self.state.starting_equity > 0 else 0
        
        if not self.state.target_reached and profit_pct >= self.state.target_pct:
            self.state.target_reached = True
            self.state.target_reached_at = datetime.now().isoformat()
            self.state.pause_until = (
                datetime.now() + self.pause_duration
            ).isoformat()
            
            print(f"[TARGET] 🎯 DAILY TARGET HIT after trade! "
                  f"+${self.state.profit_today:.2f} ({profit_pct*100:.3f}%)")
            print(f"[TARGET] 😴 Pausing trading until "
                  f"{datetime.fromisoformat(self.state.pause_until).strftime('%I:%M %p')}")
        
        self.save_state()
    
    def should_trade(self, current_equity: float = 0) -> tuple[bool, str]:
        """
        Check if trading is allowed based on target status.
        
        Args:
            current_equity: Current account equity (will initialize if needed)
        
        Returns:
            (allowed: bool, reason: str)
        """
        # Force initialization if not yet initialized
        if not self.state and current_equity > 0:
            self.initialize_day(current_equity)
        
        if not self.state:
            return (False, "Target system not initialized - provide equity")
        
        # Check if currently paused
        if self.state.pause_until:
            pause_time = datetime.fromisoformat(self.state.pause_until)
            if datetime.now() < pause_time:
                remaining = (pause_time - datetime.now()).total_seconds() / 3600
                return (
                    False,
                    f"Paused after hitting target (resume in {remaining:.1f}h)"
                )
            else:
                # Pause expired, clear it
                self.state.pause_until = None
                self.save_state()
        
        return (True, "Trading allowed")
    
    def get_progress(self) -> Dict[str, Any]:
        """Get current progress toward daily target."""
        if not self.state:
            return {
                "initialized": False,
                "message": "Target system not initialized"
            }
        
        profit_pct = (
            self.state.profit_today / self.state.starting_equity
            if self.state.starting_equity > 0 else 0
        )
        
        progress_pct = (
            profit_pct / self.state.target_pct * 100
            if self.state.target_pct > 0 else 0
        )
        
        target_usd = self.state.starting_equity * self.state.target_pct
        remaining_usd = target_usd - self.state.profit_today
        
        return {
            "initialized": True,
            "date": self.state.date,
            "starting_equity": self.state.starting_equity,
            "current_equity": self.state.current_equity,
            "target_pct": self.state.target_pct * 100,
            "target_usd": target_usd,
            "profit_today": self.state.profit_today,
            "profit_pct": profit_pct * 100,
            "progress_pct": min(progress_pct, 100),
            "remaining_usd": max(remaining_usd, 0),
            "target_reached": self.state.target_reached,
            "target_reached_at": self.state.target_reached_at,
            "paused": self.state.pause_until is not None,
            "pause_until": self.state.pause_until,
            "trades_today": self.state.trades_today
        }
    
    def get_status_message(self) -> str:
        """Get human-readable status message."""
        if not self.state:
            return "[TARGET] Not initialized"
        
        progress = self.get_progress()
        
        if progress["target_reached"]:
            if progress["paused"]:
                pause_time = datetime.fromisoformat(self.state.pause_until)  # type: ignore
                remaining = (pause_time - datetime.now()).total_seconds() / 3600
                return (f"[TARGET] ✅ Target reached! Paused for {remaining:.1f}h "
                       f"(+${progress['profit_today']:.2f}, {progress['profit_pct']:.3f}%)")
            else:
                return f"[TARGET] ✅ Target reached! (+${progress['profit_today']:.2f})"
        else:
            return (f"[TARGET] Progress: {progress['progress_pct']:.1f}% "
                   f"(${progress['profit_today']:.2f} / ${progress['target_usd']:.2f})")


# Global instance for easy access
_target_system: Optional[ProfitTargetSystem] = None


def get_target_system() -> ProfitTargetSystem:
    """Get or create global profit target system."""
    global _target_system
    if _target_system is None:
        _target_system = ProfitTargetSystem()
    return _target_system
