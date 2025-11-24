#!/usr/bin/env python3
"""One-time script to sync tracked positions with real Kraken balances"""

import json
from pathlib import Path
from exchange_manager import get_exchange

def main():
    # Get exchange (configured for live mode)
    exchange = get_exchange()
    
    # Fetch current balances
    balance = exchange.fetch_balance()
    
    # Filter only positions with significant holdings (> 0.001)
    MINIMUM_BALANCE = 0.001
    real_positions = {}
    
    for currency, amounts in balance.items():
        if amounts is None:
            continue
        free = amounts.get('free', 0)
        if free > MINIMUM_BALANCE:
            # Try to find corresponding USD pair
            symbol = f"{currency}/USD"
            try:
                # Check if this market exists
                if symbol in exchange.markets:
                    real_positions[symbol] = free
            except:
                pass
    
    print("=== REAL KRAKEN BALANCES ===")
    for symbol, qty in sorted(real_positions.items(), key=lambda x: x[0]):
        print(f"{symbol}: {qty:.8f}")
    
    # Load open_positions.json
    positions_file = Path("open_positions.json")
    if not positions_file.exists():
        print("\n⚠️  open_positions.json not found")
        return
    
    with open(positions_file, 'r') as f:
        tracked_positions = json.load(f)
    
    print("\n=== SYNCING QUANTITIES ===")
    updates = 0
    for symbol, pos_data in tracked_positions.items():
        if symbol in real_positions:
            old_qty = pos_data.get('quantity', 0)
            new_qty = real_positions[symbol]
            if abs(old_qty - new_qty) > 0.00000001:  # Use epsilon for float comparison
                print(f"{symbol}: {old_qty:.8f} → {new_qty:.8f} ✅")
                pos_data['quantity'] = new_qty
                updates += 1
            else:
                print(f"{symbol}: {new_qty:.8f} (already correct)")
        else:
            print(f"{symbol}: NOT FOUND on Kraken - position may have been manually closed")
    
    # Write back updated positions
    with open(positions_file, 'w') as f:
        json.dump(tracked_positions, f, indent=2)
    
    print(f"\n✅ Updated {updates} positions in open_positions.json")

if __name__ == "__main__":
    main()
