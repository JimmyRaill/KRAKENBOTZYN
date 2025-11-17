#!/usr/bin/env python3
"""Test TWO-STEP bracket: Entry+SL, then separate TP limit order"""

from dotenv import load_dotenv
load_dotenv()

from bracket_order_manager import get_bracket_manager
from exchange_manager import get_exchange

def test_two_step_bracket():
    """Test placing bracket with entry+SL, then separate TP"""
    print("=" * 70)
    print("TESTING TWO-STEP BRACKET ORDER SYSTEM")
    print("Strategy: Entry with SL attached → Separate TP limit order")
    print("=" * 70)
    
    manager = get_bracket_manager()
    ex = get_exchange()
    
    # Test with AR/USD - small $5 position
    symbol = 'AR/USD'
    entry_price = 4.33
    
    print(f"\nCalculating bracket for {symbol} @ ${entry_price}")
    bracket = manager.calculate_bracket_prices(
        symbol=symbol,
        side='buy',
        entry_price=entry_price,
        atr=0.03  # Small ATR for testing
    )
    
    # Set small quantity (~$5)
    bracket.quantity = 1.15
    bracket.recalculate_metrics()
    
    print(f"\nBracket Details:")
    print(f"  Entry: market {bracket.side} {bracket.quantity:.6f} @ ${bracket.entry_price:.4f}")
    print(f"  Stop-Loss: ${bracket.stop_price:.4f} (attached to entry)")
    print(f"  Take-Profit: ${bracket.take_profit_price:.4f} (separate limit order)")
    print(f"  Position: ${bracket.quantity * bracket.entry_price:.2f}")
    print(f"  R:R: 1:{bracket.reward_usd / bracket.risk_usd:.2f}")
    
    # EXECUTE THE TWO-STEP BRACKET
    print(f"\n{'='*70}")
    print("EXECUTING TWO-STEP BRACKET ORDER (LIVE MODE!)...")
    print(f"{'='*70}\n")
    
    success, message, order = manager.place_entry_with_brackets(bracket, ex)
    
    print(f"\n{'='*70}")
    if success:
        print(f"✅ SUCCESS: {message}")
    else:
        print(f"❌ FAILED: {message}")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    try:
        test_two_step_bracket()
    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
