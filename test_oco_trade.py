#!/usr/bin/env python3
"""
One-time script to test OCO bracket order execution.
This validates the complete pipeline: market entry + exchange-level TP/SL.
"""

from commands_addon import _force_trade_test

# Run test: ~$3 trade on ASTER/USD with OCO brackets
print("=" * 60)
print("TESTING OCO BRACKET ORDER SYSTEM")
print("=" * 60)

result_lines = _force_trade_test('ASTER', 3)

for line in result_lines:
    print(line)

print("\n" + "=" * 60)
print("TEST COMPLETE - Check Kraken for TP/SL orders")
print("=" * 60)
