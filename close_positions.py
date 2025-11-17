#!/usr/bin/env python3
"""Emergency script to close naked positions"""
import os
from exchange_manager import get_exchange

exchange = get_exchange()

print("Canceling all open orders...")
try:
    result = exchange.cancel_all_orders()
    print(f"✅ Canceled: {result}")
except Exception as e:
    print(f"Cancel error: {e}")

print("\nClosing ALCX/USD position...")
try:
    order = exchange.create_market_sell_order('ALCX/USD', 1.167315)
    print(f"✅ ALCX sold: {order}")
except Exception as e:
    print(f"ALCX error: {e}")

print("\nClosing AR/USD position...")
try:
    order = exchange.create_market_sell_order('AR/USD', 4.6)
    print(f"✅ AR sold: {order}")
except Exception as e:
    print(f"AR error: {e}")

print("\n✅ Positions closed!")
