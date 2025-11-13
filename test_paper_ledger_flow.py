#!/usr/bin/env python3
"""
Direct test of paper ledger integration - simulates the exact flow jimmy experiences.
Run this to verify that paper trades persist and appear in queries.
"""
import os
os.environ['KRAKEN_VALIDATE_ONLY'] = '1'

from llm_agent import _execute_bracket_with_percentages, _execute_trading_command
from account_state import get_paper_ledger

def test_paper_ledger_flow():
    print("="*80)
    print("PAPER LEDGER INTEGRATION TEST - Simulating Jimmy's Chat Flow")
    print("="*80)
    
    # Step 1: Reset for clean test
    print("\n[STEP 1] Resetting paper ledger...")
    ledger = get_paper_ledger()
    ledger.reset(10000.0)
    initial_count = len(ledger.orders)
    print(f"✓ Starting with {initial_count} orders in ledger")
    
    # Step 2: Simulate user sending: "Paper buy 0.03 ZEC/USD market. SL 1% below. TP 2% above."
    print("\n[STEP 2] Simulating user trade command...")
    print("User says: 'Paper buy 0.03 ZEC/USD market. SL 1% below. TP 2% above. Execute.'")
    print("\nLLM calls: execute_bracket_with_percentages('ZEC/USD', 0.03, 1, 2)")
    
    result = _execute_bracket_with_percentages('ZEC/USD', 0.03, 1, 2)
    
    print(f"\nLLM responds to user with:")
    print(f"{result}")
    
    # Step 3: Check what's in the ledger
    print("\n[STEP 3] Checking ledger state after execution...")
    ledger = get_paper_ledger()
    current_count = len(ledger.orders)
    orders_added = current_count - initial_count
    
    print(f"✓ Ledger now has {current_count} orders ({orders_added} added)")
    
    if current_count > 0:
        print("\nOrders in ledger:")
        for i, order in enumerate(ledger.orders, 1):
            print(f"  {i}. {order.get('symbol')} {order.get('side')} {order.get('type')} "
                  f"{order.get('amount')} @ {order.get('price')} "
                  f"(status: {order.get('status')})")
    
    # Step 4: Simulate user asking: "Any open paper orders?" or "Show open orders"
    print("\n[STEP 4] Simulating user query...")
    print("User asks: 'Any open paper orders?'")
    print("\nLLM calls: execute_trading_command('open')")
    
    open_result = _execute_trading_command("open")
    
    print(f"\nLLM responds to user with:")
    print(f"{open_result}")
    
    # Step 5: Verification
    print("\n" + "="*80)
    print("VERIFICATION RESULTS")
    print("="*80)
    
    all_pass = True
    
    # Test 1: Orders were added to ledger
    if orders_added >= 1:
        print(f"✅ PASS: {orders_added} order(s) added to ledger")
    else:
        print(f"❌ FAIL: Expected at least 1 order added, got {orders_added}")
        all_pass = False
    
    # Test 2: Query returns the orders
    if "(no open orders)" in open_result.lower():
        print(f"❌ FAIL: Query returned 'no open orders' despite {current_count} in ledger")
        print(f"   This means the query path is NOT reading from the canonical ledger!")
        all_pass = False
    elif "zec" in open_result.lower():
        print(f"✅ PASS: Query successfully returned ZEC orders from ledger")
    else:
        print(f"⚠️  WARNING: Unexpected query result (might be OK)")
    
    # Test 3: Data consistency
    if current_count > 0 and "zec" in open_result.lower():
        print(f"✅ PASS: Execution and query paths are using the same ledger!")
    elif current_count == 0:
        print(f"❌ FAIL: No orders in ledger - execution path failed")
        all_pass = False
    else:
        print(f"❌ FAIL: Orders exist ({current_count}) but query doesn't show them")
        print(f"   This is the disconnection bug!")
        all_pass = False
    
    print("\n" + "="*80)
    if all_pass:
        print("🎉 ALL TESTS PASSED!")
        print("✓ Paper trades are being stored correctly")
        print("✓ Queries are reading from the correct ledger")
        print("✓ Jimmy should see his trades in the chat!")
    else:
        print("⚠️  TESTS FAILED - There's still a problem")
        print("Debug: Run 'python run.py' and execute 'debug ledger' to inspect")
    print("="*80)
    
    return all_pass

if __name__ == "__main__":
    test_paper_ledger_flow()
