#!/usr/bin/env python3
"""
Kraken WebSocket v2 API client for atomic bracket orders.

Uses batch_add endpoint to place entry + TP + SL in one atomic request with reduce_only flags.
"""

import asyncio
import websockets
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
import os
from typing import Dict, Any, Tuple, Optional
import requests


class KrakenWebSocketV2:
    """
    Kraken WebSocket v2 client for atomic bracket orders.
    
    Critical: Uses batch_add to place entry + TP + SL in ONE atomic request.
    """
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.api_key = api_key or os.getenv("KRAKEN_API_KEY", "")
        self.api_secret = api_secret or os.getenv("KRAKEN_API_SECRET", "")
        
        if not self.api_key or not self.api_secret:
            raise ValueError("Kraken API credentials not found in environment variables")
        
        self.ws_url = "wss://ws-auth.kraken.com/v2"
        self.rest_url = "https://api.kraken.com"
        self.token = None
        self.token_expiry = 0  # Track token expiry
        self.ws = None
        
        # Symbol normalization cache (wsname lookup)
        self.symbol_cache = {}
        self.symbol_cache_expiry = 0
        
    def _get_kraken_signature(self, urlpath: str, data: Dict[str, Any]) -> str:
        """Generate Kraken API signature for REST requests"""
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data['nonce']) + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()
    
    def get_websocket_token(self, force_refresh: bool = False) -> str:
        """
        Get WebSocket authentication token via REST API.
        
        Caches token and only refreshes if expired or force_refresh=True.
        Tokens expire after 15 minutes of inactivity.
        """
        now = time.time()
        
        # Return cached token if still valid (with 1 min safety margin)
        if not force_refresh and self.token and (now < self.token_expiry - 60):
            return self.token
        
        urlpath = "/0/private/GetWebSocketsToken"
        nonce = str(int(time.time() * 1000))
        data = {"nonce": nonce}
        
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._get_kraken_signature(urlpath, data)
        }
        
        response = requests.post(self.rest_url + urlpath, headers=headers, data=data)
        result = response.json()
        
        if result.get('error') and len(result['error']) > 0:
            raise Exception(f"Failed to get WS token: {result['error']}")
        
        self.token = result['result']['token']
        self.token_expiry = now + (15 * 60)  # Expires in 15 minutes
        print(f"[KRAKEN-WS] WebSocket token obtained (expires in 15 min)")
        return self.token
    
    async def connect(self):
        """Establish WebSocket connection"""
        if not self.token:
            self.token = self.get_websocket_token()
        
        self.ws = await websockets.connect(self.ws_url)
        print(f"[KRAKEN-WS] Connected to {self.ws_url}")
        
        # Subscribe to executions to keep connection alive
        subscribe_msg = {
            "method": "subscribe",
            "params": {
                "channel": "executions",
                "token": self.token,
                "snap_orders": True
            }
        }
        await self.ws.send(json.dumps(subscribe_msg))
        
        # Read subscription response
        response = await self.ws.recv()
        print(f"[KRAKEN-WS] Subscription response: {response}")
    
    def _fetch_asset_pairs_wsnames(self) -> Dict[str, str]:
        """
        Fetch wsname mappings from Kraken AssetPairs endpoint.
        
        Returns dict mapping CCXT symbols to Kraken wsname format.
        Example: {'BTC/USD': 'XBT/USD', 'DOGE/USD': 'XDG/USD', 'ETH/USD': 'ETH/USD'}
        """
        try:
            response = requests.get(f"{self.rest_url}/0/public/AssetPairs", timeout=5)
            data = response.json()
            
            if data.get('error') and len(data['error']) > 0:
                print(f"[KRAKEN-WS] AssetPairs error: {data['error']}")
                return {}
            
            pairs = data.get('result', {})
            ccxt_to_wsname = {}
            
            # Known quote currencies for smart splitting
            # CRITICAL: Include both BTC and XBT to handle Kraken's aliasing
            quote_currencies = ['USDT', 'USDC', 'USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'CHF', 'ETH', 'BTC', 'XBT']
            
            # Build mapping from CCXT symbol to Kraken wsname
            for pair_data in pairs.values():
                altname = pair_data.get('altname')  # e.g., 'BTCUSD', 'DOGEUSD', 'XRPUSD'
                wsname = pair_data.get('wsname')  # e.g., 'XBT/USD', 'XDG/USD', 'XRP/USD'
                
                if not (altname and wsname):
                    continue
                
                # Convert altname to CCXT format by inserting slash before quote currency
                # altname examples: 'BTCUSD', 'DOGEUSD', 'XRPUSD', 'ETHUSD'
                ccxt_symbol = None
                for quote in quote_currencies:
                    if altname.endswith(quote):
                        base = altname[:-len(quote)]
                        ccxt_symbol = f"{base}/{quote}"
                        break
                
                if ccxt_symbol:
                    ccxt_to_wsname[ccxt_symbol] = wsname
                    
                    # CRITICAL: Add BTC↔XBT aliasing for BOTH base AND quote
                    # Kraken uses "XBT" but CCXT uses "BTC"
                    
                    # Case 1: XBT as base (XBT/USD) → also cache as BTC/USD
                    if ccxt_symbol.startswith('XBT/'):
                        quote = ccxt_symbol.split('/')[1]
                        btc_base_symbol = f"BTC/{quote}"
                        ccxt_to_wsname[btc_base_symbol] = wsname
                    
                    # Case 2: XBT as quote (ETH/XBT) → also cache as ETH/BTC
                    if ccxt_symbol.endswith('/XBT'):
                        base = ccxt_symbol.split('/')[0]
                        btc_quote_symbol = f"{base}/BTC"
                        ccxt_to_wsname[btc_quote_symbol] = wsname
                    
            print(f"[KRAKEN-WS] Loaded {len(ccxt_to_wsname)} symbol mappings from AssetPairs")
            return ccxt_to_wsname
            
        except Exception as e:
            print(f"[KRAKEN-WS] Failed to fetch AssetPairs: {e}")
            return {}
    
    def _normalize_kraken_symbol(self, ccxt_symbol: str) -> str:
        """
        Convert CCXT symbol format to Kraken WebSocket v2 wsname format.
        
        Uses cached AssetPairs metadata with 1-hour TTL to ensure all symbols
        are correctly normalized.
        
        Examples based on AssetPairs wsname field:
        - BTC/USD → XBT/USD (Kraken uses XBT)
        - DOGE/USD → XDG/USD (Kraken uses XDG)
        - ETH/USD → ETH/USD (no change)
        - ZEC/USD → ZEC/USD (no change)
        - AR/USD → AR/USD (no change)
        """
        # Static fallbacks for most common pairs (avoid API call when possible)
        static_map = {
            'BTC/USD': 'XBT/USD',
            'BTC/EUR': 'XBT/EUR',
            'DOGE/USD': 'XDG/USD',
            'ETH/USD': 'ETH/USD',
            'ZEC/USD': 'ZEC/USD',
            'AR/USD': 'AR/USD',
        }
        
        # Check static map first
        if ccxt_symbol in static_map:
            return static_map[ccxt_symbol]
        
        # Refresh cache if expired (1 hour TTL)
        now = time.time()
        if now > self.symbol_cache_expiry:
            print(f"[KRAKEN-WS] Refreshing AssetPairs wsname cache...")
            self.symbol_cache = self._fetch_asset_pairs_wsnames()
            self.symbol_cache_expiry = now + (60 * 60)  # 1 hour TTL
        
        # Look up in cache
        kraken_symbol = self.symbol_cache.get(ccxt_symbol, ccxt_symbol)
        
        if kraken_symbol != ccxt_symbol:
            print(f"[KRAKEN-WS] Symbol normalized: {ccxt_symbol} → {kraken_symbol}")
        
        return kraken_symbol
    
    async def place_atomic_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        take_profit_price: float,
        stop_loss_price: float,
        validate: bool = False
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Place atomic bracket order: entry + TP + SL in ONE request.
        
        Uses batch_add with reduce_only flags to prevent balance reservation conflicts.
        
        Args:
            symbol: Trading pair in CCXT format (e.g., 'BTC/USD', 'AR/USD')
            side: 'buy' or 'sell'
            quantity: Order quantity
            take_profit_price: Take profit trigger price
            stop_loss_price: Stop loss trigger price
            validate: If True, validates without executing
            
        Returns:
            (success, message, result_dict)
        """
        # Normalize symbol for Kraken (BTC/USD -> XBT/USD)
        kraken_symbol = self._normalize_kraken_symbol(symbol)
        
        # Ensure fresh token (handles expiry)
        try:
            self.get_websocket_token()
        except Exception as e:
            return False, f"Failed to get WebSocket token: {e}", None
        
        if not self.ws:
            await self.connect()
        
        exit_side = 'sell' if side == 'buy' else 'buy'
        
        # Build batch_add request with THREE orders
        # CRITICAL: Both top-level AND per-order symbol fields required per Kraken spec
        batch_request = {
            "method": "batch_add",
            "params": {
                "symbol": kraken_symbol,  # Top-level symbol (required)
                "validate": validate,
                "token": self.token,
                "orders": [
                    # ORDER 1: Entry market order (no conditional close)
                    {
                        "order_type": "market",
                        "side": side,
                        "order_qty": quantity,
                        "order_userref": 1
                    },
                    # ORDER 2: Take-profit limit order (reduce_only!)
                    {
                        "order_type": "limit",
                        "side": exit_side,
                        "order_qty": quantity,
                        "limit_price": take_profit_price,
                        "reduce_only": True,  # KEY: Prevents balance reservation
                        "order_userref": 2
                    },
                    # ORDER 3: Stop-loss order (reduce_only!)
                    {
                        "order_type": "stop-loss",
                        "side": exit_side,
                        "order_qty": quantity,
                        "triggers": {
                            "reference": "last",
                            "price": stop_loss_price,
                            "price_type": "static"
                        },
                        "reduce_only": True,  # KEY: Prevents balance reservation
                        "order_userref": 3
                    }
                ]
            },
            "req_id": int(time.time() * 1000)
        }
        
        print(f"[KRAKEN-WS] Sending atomic bracket order (batch_add):")
        print(f"[KRAKEN-WS]   Symbol: {kraken_symbol} (normalized from {symbol})")
        print(f"[KRAKEN-WS]   Entry: {side} {quantity} @ market")
        print(f"[KRAKEN-WS]   TP: {exit_side} {quantity} @ ${take_profit_price} (reduce_only)")
        print(f"[KRAKEN-WS]   SL: {exit_side} {quantity} trigger @ ${stop_loss_price} (reduce_only)")
        
        # Max 2 attempts: initial + retry on auth errors
        for attempt in range(2):
            try:
                # Send the batch request
                await self.ws.send(json.dumps(batch_request))
                
                # Wait for batch_add response, skipping subscription/snapshot messages
                result = None
                max_messages = 5  # Read up to 5 messages to find batch_add response
                for _ in range(max_messages):
                    response = await asyncio.wait_for(self.ws.recv(), timeout=10.0)
                    msg = json.loads(response)
                    
                    # Skip subscription confirmations and snapshots
                    if msg.get('method') == 'subscribe' or msg.get('type') == 'snapshot':
                        print(f"[KRAKEN-WS] Skipping message: {msg.get('method') or msg.get('type')}")
                        continue
                    
                    # Found our batch_add response
                    if msg.get('method') == 'batch_add' or (not msg.get('method') and not msg.get('type')):
                        result = msg
                        break
                    
                    print(f"[KRAKEN-WS] Unexpected message type, continuing: {json.dumps(msg, indent=2)}")
                
                if result is None:
                    print(f"[KRAKEN-WS] Never received batch_add response after {max_messages} messages")
                    return False, "No batch_add response received", None
                
                print(f"[KRAKEN-WS] Batch response received: {json.dumps(result, indent=2)}")
                
                # Check for errors
                if result.get('error'):
                    error_msg = result.get('error')
                    
                    # Retry on token expiry errors
                    if attempt == 0 and any(err in str(error_msg) for err in ['TokenExpired', 'TokenInvalid', 'EAuth']):
                        print(f"[KRAKEN-WS] Token expired/invalid, refreshing and retrying...")
                        self.get_websocket_token(force_refresh=True)
                        await self.ws.close()
                        await self.connect()
                        # Update token in request
                        batch_request['params']['token'] = self.token
                        continue  # Retry with fresh token
                    
                    print(f"[KRAKEN-WS-ERROR] Batch order failed: {error_msg}")
                    return False, f"Kraken WS error: {error_msg}", result
            
                # Check if successful
                if result.get('success') and result.get('result'):
                    orders = result.get('result', [])
                    if len(orders) >= 3:
                        entry_id = orders[0].get('order_id', 'unknown')
                        tp_id = orders[1].get('order_id', 'unknown')
                        sl_id = orders[2].get('order_id', 'unknown')
                        
                        print(f"[KRAKEN-WS-SUCCESS] ✅ ATOMIC BRACKET PLACED!")
                        print(f"[KRAKEN-WS-SUCCESS]    Entry: {entry_id}")
                        print(f"[KRAKEN-WS-SUCCESS]    TP: {tp_id}")
                        print(f"[KRAKEN-WS-SUCCESS]    SL: {sl_id}")
                        
                        return True, f"Atomic bracket placed: Entry {entry_id}, TP {tp_id}, SL {sl_id}", result
                    else:
                        return False, f"Unexpected response format: got {len(orders)} orders instead of 3", result
                else:
                    return False, "Batch order did not succeed", result
                    
            except asyncio.TimeoutError:
                if attempt == 0:
                    print(f"[KRAKEN-WS] Timeout on attempt {attempt+1}, retrying...")
                    continue
                print(f"[KRAKEN-WS-ERROR] Timeout waiting for response after 2 attempts")
                return False, "WebSocket timeout", None
            except Exception as e:
                if attempt == 0:
                    print(f"[KRAKEN-WS] Exception on attempt {attempt+1}, retrying: {e}")
                    continue
                print(f"[KRAKEN-WS-ERROR] Exception after 2 attempts: {e}")
                import traceback
                traceback.print_exc()
                return False, f"WebSocket exception: {e}", None
        
        # Should never reach here, but just in case
        return False, "All retry attempts exhausted", None
    
    async def close(self):
        """Close WebSocket connection"""
        if self.ws:
            await self.ws.close()
            print(f"[KRAKEN-WS] Connection closed")


# Singleton instance
_kraken_ws_v2 = None


def get_kraken_websocket_v2() -> KrakenWebSocketV2:
    """Get singleton instance of Kraken WebSocket v2 client"""
    global _kraken_ws_v2
    if _kraken_ws_v2 is None:
        _kraken_ws_v2 = KrakenWebSocketV2()
    return _kraken_ws_v2
