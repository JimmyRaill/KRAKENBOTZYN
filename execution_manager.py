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
from rate_limiter import wait_for_rate_limit, record_order_executed
from dust_prevention import get_dust_prevention


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
        
        # DUST PREVENTION: Validate against Kraken minimums with 7% buffer
        dust_prevention = get_dust_prevention()
        is_valid, dust_reason = dust_prevention.validate_order_size(symbol, quantity, current_price, apply_buffer=True)
        
        if not is_valid:
            # Calculate minimum required quantity
            min_qty = dust_prevention.calculate_minimum_trade_size(symbol, current_price, apply_buffer=True)
            if min_qty:
                logger.warning(f"[MARKET-ENTRY] {symbol} - Dust prevention: {dust_reason}")
                logger.warning(f"[MARKET-ENTRY] {symbol} - Adjusting quantity from {quantity:.8f} to {min_qty:.8f} (7% above minimum)")
                quantity = min_qty
            else:
                error_msg = f"Dust prevention: {dust_reason} - could not calculate safe minimum"
                logger.error(f"[MARKET-ENTRY] ❌ {symbol} - {error_msg}")
                return ExecutionResult(success=False, error=error_msg)
        
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
        
        # Extract fill details with defensive None checks
        order_id = order.get('id', 'UNKNOWN')
        filled_qty = float(order.get('filled') or 0)
        
        # Defensive: ensure fill_price never gets None
        avg_price = order.get('average')
        order_price = order.get('price')
        fill_price = float(avg_price if avg_price is not None else (order_price if order_price is not None else current_price))
        
        total_cost = float(order.get('cost') or 0)
        
        # Extract fee (ccxt structure varies)
        fee_dict = order.get('fee') or {}
        fee = float(fee_dict.get('cost') or 0)
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
        if TELEMETRY_ENABLED:
            try:
                log_trade(
                    symbol=symbol,
                    side='buy',  # FIXED: Use 'buy', not 'long'
                    action='open',  # ADDED: Required parameter
                    quantity=filled_qty,
                    price=fill_price,  # FIXED: Use 'price', not 'entry_price'
                    usd_amount=filled_qty * fill_price if filled_qty and fill_price else None,
                    order_id=order.get('id') if order else None,
                    reason=reason or 'market_entry',
                    source=source,  # CRITICAL: Pass through source from caller (autopilot/command/force_test)
                    mode=mode_str,
                    trade_id=order.get('id') if order else None,
                    entry_price=fill_price,  # Lifecycle field
                    position_size=filled_qty
                )
                logger.debug(f"[MARKET-ENTRY] Logged to telemetry_db with source={source}")
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
        
        # Determine quantity to sell
        if quantity is None:
            if full_position:
                # Fetch current balance directly from exchange
                # symbol is like "BTC/USD", we need the base currency (BTC)
                base_currency = symbol.split('/')[0]
                
                balance = exchange.fetch_balance()
                available = balance.get(base_currency, {}).get('free', 0)
                
                if not available or available <= 0:
                    error_msg = f"No balance available for {base_currency} (symbol: {symbol})"
                    logger.warning(f"[MARKET-EXIT] {error_msg}")
                    return ExecutionResult(success=False, error=error_msg)
                
                quantity = float(available)
                logger.info(f"[MARKET-EXIT] {symbol} - Auto-detected available balance: {quantity:.6f} {base_currency}")
            else:
                error_msg = "Quantity must be specified if full_position=False"
                logger.error(f"[MARKET-EXIT] {error_msg}")
                return ExecutionResult(success=False, error=error_msg)
        
        # Precision formatting
        quantity = float(exchange.amount_to_precision(symbol, quantity))
        
        # Get current price for logging
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker.get('last') or ticker.get('close') or ticker.get('bid', 0)
        
        # DUST PREVENTION: Check if position is dust (below minimum tradeable size)
        dust_prevention = get_dust_prevention()
        is_dust = dust_prevention.is_dust_position(symbol, quantity, current_price)
        
        if is_dust:
            error_msg = f"Position is DUST (below Kraken minimum) - cannot sell {quantity:.8f} {symbol.split('/')[0]}"
            logger.warning(f"[MARKET-EXIT] ⚠️  {symbol} - {error_msg}")
            logger.warning(f"[MARKET-EXIT] {symbol} - Dust positions must be consolidated via Kraken 'Buy Crypto' button ($1 minimum)")
            return ExecutionResult(success=False, error=error_msg)
        
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
        
        # Extract fill details with defensive None checks
        order_id = order.get('id', 'UNKNOWN')
        filled_qty = float(order.get('filled') or 0)
        
        # Defensive: ensure fill_price never gets None
        avg_price = order.get('average')
        order_price = order.get('price')
        fill_price = float(avg_price if avg_price is not None else (order_price if order_price is not None else current_price))
        
        total_proceeds = float(order.get('cost') or 0)
        
        # Extract fee
        fee_dict = order.get('fee') or {}
        fee = float(fee_dict.get('cost') or 0)
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
        if TELEMETRY_ENABLED:
            try:
                log_trade(
                    symbol=symbol,
                    side='sell',
                    action='close',
                    quantity=filled_qty,
                    price=fill_price,
                    usd_amount=filled_qty * fill_price if filled_qty and fill_price else None,
                    order_id=order.get('id') if order else None,
                    reason=reason or 'market_exit',
                    source=source,
                    mode=mode_str,
                    trade_id=order.get('id') if order else None,
                    exit_price=fill_price,
                    position_size=filled_qty
                )
                logger.debug(f"[MARKET-EXIT] Logged to telemetry_db with source={source}")
            except Exception as telem_err:
                logger.error(f"[MARKET-EXIT] Failed to log to telemetry: {telem_err}")
        
        # Remove position from mental SL/TP tracker
        try:
            from position_tracker import remove_position
            removed = remove_position(symbol)
            if removed:
                logger.info(f"[MARKET-EXIT] Position removed from tracker: {symbol}")
            else:
                logger.debug(f"[MARKET-EXIT] No tracked position found for: {symbol}")
        except Exception as tracker_err:
            logger.warning(f"[MARKET-EXIT] Failed to remove position from tracker: {tracker_err}")
        
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


def execute_market_short_entry(
    symbol: str,
    size_usd: float,
    source: str = "autopilot",
    atr: Optional[float] = None,
    reason: Optional[str] = None
) -> ExecutionResult:
    """
    Execute margin market SELL order to open a short position.
    
    Uses Kraken margin trading with leverage parameter (capped at 2.0).
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        size_usd: Target position size in USD (will be converted to quantity)
        source: Order source ("autopilot", "command", etc.)
        atr: ATR value for logging (optional)
        reason: Trade reason for telemetry (optional)
    
    Returns:
        ExecutionResult with order details and success status
    """
    from margin_config import can_open_short, get_effective_leverage
    from account_state import get_balances
    
    logger.info(f"[SHORT-ENTRY] {symbol} - Attempting margin SHORT for ${size_usd:.2f} (source={source})")
    
    try:
        exchange = get_exchange()
        mode_str = get_mode_str()
        
        # Pre-flight check: Can we open shorts?
        balances = get_balances()
        total_equity = sum(bal.get('usd_value', 0) for bal in balances.values())
        
        can_short, check_msg = can_open_short(total_equity)
        if not can_short:
            error_msg = f"Short selling blocked: {check_msg}"
            logger.error(f"[SHORT-ENTRY] ❌ {error_msg}")
            return ExecutionResult(success=False, error=error_msg)
        
        logger.info(f"[SHORT-ENTRY] Pre-flight OK: {check_msg}")
        
        # Get current price
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker.get('last') or ticker.get('close') or ticker.get('bid', 0)
        
        if not current_price or current_price <= 0:
            error_msg = f"Invalid price for {symbol}: {current_price}"
            logger.error(f"[SHORT-ENTRY] {error_msg}")
            return ExecutionResult(success=False, error=error_msg)
        
        # Calculate quantity
        leverage = get_effective_leverage()
        quantity = size_usd / current_price
        
        # Check exchange minimums
        market = exchange.market(symbol) or {}
        limits = market.get("limits") or {}
        min_amt = float((limits.get("amount") or {}).get("min", 0) or 0)
        min_cost = float((limits.get("cost") or {}).get("min", 0) or 0)
        
        if min_amt > 0 and quantity < min_amt:
            logger.warning(f"[SHORT-ENTRY] {symbol} - Adjusting quantity from {quantity:.6f} to minimum {min_amt:.6f}")
            quantity = min_amt * 1.01  # 1% buffer
        
        if min_cost > 0 and (quantity * current_price) < min_cost:
            min_qty = min_cost / current_price
            logger.warning(f"[SHORT-ENTRY] {symbol} - Adjusting quantity from {quantity:.6f} to meet min_cost ${min_cost:.2f} ({min_qty:.6f})")
            quantity = min_qty * 1.01  # 1% buffer
        
        # Precision formatting
        quantity = float(exchange.amount_to_precision(symbol, quantity))
        
        logger.info(f"[SHORT-ENTRY] {symbol} - Placing margin SELL (SHORT): {quantity:.6f} units @ ~${current_price:.4f}, leverage={leverage}x")
        
        # Rate limiting check BEFORE order placement
        rate_limit_ok = wait_for_rate_limit(symbol=symbol, max_wait_seconds=5.0)
        if not rate_limit_ok:
            error_msg = f"Rate limit timeout - cannot execute order safely"
            logger.error(f"[SHORT-ENTRY] {error_msg}")
            return ExecutionResult(success=False, error=error_msg)
        
        # Execute margin short (market sell with leverage parameter)
        order_params = {'leverage': int(leverage)} if leverage > 1.0 else {}
        order = exchange.create_market_sell_order(symbol, quantity, order_params)
        
        # Record order execution for rate limiting
        record_order_executed(symbol=symbol)
        
        # Extract fill details with defensive None checks
        order_id = order.get('id', 'UNKNOWN')
        filled_qty = float(order.get('filled') or 0)
        
        avg_price = order.get('average')
        order_price = order.get('price')
        fill_price = float(avg_price if avg_price is not None else (order_price if order_price is not None else current_price))
        
        total_proceeds = float(order.get('cost') or 0)
        
        # Extract fee
        fee_dict = order.get('fee') or {}
        fee = float(fee_dict.get('cost') or 0)
        fee_currency = fee_dict.get('currency', 'USD')
        
        logger.info(
            f"[SHORT-ENTRY] ✅ {symbol} SHORT FILLED: {order_id}, "
            f"qty={filled_qty:.6f} @ ${fill_price:.4f}, "
            f"proceeds=${total_proceeds:.2f}, fee=${fee:.4f} {fee_currency}, "
            f"leverage={leverage}x"
        )
        
        # Log to executed_orders table
        if EVAL_LOG_ENABLED:
            try:
                register_executed_order(
                    order_id=order_id,
                    symbol=symbol,
                    side='sell_short',
                    order_type='market',
                    quantity=filled_qty,
                    price=fill_price,
                    status='filled',
                    trading_mode=mode_str,
                    source=source,
                    timestamp_utc=datetime.now(timezone.utc).isoformat()
                )
                logger.debug(f"[SHORT-ENTRY] Logged to executed_orders: {order_id}")
            except Exception as log_err:
                logger.error(f"[SHORT-ENTRY] Failed to log to executed_orders: {log_err}")
        
        # Log to telemetry
        if TELEMETRY_ENABLED:
            try:
                log_trade(
                    symbol=symbol,
                    side='sell',
                    action='open',
                    quantity=filled_qty,
                    price=fill_price,
                    usd_amount=filled_qty * fill_price if filled_qty and fill_price else None,
                    order_id=order.get('id') if order else None,
                    reason=reason or f'short_entry_margin_{leverage}x',
                    source=source,
                    mode=mode_str,
                    trade_id=order.get('id') if order else None,
                    entry_price=fill_price,
                    position_size=filled_qty
                )
                logger.debug(f"[SHORT-ENTRY] Logged to telemetry_db with source={source}")
            except Exception as telem_err:
                logger.error(f"[SHORT-ENTRY] Failed to log to telemetry: {telem_err}")
        
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
        error_msg = f"Short entry failed: {str(e)}"
        logger.error(f"[SHORT-ENTRY] ❌ {symbol} - {error_msg}")
        return ExecutionResult(success=False, error=error_msg)


def execute_market_short_exit(
    symbol: str,
    quantity: Optional[float] = None,
    full_position: bool = True,
    source: str = "autopilot",
    reason: Optional[str] = None
) -> ExecutionResult:
    """
    Execute margin market BUY order to close a short position.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        quantity: Specific quantity to cover (None = auto-detect from position)
        full_position: If True and quantity is None, close entire short
        source: Order source ("autopilot", "command", etc.)
        reason: Exit reason for telemetry (optional)
    
    Returns:
        ExecutionResult with order details and success status
    """
    logger.info(f"[SHORT-EXIT] {symbol} - Attempting margin BUY to cover short (source={source}, full_position={full_position})")
    
    try:
        exchange = get_exchange()
        mode_str = get_mode_str()
        
        # Determine quantity to buy
        if quantity is None:
            if full_position:
                # For shorts, we need to check open positions/orders from Kraken
                # For now, use position tracker
                try:
                    from position_tracker import get_position
                    position = get_position(symbol)
                    
                    if position and position.quantity > 0:
                        quantity = float(position.quantity)
                        logger.info(f"[SHORT-EXIT] {symbol} - Using tracked short position: {quantity:.6f}")
                    else:
                        error_msg = f"No tracked short position for {symbol}"
                        logger.warning(f"[SHORT-EXIT] {error_msg}")
                        return ExecutionResult(success=False, error=error_msg)
                        
                except Exception as tracker_err:
                    error_msg = f"Failed to get short position from tracker: {tracker_err}"
                    logger.error(f"[SHORT-EXIT] {error_msg}")
                    return ExecutionResult(success=False, error=error_msg)
            else:
                error_msg = "Quantity must be specified if full_position=False"
                logger.error(f"[SHORT-EXIT] {error_msg}")
                return ExecutionResult(success=False, error=error_msg)
        
        # Precision formatting
        quantity = float(exchange.amount_to_precision(symbol, quantity))
        
        # Get current price for logging
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker.get('last') or ticker.get('close') or ticker.get('ask', 0)
        
        logger.info(f"[SHORT-EXIT] {symbol} - Placing market BUY to cover: {quantity:.6f} units @ ~${current_price:.4f}")
        
        # Rate limiting check BEFORE order placement
        rate_limit_ok = wait_for_rate_limit(symbol=symbol, max_wait_seconds=5.0)
        if not rate_limit_ok:
            error_msg = f"Rate limit timeout - cannot execute order safely"
            logger.error(f"[SHORT-EXIT] {error_msg}")
            return ExecutionResult(success=False, error=error_msg)
        
        # Execute market buy to cover short
        order = exchange.create_market_buy_order(symbol, quantity)
        
        # Record order execution for rate limiting
        record_order_executed(symbol=symbol)
        
        # Extract fill details with defensive None checks
        order_id = order.get('id', 'UNKNOWN')
        filled_qty = float(order.get('filled') or 0)
        
        avg_price = order.get('average')
        order_price = order.get('price')
        fill_price = float(avg_price if avg_price is not None else (order_price if order_price is not None else current_price))
        
        total_cost = float(order.get('cost') or 0)
        
        # Extract fee
        fee_dict = order.get('fee') or {}
        fee = float(fee_dict.get('cost') or 0)
        fee_currency = fee_dict.get('currency', 'USD')
        
        logger.info(
            f"[SHORT-EXIT] ✅ {symbol} COVER FILLED: {order_id}, "
            f"qty={filled_qty:.6f} @ ${fill_price:.4f}, "
            f"cost=${total_cost:.2f}, fee=${fee:.4f} {fee_currency}"
        )
        
        # Log to executed_orders table
        if EVAL_LOG_ENABLED:
            try:
                register_executed_order(
                    order_id=order_id,
                    symbol=symbol,
                    side='buy_cover',
                    order_type='market',
                    quantity=filled_qty,
                    price=fill_price,
                    status='filled',
                    trading_mode=mode_str,
                    source=source,
                    timestamp_utc=datetime.now(timezone.utc).isoformat()
                )
                logger.debug(f"[SHORT-EXIT] Logged to executed_orders: {order_id}")
            except Exception as log_err:
                logger.error(f"[SHORT-EXIT] Failed to log to executed_orders: {log_err}")
        
        # Log to telemetry (exit - PnL will be calculated by telemetry system)
        if TELEMETRY_ENABLED:
            try:
                log_trade(
                    symbol=symbol,
                    side='buy',
                    action='close',
                    quantity=filled_qty,
                    price=fill_price,
                    usd_amount=filled_qty * fill_price if filled_qty and fill_price else None,
                    order_id=order.get('id') if order else None,
                    reason=reason or 'short_exit',
                    source=source,
                    mode=mode_str,
                    trade_id=order.get('id') if order else None,
                    exit_price=fill_price,
                    position_size=filled_qty
                )
                logger.debug(f"[SHORT-EXIT] Logged to telemetry_db with source={source}")
            except Exception as telem_err:
                logger.error(f"[SHORT-EXIT] Failed to log to telemetry: {telem_err}")
        
        # Remove position from mental SL/TP tracker
        try:
            from position_tracker import remove_position
            removed = remove_position(symbol)
            if removed:
                logger.info(f"[SHORT-EXIT] Short position removed from tracker: {symbol}")
            else:
                logger.debug(f"[SHORT-EXIT] No tracked short position found for: {symbol}")
        except Exception as tracker_err:
            logger.warning(f"[SHORT-EXIT] Failed to remove short position from tracker: {tracker_err}")
        
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
        error_msg = f"Short exit failed: {str(e)}"
        logger.error(f"[SHORT-EXIT] ❌ {symbol} - {error_msg}")
        return ExecutionResult(success=False, error=error_msg)


def get_position_quantity(symbol: str) -> float:
    """
    Get current position quantity for a symbol from position tracker.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
    
    Returns:
        Position quantity (0 if no position)
    """
    try:
        from position_tracker import get_position
        position = get_position(symbol)
        
        if position:
            return float(position.quantity)
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
