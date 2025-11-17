#!/usr/bin/env python3
"""
Quick test of Kraken WebSocket v2 atomic bracket orders
"""
import asyncio
import os
from kraken_websocket_v2 import get_kraken_websocket_v2
from exchange_manager import get_exchange

def test_websocket_bracket():
    """Test WebSocket bracket order with small AR/USD position"""
    
    # Get current AR/USD price
    exchange = get_exchange()
    ticker = exchange.fetch_ticker('AR/USD')
    current_price = ticker['last']
    
    print(f"\n[TEST] AR/USD current price: ${current_price}")
    
    # Calculate bracket levels (1% spread for testing)
    quantity = 3.0  # ~$5 position
    entry_price = current_price
    take_profit = round(current_price * 1.01, 2)  # +1%
    stop_loss = round(current_price * 0.99, 2)   # -1%
    
    print(f"[TEST] Position size: {quantity} AR (~${quantity * current_price:.2f})")
    print(f"[TEST] Take Profit: ${take_profit} (+1%)")
    print(f"[TEST] Stop Loss: ${stop_loss} (-1%)")
    print(f"\n[TEST] Sending WebSocket batch_add request...")
    
    # Get WebSocket client
    ws_client = get_kraken_websocket_v2()
    
    # Place atomic bracket order
    success, message, result = asyncio.run(
        ws_client.batch_add_bracket(
            symbol='AR/USD',
            side='buy',
            quantity=quantity,
            take_profit_price=take_profit,
            stop_loss_price=stop_loss,
            validate=False  # LIVE execution
        )
    )
    
    print(f"\n[RESULT] Success: {success}")
    print(f"[RESULT] Message: {message}")
    if result:
        print(f"[RESULT] Details: {result}")
    
    return success

if __name__ == '__main__':
    # Verify we're in LIVE mode
    paper_mode = os.getenv('KRAKEN_PAPER_TRADING', '0') == '1'
    if paper_mode:
        print("ERROR: Cannot test WebSocket in PAPER mode")
        exit(1)
    
    print("=" * 60)
    print("TESTING KRAKEN WEBSOCKET V2 ATOMIC BRACKET ORDERS")
    print("=" * 60)
    
    success = test_websocket_bracket()
    
    if success:
        print("\n✅ ATOMIC BRACKET ORDER SUCCESSFUL!")
        print("Check Kraken interface to verify:")
        print("  - Entry market order executed")
        print("  - Take-profit limit order pending")
        print("  - Stop-loss order pending")
    else:
        print("\n❌ ATOMIC BRACKET ORDER FAILED")
        print("Check logs above for error details")
