"""
account_state.py - Single source of truth for account state (LIVE vs PAPER)

CRITICAL: This module ensures complete isolation between LIVE and PAPER modes.
- LIVE mode: All data comes from Kraken API
- PAPER mode: All data comes from internal paper ledger

NO CROSS-CONTAMINATION ALLOWED.
"""

import time
import json
from typing import Dict, List, Any, Optional, Literal
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict

from exchange_manager import get_exchange, is_paper_mode, get_mode_str
from loguru import logger


# State files
PAPER_STATE_FILE = Path(__file__).parent / "paper_ledger.json"


@dataclass
class PaperTrade:
    """Paper trading trade record"""
    trade_id: str
    order_id: str
    timestamp: float
    datetime_utc: str
    symbol: str
    side: str  # 'buy' or 'sell'
    price: float
    quantity: float
    cost: float  # quantity * price
    fee: float
    fee_currency: str = "USD"
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "PaperTrade":
        return cls(**data)


@dataclass
class PaperBalance:
    """Paper trading balance"""
    currency: str
    free: float
    locked: float
    total: float
    last_updated: float
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "PaperBalance":
        return cls(**data)


class PaperLedger:
    """
    Internal paper trading ledger.
    Maintains simulated balances and trade history completely separate from live account.
    """
    
    def __init__(self, state_file: Path = PAPER_STATE_FILE):
        self.state_file = state_file
        self.balances: Dict[str, PaperBalance] = {}
        self.trades: List[PaperTrade] = []
        self.orders: List[Dict[str, Any]] = []
        self.starting_balance_usd = 10000.0  # Default paper account size
        self.load()
    
    def load(self) -> None:
        """Load paper ledger from disk"""
        if not self.state_file.exists():
            # Initialize with default balance
            self.balances = {
                "USD": PaperBalance(
                    currency="USD",
                    free=self.starting_balance_usd,
                    locked=0.0,
                    total=self.starting_balance_usd,
                    last_updated=time.time()
                )
            }
            self.trades = []
            self.orders = []
            self.save()
            logger.info(f"[PAPER-LEDGER] Initialized with ${self.starting_balance_usd:.2f} USD")
            return
        
        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)
            
            # Load balances
            self.balances = {
                curr: PaperBalance.from_dict(bal)
                for curr, bal in data.get('balances', {}).items()
            }
            
            # Load trades
            self.trades = [
                PaperTrade.from_dict(t)
                for t in data.get('trades', [])
            ]
            
            # Load orders
            self.orders = data.get('orders', [])
            
            self.starting_balance_usd = data.get('starting_balance_usd', 10000.0)
            
            logger.info(f"[PAPER-LEDGER] Loaded: {len(self.balances)} currencies, {len(self.trades)} trades")
        
        except Exception as e:
            logger.error(f"[PAPER-LEDGER] Failed to load state: {e}")
            raise
    
    def save(self) -> None:
        """Save paper ledger to disk"""
        try:
            data = {
                'balances': {curr: bal.to_dict() for curr, bal in self.balances.items()},
                'trades': [t.to_dict() for t in self.trades],
                'orders': self.orders,
                'starting_balance_usd': self.starting_balance_usd,
                'last_saved': time.time()
            }
            
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
            
        except Exception as e:
            logger.error(f"[PAPER-LEDGER] Failed to save state: {e}")
    
    def get_balances(self) -> Dict[str, Dict[str, float]]:
        """Get all balances in format compatible with status_service"""
        result = {}
        for curr, bal in self.balances.items():
            result[curr] = {
                'free': bal.free,
                'used': bal.locked,
                'total': bal.total,
                'last_updated': bal.last_updated
            }
        return result
    
    def get_trades(self, since: Optional[float] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get trade history"""
        trades = self.trades
        
        # Filter by time
        if since:
            trades = [t for t in trades if t.timestamp >= since]
        
        # Sort by timestamp descending and limit
        trades = sorted(trades, key=lambda t: t.timestamp, reverse=True)
        trades = trades[:limit]
        
        # Convert to dicts
        return [t.to_dict() for t in trades]
    
    def record_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        fee_pct: float = 0.0026  # 0.26% taker fee
    ) -> str:
        """Record a paper trade and update balances"""
        now = time.time()
        trade_id = f"paper_{int(now * 1000)}"
        order_id = f"order_{int(now * 1000)}"
        
        cost = price * quantity
        fee = cost * fee_pct
        
        trade = PaperTrade(
            trade_id=trade_id,
            order_id=order_id,
            timestamp=now,
            datetime_utc=datetime.now(tz=timezone.utc).isoformat(),
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            cost=cost,
            fee=fee
        )
        
        # Update balances based on trade
        base_currency, quote_currency = symbol.split('/')
        
        if side == 'buy':
            # Spend quote currency (USD)
            total_cost = cost + fee
            if quote_currency not in self.balances:
                logger.error(f"[PAPER-LEDGER] Insufficient {quote_currency} balance")
                raise ValueError(f"Insufficient {quote_currency} balance")
            
            if self.balances[quote_currency].free < total_cost:
                logger.error(f"[PAPER-LEDGER] Insufficient {quote_currency}: need ${total_cost:.2f}, have ${self.balances[quote_currency].free:.2f}")
                raise ValueError(f"Insufficient {quote_currency} balance")
            
            self.balances[quote_currency].free -= total_cost
            self.balances[quote_currency].total -= total_cost
            
            # Add base currency
            if base_currency not in self.balances:
                self.balances[base_currency] = PaperBalance(
                    currency=base_currency,
                    free=0.0,
                    locked=0.0,
                    total=0.0,
                    last_updated=now
                )
            
            self.balances[base_currency].free += quantity
            self.balances[base_currency].total += quantity
        
        else:  # sell
            # Spend base currency
            if base_currency not in self.balances:
                raise ValueError(f"Insufficient {base_currency} balance")
            
            if self.balances[base_currency].free < quantity:
                raise ValueError(f"Insufficient {base_currency} balance")
            
            self.balances[base_currency].free -= quantity
            self.balances[base_currency].total -= quantity
            
            # Add quote currency (USD)
            net_proceeds = cost - fee
            if quote_currency not in self.balances:
                self.balances[quote_currency] = PaperBalance(
                    currency=quote_currency,
                    free=0.0,
                    locked=0.0,
                    total=0.0,
                    last_updated=now
                )
            
            self.balances[quote_currency].free += net_proceeds
            self.balances[quote_currency].total += net_proceeds
        
        # Update timestamps
        for bal in self.balances.values():
            bal.last_updated = now
        
        # Save trade
        self.trades.append(trade)
        self.save()
        
        logger.info(f"[PAPER-LEDGER] Recorded {side} {quantity} {symbol} @ ${price:.2f} (fee: ${fee:.2f})")
        return trade_id
    
    def reset(self, starting_balance: float = 10000.0) -> None:
        """Reset paper ledger to starting state"""
        self.starting_balance_usd = starting_balance
        self.balances = {
            "USD": PaperBalance(
                currency="USD",
                free=starting_balance,
                locked=0.0,
                total=starting_balance,
                last_updated=time.time()
            )
        }
        self.trades = []
        self.orders = []
        self.save()
        logger.info(f"[PAPER-LEDGER] Reset to ${starting_balance:.2f} USD")


# Global paper ledger instance
_paper_ledger = PaperLedger()


def get_balances() -> Dict[str, Dict[str, Any]]:
    """
    Get balances from the correct source based on mode.
    Returns normalized format: {currency: {free, used, total, usd_value, last_updated}}
    
    CRITICAL:
    - LIVE mode: Fetches from Kraken API
    - PAPER mode: Fetches from paper ledger
    """
    mode = get_mode_str()
    now = time.time()
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    
    if mode == "live":
        # LIVE MODE: Fetch from Kraken API
        try:
            ex = get_exchange()
            balances_raw = ex.fetch_balance()
            
            balances = {}
            for currency, balance in balances_raw.items():
                if currency in ('free', 'used', 'total', 'info'):
                    continue
                
                free = balance.get('free', 0.0) or 0.0
                used = balance.get('used', 0.0) or 0.0
                total = balance.get('total', 0.0) or 0.0
                
                if total == 0:
                    continue
                
                usd_value = 0.0
                if currency == 'USD':
                    usd_value = total
                else:
                    try:
                        symbol = f"{currency}/USD"
                        ticker = ex.fetch_ticker(symbol)
                        price = ticker.get('last', 0.0)
                        usd_value = total * price
                    except:
                        usd_value = 0.0
                
                balances[currency] = {
                    'free': free,
                    'used': used,
                    'total': total,
                    'usd_value': usd_value,
                    'last_updated': now_iso
                }
            
            return balances
        
        except Exception as e:
            logger.error(f"[ACCOUNT-STATE] Failed to fetch live balances: {e}")
            return {}
    
    else:
        # PAPER MODE: Use paper ledger
        balances = _paper_ledger.get_balances()
        
        # Calculate USD value for each currency
        balances_with_value = {}
        for currency, bal in balances.items():
            total = bal['total']
            
            if currency == 'USD':
                usd_value = total
            else:
                try:
                    ex = get_exchange()
                    symbol = f"{currency}/USD"
                    ticker = ex.fetch_ticker(symbol)
                    price = ticker.get('last', 0.0)
                    usd_value = total * price
                except:
                    usd_value = 0.0
            
            balances_with_value[currency] = {
                'free': bal['free'],
                'used': bal['used'],
                'total': bal['total'],
                'usd_value': usd_value,
                'last_updated': now_iso
            }
        
        return balances_with_value


def get_portfolio_snapshot() -> Dict[str, Any]:
    """
    Get complete portfolio snapshot from the correct source based on mode.
    
    Returns:
        - mode: "live" or "paper"
        - total_equity_usd: Total portfolio value in USD
        - balances: Dict of {currency: {free, used, total, usd_value, last_updated}}
        - timestamp: When snapshot was taken
        - data_source: "Kraken API" or "Paper Ledger"
    """
    mode = get_mode_str()
    now = time.time()
    
    try:
        balances = get_balances()
        total_equity = sum(bal.get('usd_value', 0) for bal in balances.values())
        
        return {
            'mode': mode,
            'total_equity_usd': total_equity,
            'balances': balances,
            'timestamp': now,
            'datetime_utc': datetime.now(tz=timezone.utc).isoformat(),
            'data_source': 'Kraken API' if mode == 'live' else 'Paper Ledger',
            'starting_balance': _paper_ledger.starting_balance_usd if mode == 'paper' else None
        }
    
    except Exception as e:
        logger.error(f"[ACCOUNT-STATE] Failed to get portfolio snapshot: {e}")
        return {
            'mode': mode,
            'error': str(e),
            'timestamp': now
        }


def get_trade_history(since: Optional[float] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get trade history from the correct source based on mode.
    
    CRITICAL:
    - LIVE mode: Fetch from Kraken API
    - PAPER mode: Fetch from paper ledger
    
    Returns list of trades with:
        - trade_id
        - order_id
        - timestamp
        - datetime_utc
        - symbol
        - side ('buy' or 'sell')
        - price
        - quantity
        - cost (quantity * price)
        - fee
    """
    mode = get_mode_str()
    
    if mode == "live":
        # LIVE MODE: Fetch from Kraken API
        try:
            ex = get_exchange()
            
            # Convert to milliseconds for Kraken API
            since_ms = int(since * 1000) if since else None
            
            # Fetch from Kraken
            trades_raw = ex.fetch_my_trades(since=since_ms, limit=limit)
            
            # Convert to our format
            trades = []
            for t in trades_raw:
                trades.append({
                    'trade_id': t.get('id', ''),
                    'order_id': t.get('order', ''),
                    'timestamp': t.get('timestamp', 0) / 1000,  # Convert ms to seconds
                    'datetime_utc': t.get('datetime', ''),
                    'symbol': t.get('symbol', ''),
                    'side': t.get('side', ''),
                    'price': t.get('price', 0),
                    'quantity': t.get('amount', 0),
                    'cost': t.get('cost', 0),
                    'fee': t.get('fee', {}).get('cost', 0),
                    'fee_currency': t.get('fee', {}).get('currency', 'USD')
                })
            
            return trades
        
        except Exception as e:
            logger.error(f"[ACCOUNT-STATE] Failed to fetch live trades: {e}")
            return []
    
    else:
        # PAPER MODE: Use paper ledger
        return _paper_ledger.get_trades(since=since, limit=limit)


def get_trading_mode() -> Literal["live", "paper"]:
    """Get current trading mode"""
    return get_mode_str()  # type: ignore


def get_paper_ledger() -> PaperLedger:
    """Get paper ledger instance (for paper trading operations)"""
    return _paper_ledger


# Initialize on import
logger.info(f"[ACCOUNT-STATE] Initialized in {get_trading_mode().upper()} mode")
