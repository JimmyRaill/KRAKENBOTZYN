#!/usr/bin/env python3
"""
Test paper ledger persistence across multiple "requests" (simulating multi-worker scenario).
This test simulates what happens when uvicorn serves requests with multiple workers.
"""
import os
os.environ['KRAKEN_VALIDATE_ONLY'] = '1'

import json
import subprocess
import sys
from pathlib import Path

LEDGER_FILE = Path(__file__).parent / "paper_ledger.json"
STATE_FILE = Path(__file__).parent / "paper_trading_state.json"

def reset_ledger():
    """Reset the paper ledger AND simulator state to clean state"""
    # Reset ledger
    ledger_data = {
        'balances': {
            'USD': {
                'currency': 'USD',
                'free': 10000.0,
                'locked': 0.0,
                'total': 10000.0,
                'last_updated': 0.0
            }
        },
        'trades': [],
        'orders': [],
        'starting_balance_usd': 10000.0,
        'last_saved': 0.0
    }
    with open(LEDGER_FILE, 'w') as f:
        json.dump(ledger_data, f, indent=2)
    print(f"✓ Reset {LEDGER_FILE}")
    
    # Reset simulator state (remove lingering positions)
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        print(f"✓ Removed {STATE_FILE}")
    
    # Remove lock files
    for lock in Path(__file__).parent.glob("*.lock"):
        lock.unlink()
        print(f"✓ Removed {lock.name}")

def run_in_subprocess(code: str) -> tuple:
    """Run Python code in a subprocess (simulates different worker)"""
    result = subprocess.run(
        [sys.executable, '-c', code],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent
    )
    return result.stdout, result.stderr, result.returncode

def test_persistence():
    print("="*80)
    print("MULTI-REQUEST PERSISTENCE TEST")
    print("Simulating separate worker processes (like uvicorn multi-worker)")
    print("="*80)
    
    # Step 1: Reset
    print("\n[STEP 1] Resetting ledger to clean state...")
    reset_ledger()
    
    # Step 2: Execute trade in "Worker A" (subprocess 1)
    print("\n[STEP 2] Worker A: Executing bracket order...")
    worker_a_code = """
import os
os.environ['KRAKEN_VALIDATE_ONLY'] = '1'

from llm_agent import _execute_bracket_with_percentages

result = _execute_bracket_with_percentages('ZEC/USD', 0.03, 1, 2)
print("WORKER_A_RESULT:", "SUCCESS" if "BRACKET OK" in result else "FAILED")
"""
    
    stdout_a, stderr_a, code_a = run_in_subprocess(worker_a_code)
    print(f"Worker A output: {stdout_a.strip()}")
    if code_a != 0:
        print(f"Worker A stderr: {stderr_a}")
        print("❌ Worker A failed!")
        return False
    
    # Step 3: Check ledger file directly
    print("\n[STEP 3] Checking paper_ledger.json on disk...")
    try:
        with open(LEDGER_FILE, 'r') as f:
            ledger_data = json.load(f)
        orders_on_disk = len(ledger_data.get('orders', []))
        print(f"✓ Found {orders_on_disk} orders in paper_ledger.json")
        
        if orders_on_disk > 0:
            print("\nOrders on disk:")
            for i, order in enumerate(ledger_data.get('orders', []), 1):
                print(f"  {i}. {order.get('symbol')} {order.get('side')} {order.get('type')} @ {order.get('price')}")
    except Exception as e:
        print(f"❌ Failed to read ledger file: {e}")
        return False
    
    # Step 4: Query orders in "Worker B" (subprocess 2) - DIFFERENT PROCESS
    print("\n[STEP 4] Worker B: Querying open orders (DIFFERENT PROCESS)...")
    worker_b_code = """
import os
os.environ['KRAKEN_VALIDATE_ONLY'] = '1'

from llm_agent import _execute_trading_command

result = _execute_trading_command("open")
print("WORKER_B_RESULT:", result)
"""
    
    stdout_b, stderr_b, code_b = run_in_subprocess(worker_b_code)
    print(f"Worker B output:\n{stdout_b}")
    if code_b != 0:
        print(f"Worker B stderr: {stderr_b}")
    
    # Step 5: Verify
    print("\n" + "="*80)
    print("VERIFICATION")
    print("="*80)
    
    all_pass = True
    
    # Test 1: Orders saved to disk
    if orders_on_disk >= 1:
        print(f"✅ PASS: {orders_on_disk} order(s) saved to disk by Worker A")
    else:
        print(f"❌ FAIL: No orders saved to disk by Worker A")
        all_pass = False
    
    # Test 2: Worker B can read them
    if "(no open orders)" in stdout_b.lower():
        print("❌ FAIL: Worker B returned 'no open orders' despite orders on disk")
        print("   This means the reload fix is NOT working!")
        all_pass = False
    elif "zec" in stdout_b.lower():
        print("✅ PASS: Worker B successfully read ZEC orders from disk")
    else:
        print(f"⚠️  WARNING: Unexpected Worker B result")
    
    # Test 3: Cross-process persistence
    if orders_on_disk > 0 and "zec" in stdout_b.lower():
        print("✅ PASS: Orders persist across different processes!")
        print("   Multi-worker environment is handled correctly!")
    else:
        print("❌ FAIL: Orders do NOT persist across processes")
        print("   This is the bug Jimmy is experiencing!")
        all_pass = False
    
    print("\n" + "="*80)
    if all_pass:
        print("🎉 ALL TESTS PASSED!")
        print("✓ Orders saved to disk by one worker")
        print("✓ Orders readable by another worker")
        print("✓ Jimmy's chat will now work correctly!")
    else:
        print("⚠️  TESTS FAILED - Multi-process persistence broken")
    print("="*80)
    
    return all_pass

if __name__ == "__main__":
    success = test_persistence()
    sys.exit(0 if success else 1)
