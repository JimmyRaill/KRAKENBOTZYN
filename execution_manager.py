"""
execution_manager.py - Centralized market order execution for MARKET_ONLY mode

Handles all market buy/sell order placement, confirmation, and logging.
Completely bypasses bracket order system when USE_BRACKETS=False.
"""

import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from loguru import logger

from exchange_manager import get_exchange, get_mode_str
from account_state import get_account_state
from rate_limiter import wait_for_rate_limit, record_order_executed


# Telemetry logging (optional - graceful degradation if not available)
try:
    from telemetry_db import log_trade
    TELEMETRY_ENABLED = True
except ImportError:
    TELEMETRY_ENABLED = False
    logger.warning("[EXEC-MGR] Telemetry not available - trades will not be logged to telemetry_db")

# Evaluation logging
try:
    from evaluation_log import register_executed_order
    EVAL_LOG_ENABLED = True
except ImportError:
    EVAL_LOG_ENABLED = False
    logger.warning("[EXEC-MGR] Evaluation log not available - orders will not be logged to executed_orders")


class ExecutionResult:
    """Result of market order execution"""
    def __init__(
        self,
        success: bool,
        order_id: Optional[str] = None,
        filled_qty: float = 0.0,
        fill_price: float = 0.0,
        total_cost: float = 0.0,
        fee: float = 0.0,
        fee_currency: str = "USD",
        error: Optional[str] = None,
        raw_response: Optional[Dict[str, Any]] = None
    ):
        self.success = success
        self.order_id = order_id
        self.filled_qty = filled_qty
        self.fill_price = fill_price
        self.total_cost = total_cost
        self.fee = fee
        self.fee_currency = fee_currency
        self.error = error
        self.raw_response = raw_response
    
    def __str__(self):
        if self.success:
            return (
                f"ExecutionResult(OK: {self.order_id}, "
                f"qty={self.filled_qty:.6f} @ ${self.fill_price:.4f}, "
                f"cost=${self.total_cost:.2f}, fee=${self.fee:.4f})"
            )
        else:
            return f"ExecutionResult(FAILED: {self.error})"


def execute_market_entry(
    symbol: str,
    size_usd: float,
    source: str = "autopilot",
    atr: Optional[float] = None,
    reason: Optional[str] = None
) -> ExecutionResult:
    """
    Execute market BUY order to open a long position.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        size_usd: Target position size in USD (will be converted to quantity)
        source: Order source ("autopilot", "command", "force_test", etc.)
        atr: ATR value for logging (optional)
        reason: Trade reason for telemetry (optional)
    
    Returns:
        ExecutionResult with order details and success status
    """
    logger.info(f"[MARKET-ENTRY] {symbol} - Attempting market BUY for ${size_usd:.2f} (source={source})")
    
    try:
        exchange = get_exchange()
        mode_str = get_mode_str()
        
        # Get current price
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker.get('last') or ticker.get('close') or ticker.get('ask', 0)
        
        if not current_price or current_price <= 0:
            error_msg = f"Invalid price for {symbol}: {current_price}"
            logger.error(f"[MARKET-ENTRY] {error_msg}")
            return ExecutionResult(success=False, error=error_msg)
        
        # Calculate quantity
        quantity = size_usd / current_price
        
        # Check exchange minimums
        market = exchange.market(symbol) or {}
        limits = market.get("limits") or {}
        min_amt = float((limits.get("amount") or {}).get("min", 0) or 0)
        min_cost = float((limits.get("cost") or {}).get("min", 0) or 0)
        
        if min_amt > 0 and quantity < min_amt:
            logger.warning(f"[MARKET-ENTRY] {symbol} - Adjusting quantity from {quantity:.6f} to minimum {min_amt:.6f}")
            quantity = min_amt * 1.01  # 1% buffer
        
        if min_cost > 0 and (quantity * current_price) < min_cost:
            min_qty = min_cost / current_price
            logger.warning(f"[MARKET-ENTRY] {symbol} - Adjusting quantity from {quantity:.6f} to meet min_cost ${min_cost:.2f} ({min_qty:.6f})")
            quantity = min_qty * 1.01  # 1% buffer
        
        # Precision formatting
        quantity = float(exchange.amount_to_precision(symbol, quantity))
        
        logger.info(f"[MARKET-ENTRY] {symbol} - Placing market BUY: {quantity:.6f} units @ ~${current_price:.4f}")
        
        # Rate limiting check BEFORE order placement
        rate_limit_ok = wait_for_rate_limit(symbol=symbol, max_wait_seconds=5.0)
        if not rate_limit_ok:
            error_msg = f"Rate limit timeout - cannot execute order safely"
            logger.error(f"[MARKET-ENTRY] {error_msg}")
            return ExecutionResult(success=False, error=error_msg)
        
        # Execute market buy
        order = exchange.create_market_buy_order(symbol, quantity)
        
        # Record order execution for rate limiting
        record_order_executed(symbol=symbol)
        
        # Extract fill details
        order_id = order.get('id', 'UNKNOWN')
        filled_qty = float(order.get('filled', 0))
        fill_price = float(order.get('average') or order.get('price') or current_price)
        total_cost = float(order.get('cost', 0))
        
        # Extract fee (ccxt structure varies)
        fee_dict = order.get('fee') or {}
        fee = float(fee_dict.get('cost', 0))
        fee_currency = fee_dict.get('currency', 'USD')
        
        logger.info(
            f"[MARKET-ENTRY] ✅ {symbol} FILLED: {order_id}, "
            f"qty={filled_qty:.6f} @ ${fill_price:.4f}, "
            f"cost=${total_cost:.2f}, fee=${fee:.4f} {fee_currency}"
        )
        
        # Log to executed_orders table
        if EVAL_LOG_ENABLED:
            try:
                register_executed_order(
                    order_id=order_id,
                    symbol=symbol,
                    side='buy',
                    order_type='market',
                    quantity=filled_qty,
                    price=fill_price,
                    status='filled',
                    trading_mode=mode_str,
                    source=source,
                    timestamp_utc=datetime.now(timezone.utc).isoformat()
                )
                logger.debug(f"[MARKET-ENTRY] Logged to executed_orders: {order_id}")
            except Exception as log_err:
                logger.error(f"[MARKET-ENTRY] Failed to log to executed_orders: {log_err}")
        
        # Log to telemetry
        if TELEMETRY_ENABLED and reason:
            try:
                log_trade(
                    symbol=symbol,
                    side='long',
                    entry_price=fill_price,
                    quantity=filled_qty,
                    reason=reason,
                    atr=atr,
                    pnl=None,  # Entry doesn't have PnL yet
                    mode=mode_str
                )
                logger.debug(f"[MARKET-ENTRY] Logged to telemetry_db")
            except Exception as telem_err:
                logger.error(f"[MARKET-ENTRY] Failed to log to telemetry: {telem_err}")
        
        return ExecutionResult(
            success=True,
            order_id=order_id,
            filled_qty=filled_qty,
            fill_price=fill_price,
            total_cost=total_cost,
            fee=fee,
            fee_currency=fee_currency,
            raw_response=order
        )
        
    except Exception as e:
        error_msg = f"Market entry failed: {str(e)}"
        logger.error(f"[MARKET-ENTRY] ❌ {symbol} - {error_msg}")
        return ExecutionResult(success=False, error=error_msg)


def execute_market_exit(
    symbol: str,
    quantity: Optional[float] = None,
    full_position: bool = True,
    source: str = "autopilot",
    reason: Optional[str] = None
) -> ExecutionResult:
    """
    Execute market SELL order to close/reduce a long position.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        quantity: Specific quantity to sell (None = auto-detect from position)
        full_position: If True and quantity is None, sell entire position
        source: Order source ("autopilot", "command", "regime_exit", etc.)
        reason: Exit reason for telemetry (optional)
    
    Returns:
        ExecutionResult with order details and success status
    """
    logger.info(f"[MARKET-EXIT] {symbol} - Attempting market SELL (source={source}, full_position={full_position})")
    
    try:
        exchange = get_exchange()
        mode_str = get_mode_str()
        account_state = get_account_state()
        
        # Determine quantity to sell
        if quantity is None:
            if full_position:
                # Fetch current position from account_state
                positions = account_state.get_positions()
                position = positions.get(symbol)
                
                if not position or position.get('quantity', 0) <= 0:
                    error_msg = f"No open position for {symbol}"
                    logger.warning(f"[MARKET-EXIT] {error_msg}")
                    return ExecutionResult(success=False, error=error_msg)
                
                quantity = float(position['quantity'])
                logger.info(f"[MARKET-EXIT] {symbol} - Auto-detected position: {quantity:.6f} units")
            else:
                error_msg = "Quantity must be specified if full_position=False"
                logger.error(f"[MARKET-EXIT] {error_msg}")
                return ExecutionResult(success=False, error=error_msg)
        
        # Precision formatting
        quantity = float(exchange.amount_to_precision(symbol, quantity))
        
        # Get current price for logging
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker.get('last') or ticker.get('close') or ticker.get('bid', 0)
        
        logger.info(f"[MARKET-EXIT] {symbol} - Placing market SELL: {quantity:.6f} units @ ~${current_price:.4f}")
        
        # Rate limiting check BEFORE order placement
        rate_limit_ok = wait_for_rate_limit(symbol=symbol, max_wait_seconds=5.0)
        if not rate_limit_ok:
            error_msg = f"Rate limit timeout - cannot execute order safely"
            logger.error(f"[MARKET-EXIT] {error_msg}")
            return ExecutionResult(success=False, error=error_msg)
        
        # Execute market sell
        order = exchange.create_market_sell_order(symbol, quantity)
        
        # Record order execution for rate limiting
        record_order_executed(symbol=symbol)
        
        # Extract fill details
        order_id = order.get('id', 'UNKNOWN')
        filled_qty = float(order.get('filled', 0))
        fill_price = float(order.get('average') or order.get('price') or current_price)
        total_proceeds = float(order.get('cost', 0))
        
        # Extract fee
        fee_dict = order.get('fee') or {}
        fee = float(fee_dict.get('cost', 0))
        fee_currency = fee_dict.get('currency', 'USD')
        
        logger.info(
            f"[MARKET-EXIT] ✅ {symbol} FILLED: {order_id}, "
            f"qty={filled_qty:.6f} @ ${fill_price:.4f}, "
            f"proceeds=${total_proceeds:.2f}, fee=${fee:.4f} {fee_currency}"
        )
        
        # Log to executed_orders table
        if EVAL_LOG_ENABLED:
            try:
                register_executed_order(
                    order_id=order_id,
                    symbol=symbol,
                    side='sell',
                    order_type='market',
                    quantity=filled_qty,
                    price=fill_price,
                    status='filled',
                    trading_mode=mode_str,
                    source=source,
                    timestamp_utc=datetime.now(timezone.utc).isoformat()
                )
                logger.debug(f"[MARKET-EXIT] Logged to executed_orders: {order_id}")
            except Exception as log_err:
                logger.error(f"[MARKET-EXIT] Failed to log to executed_orders: {log_err}")
        
        # Log to telemetry (exit - PnL will be calculated by telemetry system)
        if TELEMETRY_ENABLED and reason:
            try:
                log_trade(
                    symbol=symbol,
                    side='exit',
                    entry_price=None,  # Exit doesn't have entry price here
                    quantity=filled_qty,
                    reason=reason,
                    exit_price=fill_price,
                    mode=mode_str
                )
                logger.debug(f"[MARKET-EXIT] Logged to telemetry_db")
            except Exception as telem_err:
                logger.error(f"[MARKET-EXIT] Failed to log to telemetry: {telem_err}")
        
        return ExecutionResult(
            success=True,
            order_id=order_id,
            filled_qty=filled_qty,
            fill_price=fill_price,
            total_cost=total_proceeds,
            fee=fee,
            fee_currency=fee_currency,
            raw_response=order
        )
        
    except Exception as e:
        error_msg = f"Market exit failed: {str(e)}"
        logger.error(f"[MARKET-EXIT] ❌ {symbol} - {error_msg}")
        return ExecutionResult(success=False, error=error_msg)


def get_position_quantity(symbol: str) -> float:
    """
    Get current position quantity for a symbol.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
    
    Returns:
        Position quantity (0 if no position)
    """
    try:
        account_state = get_account_state()
        positions = account_state.get_positions()
        position = positions.get(symbol)
        
        if position:
            return float(position.get('quantity', 0))
        return 0.0
        
    except Exception as e:
        logger.error(f"[EXEC-MGR] Failed to get position for {symbol}: {e}")
        return 0.0


def has_open_position(symbol: str) -> bool:
    """
    Check if there's an open position for a symbol.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
    
    Returns:
        True if position exists and quantity > 0
    """
    qty = get_position_quantity(symbol)
    return qty > 0
