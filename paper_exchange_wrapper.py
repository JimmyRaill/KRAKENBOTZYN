"""
paper_exchange_wrapper.py - Intercepts ccxt calls and routes to PaperTradingSimulator

This wrapper sits between commands.py and the real ccxt exchange.
In paper mode, it routes all order creation and data fetching to the simulator.
In live mode, it passes through to real Kraken API.
"""

import json
import time
import uuid
from typing import Dict, List, Optional, Any
from loguru import logger
from paper_trading import PaperTradingSimulator


class PaperOrder:
    """Represents a paper order (market, limit, or stop)"""
    def __init__(
        self,
        order_id: str,
        symbol: str,
        order_type: str,  # 'market', 'limit', 'stop'
        side: str,  # 'buy' or 'sell'
        amount: float,
        price: Optional[float] = None,  # For limit/stop orders
        stop_price: Optional[float] = None,  # For stop orders
        status: str = 'open',
        timestamp: float = None
    ):
        self.order_id = order_id
        self.symbol = symbol
        self.order_type = order_type
        self.side = side
        self.amount = amount
        self.price = price
        self.stop_price = stop_price
        self.status = status
        self.timestamp = timestamp or time.time()
    
    def to_ccxt_format(self) -> Dict[str, Any]:
        """Convert to ccxt format for compatibility"""
        return {
            'id': self.order_id,
            'orderId': self.order_id,
            'symbol': self.symbol,
            'type': self.order_type,
            'side': self.side,
            'amount': self.amount,
            'price': self.price or 0.0,
            'stopPrice': self.stop_price,
            'status': self.status,
            'timestamp': int(self.timestamp * 1000),
            'datetime': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(self.timestamp))
        }
    
    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage"""
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'order_type': self.order_type,
            'side': self.side,
            'amount': self.amount,
            'price': self.price,
            'stop_price': self.stop_price,
            'status': self.status,
            'timestamp': self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PaperOrder":
        """Deserialize from dict"""
        return cls(**data)


class PaperExchangeWrapper:
    """
    Wraps a ccxt exchange instance and intercepts calls in paper mode.
    
    In PAPER mode:
    - Stores all orders in paper_orders.json
    - Routes to PaperTradingSimulator for execution
    - Returns paper data for queries (fetch_open_orders, fetch_balance)
    
    In LIVE mode:
    - Passes through to real ccxt exchange
    """
    
    def __init__(self, ccxt_exchange, is_paper_mode: bool = True):
        self._exchange = ccxt_exchange
        self._is_paper = is_paper_mode
        self._simulator = PaperTradingSimulator() if is_paper_mode else None
        self._paper_orders: Dict[str, PaperOrder] = {}
        self._orders_file = "paper_orders.json"
        
        if is_paper_mode:
            self._load_orders()
            logger.info(f"[PAPER-WRAPPER] Initialized with {len(self._paper_orders)} saved orders")
    
    def _load_orders(self):
        """Load paper orders from JSON file"""
        try:
            with open(self._orders_file, 'r') as f:
                data = json.load(f)
                self._paper_orders = {
                    k: PaperOrder.from_dict(v) for k, v in data.items()
                }
        except FileNotFoundError:
            logger.info("[PAPER-WRAPPER] No saved orders found - starting fresh")
        except Exception as e:
            logger.error(f"[PAPER-WRAPPER] Error loading orders: {e}")
    
    def _save_orders(self):
        """Save paper orders to JSON file"""
        try:
            data = {k: v.to_dict() for k, v in self._paper_orders.items()}
            with open(self._orders_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"[PAPER-WRAPPER] Error saving orders: {e}")
    
    def _generate_order_id(self) -> str:
        """Generate a unique order ID"""
        return f"PAPER-{str(uuid.uuid4())[:8].upper()}"
    
    def _log_execution(self, mode: str, symbol: str, side: str, size: float, 
                      sl: Optional[float] = None, tp: Optional[float] = None,
                      success: bool = True, error: str = None):
        """Comprehensive logging for all trade executions"""
        log_msg = (
            f"[TRADE-EXEC] mode={mode} | symbol={symbol} | side={side} | "
            f"size={size} | SL={sl} | TP={tp} | success={success}"
        )
        if error:
            log_msg += f" | error={error}"
        
        logger.info(log_msg)
    
    def create_market_buy_order(self, symbol: str, amount: float, params: dict = None):
        """Create market buy order (paper or live)"""
        if not self._is_paper:
            return self._exchange.create_market_buy_order(symbol, amount, params)
        
        # Paper mode: simulate market buy
        try:
            # Get current market price
            ticker = self._exchange.fetch_ticker(symbol)
            market_price = ticker['last']
            
            # Open position in simulator
            success, msg, position = self._simulator.open_position(
                symbol=symbol,
                side='long',
                quantity=amount,
                market_price=market_price,
                is_maker=False
            )
            
            if not success:
                self._log_execution('PAPER', symbol, 'buy', amount, success=False, error=msg)
                raise Exception(msg)
            
            # Create and store order
            order_id = self._generate_order_id()
            order = PaperOrder(
                order_id=order_id,
                symbol=symbol,
                order_type='market',
                side='buy',
                amount=amount,
                price=market_price,
                status='closed'  # Market orders fill immediately
            )
            
            self._paper_orders[order_id] = order
            self._save_orders()
            
            self._log_execution('PAPER', symbol, 'buy', amount, success=True)
            logger.info(msg)
            
            return order.to_ccxt_format()
        
        except Exception as e:
            self._log_execution('PAPER', symbol, 'buy', amount, success=False, error=str(e))
            raise
    
    def create_market_sell_order(self, symbol: str, amount: float, params: dict = None):
        """Create market sell order (paper or live)"""
        if not self._is_paper:
            return self._exchange.create_market_sell_order(symbol, amount, params)
        
        # Paper mode: close position
        try:
            ticker = self._exchange.fetch_ticker(symbol)
            market_price = ticker['last']
            
            success, msg, pnl = self._simulator.close_position(
                symbol=symbol,
                market_price=market_price,
                reason='manual',
                is_maker=False
            )
            
            if not success:
                self._log_execution('PAPER', symbol, 'sell', amount, success=False, error=msg)
                raise Exception(msg)
            
            # Create and store order
            order_id = self._generate_order_id()
            order = PaperOrder(
                order_id=order_id,
                symbol=symbol,
                order_type='market',
                side='sell',
                amount=amount,
                price=market_price,
                status='closed'
            )
            
            self._paper_orders[order_id] = order
            self._save_orders()
            
            self._log_execution('PAPER', symbol, 'sell', amount, success=True)
            logger.info(f"{msg} | PnL=${pnl:.2f}")
            
            return order.to_ccxt_format()
        
        except Exception as e:
            self._log_execution('PAPER', symbol, 'sell', amount, success=False, error=str(e))
            raise
    
    def create_limit_buy_order(self, symbol: str, amount: float, price: float, params: dict = None):
        """Create limit buy order (paper or live)"""
        if not self._is_paper:
            return self._exchange.create_limit_buy_order(symbol, amount, price, params)
        
        # Paper mode: store as open limit order
        try:
            order_id = self._generate_order_id()
            order = PaperOrder(
                order_id=order_id,
                symbol=symbol,
                order_type='limit',
                side='buy',
                amount=amount,
                price=price,
                status='open'
            )
            
            self._paper_orders[order_id] = order
            self._save_orders()
            
            self._log_execution('PAPER', symbol, 'buy_limit', amount, success=True)
            logger.info(f"[PAPER] Created limit buy order {order_id} for {amount} {symbol} @ ${price}")
            
            return order.to_ccxt_format()
        
        except Exception as e:
            self._log_execution('PAPER', symbol, 'buy_limit', amount, success=False, error=str(e))
            raise
    
    def create_limit_sell_order(self, symbol: str, amount: float, price: float, params: dict = None):
        """Create limit sell order (paper or live)"""
        if not self._is_paper:
            return self._exchange.create_limit_sell_order(symbol, amount, price, params)
        
        # Paper mode: store as open limit order (could be TP from bracket)
        try:
            order_id = self._generate_order_id()
            order = PaperOrder(
                order_id=order_id,
                symbol=symbol,
                order_type='limit',
                side='sell',
                amount=amount,
                price=price,
                status='open'
            )
            
            self._paper_orders[order_id] = order
            self._save_orders()
            
            self._log_execution('PAPER', symbol, 'sell_limit', amount, tp=price, success=True)
            logger.info(f"[PAPER] Created limit sell order (TP) {order_id} for {amount} {symbol} @ ${price}")
            
            return order.to_ccxt_format()
        
        except Exception as e:
            self._log_execution('PAPER', symbol, 'sell_limit', amount, success=False, error=str(e))
            raise
    
    def create_order(self, symbol: str, order_type: str, side: str, amount: float, 
                    price: Optional[float] = None, params: dict = None):
        """Generic create_order (handles stop orders)"""
        if not self._is_paper:
            return self._exchange.create_order(symbol, order_type, side, amount, price, params)
        
        # Paper mode: handle stop orders
        try:
            params = params or {}
            stop_price = params.get('stopPrice')
            
            if stop_price and order_type == 'market':
                # This is a stop-loss order
                order_id = self._generate_order_id()
                order = PaperOrder(
                    order_id=order_id,
                    symbol=symbol,
                    order_type='stop',
                    side=side,
                    amount=amount,
                    stop_price=stop_price,
                    status='open'
                )
                
                self._paper_orders[order_id] = order
                self._save_orders()
                
                self._log_execution('PAPER', symbol, f'{side}_stop', amount, sl=stop_price, success=True)
                logger.info(f"[PAPER] Created stop order (SL) {order_id} for {amount} {symbol} @ stop ${stop_price}")
                
                return order.to_ccxt_format()
            
            # Fallback to regular order creation
            if order_type == 'market':
                if side == 'buy':
                    return self.create_market_buy_order(symbol, amount, params)
                else:
                    return self.create_market_sell_order(symbol, amount, params)
            elif order_type == 'limit':
                if side == 'buy':
                    return self.create_limit_buy_order(symbol, amount, price, params)
                else:
                    return self.create_limit_sell_order(symbol, amount, price, params)
        
        except Exception as e:
            self._log_execution('PAPER', symbol, f'{side}_{order_type}', amount, success=False, error=str(e))
            raise
    
    def fetch_open_orders(self, symbol: str = None, since: int = None, limit: int = None, params: dict = None):
        """Fetch open orders (paper or live)"""
        if not self._is_paper:
            if symbol:
                return self._exchange.fetch_open_orders(symbol, since, limit, params)
            else:
                return self._exchange.fetch_open_orders()
        
        # Paper mode: return paper orders with status='open'
        open_orders = [
            order.to_ccxt_format()
            for order in self._paper_orders.values()
            if order.status == 'open' and (not symbol or order.symbol == symbol)
        ]
        
        logger.debug(f"[PAPER-WRAPPER] fetch_open_orders: {len(open_orders)} open orders")
        return open_orders
    
    def fetch_balance(self, params: dict = None):
        """Fetch balance (paper or live)"""
        if not self._is_paper:
            return self._exchange.fetch_balance(params)
        
        # Paper mode: return simulator balance
        balance = {
            'USD': {
                'free': self._simulator.balance,
                'used': 0.0,
                'total': self._simulator.balance
            },
            'free': {'USD': self._simulator.balance},
            'used': {'USD': 0.0},
            'total': {'USD': self._simulator.balance}
        }
        
        # Add position values
        for symbol, position in self._simulator.open_positions.items():
            base_currency = symbol.split('/')[0]
            position_value = position.quantity
            balance[base_currency] = {
                'free': position_value,
                'used': 0.0,
                'total': position_value
            }
            balance['free'][base_currency] = position_value
            balance['total'][base_currency] = position_value
        
        return balance
    
    def cancel_order(self, order_id: str, symbol: str = None, params: dict = None):
        """Cancel an order"""
        if not self._is_paper:
            return self._exchange.cancel_order(order_id, symbol, params)
        
        # Paper mode: mark as cancelled
        if order_id in self._paper_orders:
            self._paper_orders[order_id].status = 'cancelled'
            self._save_orders()
            logger.info(f"[PAPER] Cancelled order {order_id}")
            return {'id': order_id, 'status': 'cancelled'}
        else:
            raise Exception(f"Order {order_id} not found")
    
    def __getattr__(self, name):
        """Pass through all other methods to the underlying exchange"""
        return getattr(self._exchange, name)
