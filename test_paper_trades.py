#!/usr/bin/env python3
"""
Test script to verify paper trades show up in open orders.
Tests the complete flow: execute bracket order → query open orders → verify results.
"""

import os
import sys

# Set paper mode before importing
os.environ["KRAKEN_VALIDATE_ONLY"] = "1"

from exchange_manager import get_exchange, get_mode_str, is_paper_mode
from commands import handle

def test_paper_bracket_order():
    """
    Test that a paper bracket order creates:
    1. Entry order (market, executes immediately)
    2. TP order (limit, stays open)
    3. SL order (stop, stays open)
    
    Then verify that open orders shows the TP and SL orders.
    """
    print("=" * 70)
    print("PAPER TRADING TEST - Bracket Order Flow")
    print("=" * 70)
    
    # Verify mode
    mode = get_mode_str()
    print(f"\n✓ Trading mode: {mode.upper()}")
    assert is_paper_mode(), "Test must run in PAPER mode"
    
    # Get exchange instance
    ex = get_exchange()
    ex_type = type(ex).__name__
    print(f"✓ Exchange type: {ex_type}")
    assert ex_type == "PaperExchangeWrapper", f"Expected PaperExchangeWrapper, got {ex_type}"
    
    # Test 1: Execute a bracket order
    print("\n" + "-" * 70)
    print("TEST 1: Execute bracket order")
    print("-" * 70)
    
    # Get current price for ZEC/USD
    ticker = ex.fetch_ticker('ZEC/USD')
    current_price = ticker['last']
    print(f"Current ZEC/USD price: ${current_price:.2f}")
    
    # Calculate bracket prices (long position)
    tp_price = current_price * 1.02  # 2% above
    sl_price = current_price * 0.99  # 1% below
    
    print(f"Creating LONG bracket order:")
    print(f"  Amount: 0.04 ZEC")
    print(f"  Entry: Market buy @ ${current_price:.2f}")
    print(f"  TP: ${tp_price:.2f} (2% profit)")
    print(f"  SL: ${sl_price:.2f} (1% stop)")
    
    # Execute bracket command
    cmd = f"bracket ZEC/USD 0.04 tp {tp_price:.2f} sl {sl_price:.2f}"
    print(f"\nExecuting: {cmd}")
    result = handle(cmd)
    print(f"\nResult:\n{result}")
    
    # Verify success
    assert "BRACKET OK" in result, f"Bracket order failed: {result}"
    print("\n✓ Bracket order executed successfully")
    
    # Test 2: Query open orders using the SAME exchange instance
    print("\n" + "-" * 70)
    print("TEST 2: Query open orders")
    print("-" * 70)
    
    # Method 1: Direct exchange call
    open_orders = ex.fetch_open_orders('ZEC/USD')
    print(f"\nDirect ex.fetch_open_orders(): {len(open_orders)} orders")
    for order in open_orders:
        oid = order.get('id', '?')
        otype = order.get('type', '?')
        side = order.get('side', '?')
        price = order.get('price', 0)
        status = order.get('status', '?')
        print(f"  - {oid}: {side} {otype} @ ${price:.2f} ({status})")
    
    # Method 2: Via commands.handle (same path as LLM)
    cmd_result = handle("open ZEC/USD")
    print(f"\nVia handle('open ZEC/USD'):\n{cmd_result}")
    
    # Verify results
    print("\n" + "-" * 70)
    print("VERIFICATION")
    print("-" * 70)
    
    if len(open_orders) >= 2:
        print(f"✅ SUCCESS: Found {len(open_orders)} open orders (TP + SL)")
        print(f"   Expected: 2 open orders (TP limit sell + SL stop sell)")
        print(f"   Note: Market entry order executed immediately (not in open orders)")
        return True
    else:
        print(f"❌ FAILURE: Expected 2 open orders, found {len(open_orders)}")
        print(f"   This means the bracket command didn't create the orders correctly")
        return False

def main():
    try:
        success = test_paper_bracket_order()
        
        print("\n" + "=" * 70)
        if success:
            print("TEST PASSED ✅")
            print("=" * 70)
            return 0
        else:
            print("TEST FAILED ❌")
            print("=" * 70)
            return 1
    
    except Exception as e:
        print(f"\n❌ TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
