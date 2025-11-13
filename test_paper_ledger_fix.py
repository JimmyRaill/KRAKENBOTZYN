#!/usr/bin/env python3
"""
Test script to verify paper trading ledger integration.
This test executes a bracket order and verifies it appears in queries.
"""

import sys
import os

# Set paper mode
os.environ['KRAKEN_VALIDATE_ONLY'] = '1'

from commands import handle
from account_state import get_paper_ledger

print("="*60)
print("PAPER TRADING LEDGER INTEGRATION TEST")
print("="*60)

# Step 1: Get initial state
print("\n[STEP 1] Checking initial ledger state...")
ledger = get_paper_ledger()
initial_order_count = len(ledger.orders)
print(f"Initial orders in ledger: {initial_order_count}")

# Step 2: Execute a bracket order
print("\n[STEP 2] Executing bracket order: ZEC/USD 0.03 tp 500 sl 480...")
result = handle("bracket zec/usd 0.03 tp 500 sl 480")
print(f"Result: {result}")

# Step 3: Check ledger updated
print("\n[STEP 3] Checking if ledger was updated...")
ledger = get_paper_ledger()  # Reload
current_order_count = len(ledger.orders)
print(f"Current orders in ledger: {current_order_count}")
orders_added = current_order_count - initial_order_count
print(f"Orders added: {orders_added}")

# Step 4: Query open orders
print("\n[STEP 4] Querying open orders...")
open_result = handle("open")
print(f"Open orders result:\n{open_result}")

# Step 5: Use debug command
print("\n[STEP 5] Dumping ledger with debug command...")
debug_result = handle("debug ledger")
print(f"Debug output:\n{debug_result}")

# Step 6: Verify
print("\n" + "="*60)
print("VERIFICATION")
print("="*60)

success = True

if orders_added < 2:
    print(f"❌ FAIL: Expected at least 2 orders (TP + SL), got {orders_added}")
    success = False
else:
    print(f"✅ PASS: {orders_added} orders added to ledger")

if "(no open orders)" in open_result.lower():
    print("❌ FAIL: Query returned 'no open orders' despite orders being created")
    success = False
else:
    print("✅ PASS: Query returned orders successfully")

if "zec" in open_result.lower():
    print("✅ PASS: ZEC orders visible in query results")
else:
    print("❌ FAIL: ZEC orders not visible in query results")
    success = False

print("\n" + "="*60)
if success:
    print("🎉 ALL TESTS PASSED! Paper ledger integration is working!")
else:
    print("⚠️  SOME TESTS FAILED - see details above")
print("="*60)

sys.exit(0 if success else 1)
