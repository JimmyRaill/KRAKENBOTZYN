#!/usr/bin/env python3
"""Test THREE-ORDER bracket: Entry (no conditional) → TP → SL"""

import os
os.environ["TRADING_MODE"] = "LIVE"

from dotenv import load_dotenv
load_dotenv()

from bracket_order_manager import get_bracket_manager
from exchange_manager import get_exchange

def test_three_order_bracket():
    """Test placing bracket with entry, then separate TP and SL orders"""
    print("=" * 70)
    print("TESTING THREE-ORDER BRACKET SYSTEM")
    print("Strategy: Entry (no conditional) → TP limit → SL stop-loss")
    print("=" * 70)
    
    manager = get_bracket_manager()
    ex = get_exchange()
    
    # Small test with AR/USD
    symbol = 'AR/USD'
    entry_price = 4.33
    
    print(f"\nCalculating bracket for {symbol} @ ${entry_price}")
    bracket = manager.calculate_bracket_prices(
        symbol=symbol,
        side='buy',
        entry_price=entry_price,
        atr=0.03
    )
    
    # Set small quantity (~$5)
    bracket.quantity = 1.15
    bracket.recalculate_metrics()
    
    print(f"\nBracket Details:")
    print(f"  Entry: market buy {bracket.quantity:.6f} @ ${bracket.entry_price:.4f}")
    print(f"  Take-Profit: ${bracket.take_profit_price:.4f}")
    print(f"  Stop-Loss: ${bracket.stop_price:.4f}")
    
    # EXECUTE THE THREE-ORDER BRACKET
    print(f"\n{'='*70}")
    print("EXECUTING THREE-ORDER BRACKET (LIVE MODE!)...")
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
        test_three_order_bracket()
    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
