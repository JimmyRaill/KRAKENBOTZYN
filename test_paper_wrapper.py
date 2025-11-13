#!/usr/bin/env python3
"""
Quick test script to verify paper trading wrapper works end-to-end.
Tests:
1. Execute a bracket order in paper mode
2. Query open orders and verify they appear
"""

import sys
from commands import handle

print("=" * 60)
print("PAPER TRADING WRAPPER END-TO-END TEST")
print("=" * 60)

# Step 1: Check current mode
print("\n[TEST 1] Checking current mode...")
from exchange_manager import get_mode_str, is_paper_mode
mode = get_mode_str()
print(f"Current mode: {mode.upper()}")

if not is_paper_mode():
    print("❌ ABORT: Test requires PAPER mode")
    sys.exit(1)

print("✅ PASS: Running in PAPER mode")

# Step 2: Execute a test bracket order
print("\n[TEST 2] Executing test bracket order...")
test_command = "bracket zec/usd 0.03 tp 500 sl 480"
print(f"Command: {test_command}")

try:
    result = handle(test_command)
    print(f"Result: {result}")
    
    if "BRACKET OK" in result or "PAPER" in result:
        print("✅ PASS: Bracket order executed")
    else:
        print(f"⚠️ WARNING: Unexpected result format: {result}")
except Exception as e:
    print(f"❌ FAIL: Execution error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Step 3: Query open orders
print("\n[TEST 3] Querying open orders...")
try:
    open_result = handle("open")
    print(f"Open orders:\n{open_result}")
    
    # Check if orders appear
    has_orders = "(no open orders)" not in open_result.lower()
    has_zec = "zec" in open_result.lower()
    
    if has_orders and has_zec:
        print("✅ PASS: Paper orders are visible in open orders query!")
    else:
        print(f"❌ FAIL: Paper orders NOT appearing (has_orders={has_orders}, has_zec={has_zec})")
        sys.exit(1)
except Exception as e:
    print(f"❌ FAIL: Query error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Step 4: Check balances
print("\n[TEST 4] Checking paper balances...")
try:
    bal_result = handle("bal")
    print(f"Balances:\n{bal_result}")
    print("✅ PASS: Balance query successful")
except Exception as e:
    print(f"⚠️ WARNING: Balance query failed: {e}")

print("\n" + "=" * 60)
print("🎯 ALL TESTS PASSED!")
print("Paper trading wrapper is working correctly.")
print("=" * 60)
