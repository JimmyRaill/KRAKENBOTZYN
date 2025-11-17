#!/usr/bin/env python3
"""Test ATOMIC bracket order via WebSocket v2 batch_add"""

import os
os.environ["TRADING_MODE"] = "LIVE"

from dotenv import load_dotenv
load_dotenv()

from bracket_order_manager import get_bracket_manager
from exchange_manager import get_exchange

def test_atomic_bracket():
    """Test atomic bracket: Entry + TP + SL in ONE WebSocket request"""
    print("=" * 70)
    print("TESTING ATOMIC BRACKET ORDER VIA WEBSOCKET V2")
    print("Strategy: batch_add with reduce_only flags")
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
    
    # EXECUTE ATOMIC BRACKET
    print(f"\n{'='*70}")
    print("EXECUTING ATOMIC BRACKET (LIVE MODE!)...")
    print("This will place Entry + TP + SL in ONE WebSocket request")
    print(f"{'='*70}\n")
    
    success, message, order = manager.place_entry_with_brackets(bracket, ex)
    
    print(f"\n{'='*70}")
    if success:
        print(f"✅ SUCCESS: {message}")
        print(f"\nYou should now see THREE orders in Kraken:")
        print(f"  1. Entry order (filled)")
        print(f"  2. Take-profit limit order (pending)")
        print(f"  3. Stop-loss order (pending)")
    else:
        print(f"❌ FAILED: {message}")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    try:
        test_atomic_bracket()
    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
