#!/usr/bin/env python3
"""Emergency liquidation script - cancels all orders and sells all crypto balances"""

import sys
from exchange_manager import get_exchange
from account_state import get_balances
from loguru import logger

def emergency_flatten():
    """Cancel all open orders and market sell all crypto balances"""
    ex = get_exchange()
    
    print("=" * 60)
    print("EMERGENCY LIQUIDATION - CANCELING ALL ORDERS & SELLING ALL CRYPTO")
    print("=" * 60)
    
    # Step 1: Cancel all open orders
    print("\n[STEP 1] Canceling all open orders...")
    try:
        open_orders = ex.fetch_open_orders()
        if open_orders:
            print(f"Found {len(open_orders)} open order(s)")
            for order in open_orders:
                order_id = order.get('id')
                symbol = order.get('symbol')
                try:
                    ex.cancel_order(order_id, symbol)
                    print(f"  ✅ Canceled order {order_id} ({symbol})")
                except Exception as e:
                    print(f"  ❌ Failed to cancel {order_id}: {e}")
        else:
            print("  No open orders to cancel")
    except Exception as e:
        print(f"  ❌ Error fetching/canceling orders: {e}")
    
    # Step 2: Get all balances and sell crypto
    print("\n[STEP 2] Selling all crypto balances...")
    try:
        balances = get_balances()
        if not balances:
            print("  No balances found")
            return
        
        # Filter out USD and dust amounts
        crypto_balances = {}
        for currency, bal in balances.items():
            if currency == 'USD':
                continue
            total = bal.get('total', 0.0)
            if total > 0.001:  # Ignore dust
                crypto_balances[currency] = total
        
        if not crypto_balances:
            print("  No crypto balances to sell (only USD or dust)")
            return
        
        print(f"Found {len(crypto_balances)} crypto balance(s) to sell:")
        for currency, amount in crypto_balances.items():
            print(f"  {currency}: {amount:.8f}")
        
        # Market sell each crypto balance
        for currency, amount in crypto_balances.items():
            symbol = f"{currency}/USD"
            try:
                print(f"\n  Selling {amount:.8f} {currency} ({symbol})...")
                order = ex.create_market_sell_order(symbol, amount)
                order_id = order.get('id', 'unknown')
                status = order.get('status', 'unknown')
                print(f"    ✅ SOLD {amount:.8f} {currency} - Order {order_id} ({status})")
            except Exception as e:
                print(f"    ❌ Failed to sell {currency}: {e}")
    
    except Exception as e:
        print(f"  ❌ Error selling balances: {e}")
    
    print("\n" + "=" * 60)
    print("EMERGENCY LIQUIDATION COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    try:
        emergency_flatten()
    except Exception as e:
        logger.error(f"Emergency flatten failed: {e}")
        sys.exit(1)
