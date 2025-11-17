#!/usr/bin/env python3
"""Test atomic bracket order system with Kraken conditional close API"""

from bracket_order_manager import get_bracket_manager
from exchange_manager import get_exchange

def test_bracket_order():
    """Test placing a small bracket order with TP/SL attached"""
    print("=" * 60)
    print("TESTING ATOMIC BRACKET ORDER SYSTEM")
    print("=" * 60)
    
    # Get manager and exchange
    manager = get_bracket_manager()
    ex = get_exchange()
    
    # Test with AR/USD - small $5 position
    symbol = 'AR/USD'
    entry_price = 4.33  # Current price
    
    print(f"\n1. Calculating bracket prices for {symbol} @ ${entry_price}")
    bracket = manager.calculate_bracket_prices(
        symbol=symbol,
        side='buy',
        entry_price=entry_price,
        atr=0.03  # Simulated ATR (~3% of price) - gives better R:R
    )
    
    if not bracket:
        print("❌ Failed to calculate bracket prices")
        return
    
    print(f"   Entry: ${bracket.entry_price:.4f}")
    print(f"   Stop Loss: ${bracket.stop_price:.4f} ({bracket.stop_distance_pct*100:.2f}% below)")
    print(f"   Take Profit: ${bracket.take_profit_price:.4f} ({bracket.tp_distance_pct*100:.2f}% above)")
    
    # Set small quantity ($5 worth)
    bracket.quantity = 1.15  # ~$5 at $4.33/unit
    bracket.recalculate_metrics()
    
    print(f"\n2. Setting test quantity: {bracket.quantity:.6f} units (~${bracket.quantity * entry_price:.2f})")
    print(f"   Risk: ${bracket.risk_usd:.4f}")
    print(f"   Reward: ${bracket.reward_usd:.4f}")
    print(f"   R:R Ratio: {bracket.rr_ratio:.2f}")
    
    # Validate
    print(f"\n3. Validating bracket can be placed...")
    can_place, reason, adj_qty = manager.validate_bracket_can_be_placed(bracket, ex, allow_adjust=True)
    
    if not can_place:
        print(f"❌ Validation failed: {reason}")
        return
    
    if adj_qty:
        print(f"   ⚠️  Quantity adjusted: {bracket.quantity:.6f} → {adj_qty:.6f}")
        bracket.quantity = adj_qty
        bracket.recalculate_metrics()
    
    print(f"   ✅ Validation passed: {reason}")
    
    # Place atomic bracket order
    print(f"\n4. Placing ATOMIC bracket order (entry + TP/SL in ONE order)...")
    print(f"   Using Kraken 'stop-loss-profit' conditional close API")
    
    success, message, order = manager.place_entry_with_brackets(bracket, ex)
    
    print(f"\n{'='*60}")
    if success:
        print(f"✅ SUCCESS: {message}")
        if order:
            order_id = order.get('id', 'unknown')
            print(f"   Order ID: {order_id}")
            print(f"   Status: {order.get('status', 'unknown')}")
            print(f"   Filled: {order.get('filled', 0)} units")
    else:
        print(f"❌ FAILED: {message}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    try:
        test_bracket_order()
    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
