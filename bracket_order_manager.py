"""
Bracket Order Manager - Mandatory Stop-Loss + Take-Profit System
Implements NON-NEGOTIABLE requirement: NO NAKED POSITIONS - EVER

Every entry order MUST have protective brackets (SL + TP).
If brackets cannot be placed, the entry order is BLOCKED.
"""
from __future__ import annotations
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
import os
import math


def env_float(key: str, default: float) -> float:
    """Get environment variable as float with default."""
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def env_bool(key: str, default: bool) -> bool:
    """Get environment variable as bool with default."""
    val = os.getenv(key, str(default)).lower()
    return val in ("true", "1", "yes", "on")


@dataclass
class BracketConfig:
    """Configuration for bracket orders."""
    risk_per_trade_pct: float = 0.25      # % of equity max loss per position
    min_rr: float = 1.5                    # minimum reward:risk ratio
    atr_mult_stop: float = 2.0             # ATR multiplier for stop
    atr_mult_tp: float = 3.0               # ATR multiplier for TP
    max_slippage_bps: int = 10             # max slippage in basis points
    require_oco: bool = True               # require OCO or emulation
    cancel_on_child_fail: bool = True      # cancel parent if child fails
    fallback_stop_pct: float = 0.02        # fallback stop % if no ATR (2%)
    fallback_tp_pct: float = 0.03          # fallback TP % if no ATR (3%)
    
    @classmethod
    def from_env(cls) -> 'BracketConfig':
        """Load configuration from environment variables."""
        return cls(
            risk_per_trade_pct=env_float("RISK_PER_TRADE", 0.25),
            min_rr=env_float("MIN_RR", 1.5),
            atr_mult_stop=env_float("ATR_MULT_STOP", 2.0),
            atr_mult_tp=env_float("ATR_MULT_TP", 3.0),
            max_slippage_bps=int(env_float("MAX_SLIPPAGE_BPS", 10)),
            require_oco=env_bool("REQUIRE_OCO", True),
            cancel_on_child_fail=env_bool("CANCEL_ON_CHILD_FAIL", True),
            fallback_stop_pct=env_float("FALLBACK_STOP_PCT", 0.02),
            fallback_tp_pct=env_float("FALLBACK_TP_PCT", 0.03)
        )


@dataclass
class BracketOrder:
    """Validated bracket order with entry, stop-loss, and take-profit."""
    symbol: str
    side: str                    # "buy" or "sell"
    entry_price: float
    quantity: float
    stop_price: float
    take_profit_price: float
    risk_usd: float
    reward_usd: float
    rr_ratio: float
    stop_distance_pct: float
    tp_distance_pct: float
    
    def validate(self, config: BracketConfig) -> Tuple[bool, str]:
        """
        Validate bracket order meets all safety requirements.
        
        Returns:
            (is_valid, error_message)
        """
        # Check quantity is positive
        if self.quantity <= 0:
            return False, f"Invalid quantity: {self.quantity}"
        
        # Check prices are valid
        if any(p <= 0 for p in [self.entry_price, self.stop_price, self.take_profit_price]):
            return False, "All prices must be positive"
        
        # Validate stop/TP direction for LONG positions
        if self.side.lower() == "buy":
            if self.stop_price >= self.entry_price:
                return False, f"LONG: stop_price ({self.stop_price}) must be BELOW entry ({self.entry_price})"
            if self.take_profit_price <= self.entry_price:
                return False, f"LONG: take_profit ({self.take_profit_price}) must be ABOVE entry ({self.entry_price})"
        
        # Validate stop/TP direction for SHORT positions
        elif self.side.lower() == "sell":
            if self.stop_price <= self.entry_price:
                return False, f"SHORT: stop_price ({self.stop_price}) must be ABOVE entry ({self.entry_price})"
            if self.take_profit_price >= self.entry_price:
                return False, f"SHORT: take_profit ({self.take_profit_price}) must be BELOW entry ({self.entry_price})"
        
        # Check minimum R:R ratio (use <= to allow exact match)
        if self.rr_ratio < config.min_rr - 0.01:  # Allow 0.01 tolerance for floating point
            return False, f"R:R {self.rr_ratio:.2f} below minimum {config.min_rr:.2f}"
        
        # All validations passed
        return True, "OK"
    
    def recalculate_metrics(self):
        """Recalculate risk/reward metrics after quantity changes."""
        self.risk_usd = abs(self.entry_price - self.stop_price) * self.quantity if self.quantity > 0 else 0
        self.reward_usd = abs(self.take_profit_price - self.entry_price) * self.quantity if self.quantity > 0 else 0
        self.rr_ratio = self.reward_usd / self.risk_usd if self.risk_usd > 0 else 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/API."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "stop_price": self.stop_price,
            "take_profit_price": self.take_profit_price,
            "risk_usd": self.risk_usd,
            "reward_usd": self.reward_usd,
            "rr_ratio": self.rr_ratio,
            "stop_distance_pct": self.stop_distance_pct,
            "tp_distance_pct": self.tp_distance_pct
        }


class BracketOrderManager:
    """Manages bracket order creation and validation."""
    
    def __init__(self, config: Optional[BracketConfig] = None):
        """Initialize bracket order manager."""
        self.config = config or BracketConfig.from_env()
    
    def calculate_bracket_prices(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        atr: Optional[float] = None,
        equity: Optional[float] = None
    ) -> Optional[BracketOrder]:
        """
        Calculate bracket order prices (stop-loss + take-profit).
        
        CRITICAL: ALWAYS returns bracket prices, even if ATR is missing.
        Uses fallback percentage-based stops if ATR unavailable.
        
        Args:
            symbol: Trading symbol
            side: "buy" or "sell"
            entry_price: Entry price
            atr: Average True Range (optional - uses fallback if missing)
            equity: Account equity for position sizing (optional)
            
        Returns:
            BracketOrder with validated prices or None if invalid
        """
        # CRITICAL: Never skip brackets - use fallback if ATR missing
        if atr and atr > 0:
            # Use ATR-based stops
            stop_distance = self.config.atr_mult_stop * atr
            tp_distance = self.config.atr_mult_tp * atr
            print(f"[BRACKET-CALC] Using ATR-based: ATR={atr:.4f}, stop={stop_distance:.4f}, tp={tp_distance:.4f}")
        else:
            # FALLBACK: Use percentage-based stops (NEVER skip brackets)
            stop_distance = entry_price * self.config.fallback_stop_pct
            tp_distance = entry_price * self.config.fallback_tp_pct
            print(f"[BRACKET-CALC] ⚠️  No ATR - using fallback %: stop={self.config.fallback_stop_pct*100:.1f}%, tp={self.config.fallback_tp_pct*100:.1f}%")
        
        # Calculate stop and TP prices based on side
        if side.lower() == "buy":
            # LONG position
            stop_price = entry_price - stop_distance
            tp_price = entry_price + tp_distance
        elif side.lower() == "sell":
            # SHORT position
            stop_price = entry_price + stop_distance
            tp_price = entry_price - tp_distance
        else:
            print(f"[BRACKET-ERR] Invalid side: {side}")
            return None
        
        # Ensure prices are positive BEFORE precision correction
        if stop_price <= 0 or tp_price <= 0:
            print(f"[BRACKET-ERR] Negative price: stop={stop_price}, tp={tp_price}")
            return None
        
        # CRITICAL: Use symbol-specific precision (NOT hard-coded decimals)
        from exchange_manager import get_exchange
        exchange = get_exchange()
        
        stop_price_rounded = float(exchange.price_to_precision(symbol, stop_price))
        tp_price_rounded = float(exchange.price_to_precision(symbol, tp_price))
        
        # CRITICAL: Validate prices again AFTER precision correction to prevent zero/negative values
        if stop_price_rounded <= 0 or tp_price_rounded <= 0:
            print(f"[BRACKET-ERR] Precision correction produced invalid price: stop={stop_price_rounded}, tp={tp_price_rounded}")
            return None
        
        # Calculate position size if equity provided
        if equity and equity > 0:
            # Position sizing based on risk per trade
            risk_amount_usd = equity * (self.config.risk_per_trade_pct / 100.0)
            quantity = risk_amount_usd / abs(entry_price - stop_price_rounded)
        else:
            # Use default quantity (caller must provide)
            quantity = 0.0
        
        # Calculate risk/reward metrics using ROUNDED prices
        risk_usd = abs(entry_price - stop_price_rounded) * quantity if quantity > 0 else 0
        reward_usd = abs(tp_price_rounded - entry_price) * quantity if quantity > 0 else 0
        rr_ratio = reward_usd / risk_usd if risk_usd > 0 else 0
        
        # Calculate percentage distances using ROUNDED prices
        stop_distance_pct = abs((entry_price - stop_price_rounded) / entry_price) * 100
        tp_distance_pct = abs((tp_price_rounded - entry_price) / entry_price) * 100
        
        bracket = BracketOrder(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            stop_price=stop_price_rounded,
            take_profit_price=tp_price_rounded,
            risk_usd=risk_usd,
            reward_usd=reward_usd,
            rr_ratio=rr_ratio,
            stop_distance_pct=stop_distance_pct,
            tp_distance_pct=tp_distance_pct
        )
        
        return bracket
    
    def calculate_minimum_balance_for_symbol(
        self,
        symbol: str,
        exchange,
        entry_price: Optional[float] = None
    ) -> Tuple[float, str]:
        """
        Calculate the minimum balance required to place a bracket order for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., "BTC/USD")
            exchange: CCXT exchange instance
            entry_price: Optional entry price to use instead of ticker price
        
        Returns:
            (min_balance_usd, description)
        """
        try:
            market = exchange.market(symbol) or {}
            limits = market.get("limits") or {}
            min_amt = float((limits.get("amount") or {}).get("min", 0) or 0)
            min_cost = float((limits.get("cost") or {}).get("min", 0) or 0)
            
            if min_amt <= 0 and min_cost <= 0:
                return 0, f"No minimum found for {symbol}"
            
            # Get current price with fallback
            current_price = entry_price if entry_price and entry_price > 0 else None
            if not current_price:
                ticker = exchange.fetch_ticker(symbol)
                current_price = ticker.get("last") or ticker.get("close") or ticker.get("bid") or 0
            
            if current_price <= 0:
                return 0, f"Cannot fetch price for {symbol}"
            
            # Calculate minimum balance based on whichever is larger
            min_balance_from_amt = min_amt * current_price if min_amt > 0 else 0
            min_balance_from_cost = min_cost if min_cost > 0 else 0
            
            # Determine which constraint is binding
            if min_balance_from_cost > min_balance_from_amt:
                # min_cost is binding - calculate actual required amount
                required_amount = min_cost / current_price
                min_balance = min_balance_from_cost
                binding_constraint = f"min cost ${min_cost:.2f} requires {required_amount:.6f} {symbol.split('/')[0]}"
            else:
                # min_amount is binding
                required_amount = min_amt
                min_balance = min_balance_from_amt
                binding_constraint = f"min amount {min_amt:.6f} {symbol.split('/')[0]}"
            
            # Add 10% buffer for safety
            min_balance_with_buffer = min_balance * 1.10
            
            desc = f"{symbol} minimum: {binding_constraint} = ${min_balance_with_buffer:.2f} (current price: ${current_price:.2f})"
            return min_balance_with_buffer, desc
            
        except Exception as e:
            return 0, f"Error calculating minimum for {symbol}: {e}"
    
    def validate_bracket_can_be_placed(
        self,
        bracket: BracketOrder,
        exchange,
        allow_adjust: bool = True
    ) -> Tuple[bool, str, Optional[float]]:
        """
        Validate that bracket orders can be placed on the exchange.
        
        CRITICAL SAFETY CHECK: If brackets cannot be placed, DO NOT TRADE.
        
        Args:
            bracket: BracketOrder to validate
            exchange: CCXT exchange instance
            allow_adjust: Allow automatic quantity adjustment to meet minimums
            
        Returns:
            (can_place, reason, adjusted_qty)
        """
        # Validate bracket logic first
        is_valid, error = bracket.validate(self.config)
        if not is_valid:
            return False, f"Bracket validation failed: {error}", None
        
        # Check exchange minimum volume requirements
        try:
            market = exchange.market(bracket.symbol) or {}
            limits = market.get("limits") or {}
            min_amt = float((limits.get("amount") or {}).get("min", 0) or 0)
            min_cost = float((limits.get("cost") or {}).get("min", 0) or 0)
            
            qty = bracket.quantity
            cost = qty * bracket.entry_price
            
            # Check minimum amount
            if min_amt > 0 and qty < min_amt:
                if allow_adjust:
                    adjusted_qty = min_amt * 1.05  # 5% buffer
                    adjusted_cost = adjusted_qty * bracket.entry_price
                    
                    # CRITICAL: Calculate minimum balance needed and include in error
                    min_balance, desc = self.calculate_minimum_balance_for_symbol(bracket.symbol, exchange, bracket.entry_price)
                    return True, f"Adjusted qty from {qty:.6f} to {adjusted_qty:.6f} (cost: ${adjusted_cost:.2f}). {desc}", adjusted_qty
                else:
                    # CRITICAL: Include minimum balance requirement in error
                    min_balance, desc = self.calculate_minimum_balance_for_symbol(bracket.symbol, exchange, bracket.entry_price)
                    return False, f"INSUFFICIENT_FUNDS: Qty {qty:.6f} below minimum {min_amt:.6f}. Required: {desc}", None
            
            # Check minimum cost
            if min_cost > 0 and cost < min_cost:
                if allow_adjust:
                    adjusted_qty = (min_cost * 1.05) / bracket.entry_price
                    adjusted_cost = adjusted_qty * bracket.entry_price
                    
                    # CRITICAL: Calculate minimum balance needed
                    min_balance, desc = self.calculate_minimum_balance_for_symbol(bracket.symbol, exchange, bracket.entry_price)
                    return True, f"Adjusted qty from {qty:.6f} to {adjusted_qty:.6f} for min cost (${adjusted_cost:.2f}). {desc}", adjusted_qty
                else:
                    # CRITICAL: Include minimum balance requirement in error
                    min_balance, desc = self.calculate_minimum_balance_for_symbol(bracket.symbol, exchange, bracket.entry_price)
                    return False, f"INSUFFICIENT_FUNDS: Cost ${cost:.2f} below minimum ${min_cost:.2f}. Required: {desc}", None
            
            return True, "OK", None
            
        except Exception as e:
            return False, f"Exchange validation error: {e}", None
    
    def place_entry_with_brackets(
        self,
        bracket: BracketOrder,
        exchange
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Place entry order with sequential bracket protection.
        
        IMPORTANT: Kraken REST API does NOT support stop-loss-profit ordertype.
        Sequential approach:
        1. Place LIMIT entry + SL protection (atomic)
        2. Monitor entry fill (2 second wait)
        3. If filled, place TP limit order separately
        
        Args:
            bracket: Validated BracketOrder with calculated prices and quantity
            exchange: CCXT exchange instance (used for symbol precision only)
            
        Returns:
            (success, message, order_dict) - order_dict contains entry order result
        """
        # Final validation
        is_valid, error = bracket.validate(self.config)
        if not is_valid:
            return False, f"Pre-flight validation failed: {error}", None
        
        # Precision adjustment for quantity
        try:
            qty_p = float(exchange.amount_to_precision(bracket.symbol, bracket.quantity))
            if qty_p <= 0:
                return False, "Precision rounding produced zero quantity", None
        except Exception as e:
            return False, f"Precision error: {e}", None
        
        # SEQUENTIAL BRACKET SOLUTION: Entry+SL first, then TP after fill
        # Required because Kraken REST API doesn't support stop-loss-profit ordertype
        try:
            from kraken_native_api import get_kraken_native_api
            
            native_api = get_kraken_native_api()
            
            # Calculate aggressive limit entry price for quick fill
            # Buy: slightly above current price | Sell: slightly below current price
            ticker = exchange.fetch_ticker(bracket.symbol)
            current_price = float(ticker['last'])
            
            if bracket.side.lower() == 'buy':
                raw_entry_price = current_price * 1.001  # 0.1% above market (aggressive fill)
            else:
                raw_entry_price = current_price * 0.999  # 0.1% below market
            
            # Apply exchange price precision (critical for Kraken)
            entry_limit_price = float(exchange.price_to_precision(bracket.symbol, raw_entry_price))
            stop_price_p = float(exchange.price_to_precision(bracket.symbol, bracket.stop_price))
            tp_price_p = float(exchange.price_to_precision(bracket.symbol, bracket.take_profit_price))
            
            print(f"[BRACKET-SEQ] Using sequential bracket approach (Kraken REST limitation)")
            print(f"[BRACKET-SEQ] Step 1: LIMIT entry + SL protection")
            print(f"[BRACKET-SEQ]   Entry: {bracket.side} {qty_p:.6f} {bracket.symbol} @ ${entry_limit_price:.5f}")
            print(f"[BRACKET-SEQ]   Stop-Loss: ${stop_price_p:.5f}")
            print(f"[BRACKET-SEQ] Step 2: TP limit order after fill")
            print(f"[BRACKET-SEQ]   Take-Profit: ${tp_price_p:.5f}")
            
            # Step 1: Place LIMIT entry with SL protection (atomic)
            success, message, result = native_api.place_entry_with_stop_loss(
                symbol=bracket.symbol,
                side=bracket.side,
                quantity=qty_p,
                entry_price=entry_limit_price,
                stop_loss_price=stop_price_p,
                validate=False
            )
            
            if not success:
                print(f"[BRACKET-FAILED] ❌ Entry+SL placement failed: {message}")
                return False, message, result
            
            # Extract entry order ID
            entry_order_id = result.get('txid', ['unknown'])[0] if result and 'txid' in result else 'unknown'
            print(f"[BRACKET-SEQ] ✅ Entry+SL placed: {entry_order_id}")
            
            # CRITICAL: Register entry for monitoring (ensures TP placement if fills later)
            from evaluation_log import register_pending_entry
            from exchange_manager import get_mode_str
            
            register_pending_entry(
                symbol=bracket.symbol,
                entry_order_id=entry_order_id,
                entry_side=bracket.side,
                entry_quantity=qty_p,
                entry_price=entry_limit_price,
                tp_price=tp_price_p,
                sl_price=stop_price_p,
                trading_mode=get_mode_str().lower()
            )
            
            # Step 2: Check if entry filled (fill_data already queried in place_entry_with_stop_loss)
            fill_data = result.get('fill_data') if result else None
            
            if fill_data and fill_data.get('status') == 'closed' and fill_data.get('filled', 0) > 0:
                filled_qty = fill_data['filled']
                fill_price = fill_data.get('average', entry_limit_price)
                
                print(f"[BRACKET-SEQ] ✅ Entry FILLED: {filled_qty:.8f} @ ${fill_price:.4f}")
                print(f"[BRACKET-SEQ] Step 2: Placing TP limit order...")
                
                # Determine TP order side (opposite of entry)
                tp_side = 'sell' if bracket.side.lower() == 'buy' else 'buy'
                
                # Step 3: Place TP with intelligent settlement detection + retry
                from settlement_detector import place_tp_with_retry
                
                tp_success, tp_message, tp_order_id = place_tp_with_retry(
                    symbol=bracket.symbol,
                    side=tp_side,
                    quantity=filled_qty,
                    tp_price=tp_price_p,
                    fill_price=fill_price,
                    max_attempts=5,
                    initial_backoff=1.0
                )
                
                if tp_success:
                    print(f"[BRACKET-COMPLETE] 🎯 SEQUENTIAL BRACKETS COMPLETE!")
                    print(f"[BRACKET-COMPLETE] Entry filled + SL active + TP placed")
                    print(f"[BRACKET-COMPLETE] Entry ID: {entry_order_id}")
                    print(f"[BRACKET-COMPLETE] TP ID: {tp_order_id}")
                    
                    # Step 4: Find and store SL order ID for OCO monitoring
                    from sl_order_enrichment import enrich_and_store_sl_order_id
                    sl_order_id = enrich_and_store_sl_order_id(entry_order_id, bracket.symbol, max_attempts=3)
                    
                    if sl_order_id:
                        print(f"[BRACKET-COMPLETE] SL ID: {sl_order_id} (for OCO monitoring)")
                    else:
                        print(f"[BRACKET-WARNING] ⚠️ Could not find SL order ID yet (will retry in reconciliation)")
                    
                    # Mark entry as filled and store TP/SL IDs (for OCO monitoring)
                    from evaluation_log import mark_pending_order_filled
                    mark_pending_order_filled(entry_order_id, tp_order_id=tp_order_id, sl_order_id=sl_order_id)
                    
                    # Include TP order ID in result
                    result['tp_order_id'] = tp_order_id
                    result['sl_order_id'] = sl_order_id
                    return True, f"Brackets complete: Entry filled, SL active, TP placed", result
                else:
                    print(f"[BRACKET-WARNING] ⚠️ TP placement failed: {tp_message}")
                    print(f"[BRACKET-WARNING] Entry is FILLED with SL protection, but NO TP")
                    print(f"[BRACKET-WARNING] Reconciliation will retry TP placement...")
                    return True, f"Entry filled with SL, but TP failed: {tp_message}", result
            else:
                # Entry not filled yet (limit order pending)
                status = fill_data.get('status', 'unknown') if fill_data else 'unknown'
                print(f"[BRACKET-PENDING] Entry order placed but not filled yet (status: {status})")
                print(f"[BRACKET-PENDING] SL protection is active. TP will need manual placement after fill.")
                return True, f"Entry+SL placed (status: {status}), pending fill for TP", result
            
        except Exception as e:
            error_msg = str(e)
            print(f"[BRACKET-ERROR] Failed to place sequential bracket orders: {error_msg}")
            import traceback
            traceback.print_exc()
            return False, f"Kraken native bracket API failed: {error_msg}", None
    
    def place_bracket_orders(
        self,
        bracket: BracketOrder,
        exchange,
        run_command_func
    ) -> Tuple[bool, str]:
        """
        DEPRECATED: Use place_entry_with_brackets() instead.
        
        This method is kept for backward compatibility but should not be used
        for new code. It was designed for the old two-step process (entry first,
        then brackets), which doesn't work with Kraken's conditional close API.
        """
        success, message, _ = self.place_entry_with_brackets(bracket, exchange)
        return success, message


# Global instance
_bracket_manager: Optional[BracketOrderManager] = None


def get_bracket_manager() -> BracketOrderManager:
    """Get global bracket order manager instance."""
    global _bracket_manager
    if _bracket_manager is None:
        _bracket_manager = BracketOrderManager()
    return _bracket_manager
