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
    
    async def add_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        order_userref: int = 0,
        validate: bool = False
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Place a single order via WebSocket v2 add_order method.
        
        Supports SPOT account (no reduce_only flags).
        
        Args:
            symbol: Trading pair in CCXT format
            side: 'buy' or 'sell'
            order_type: 'market', 'limit', or 'stop-loss'
            quantity: Order quantity
            limit_price: Limit price (for limit orders)
            stop_price: Stop trigger price (for stop-loss orders)
            order_userref: User reference ID for linking orders
            validate: If True, validates without executing
            
        Returns:
            (success, message, result_dict)
        """
        kraken_symbol = self._normalize_kraken_symbol(symbol)
        
        # Ensure fresh token
        try:
            self.get_websocket_token()
        except Exception as e:
            return False, f"Failed to get WebSocket token: {e}", None
        
        if not self.ws:
            await self.connect()
        
        # Build order request
        order_params = {
            "order_type": order_type,
            "side": side,
            "order_qty": quantity,
            "order_userref": order_userref
        }
        
        # Add type-specific parameters
        if order_type == "limit" and limit_price:
            order_params["limit_price"] = limit_price
            order_params["limit_price_type"] = "static"
        
        if order_type == "stop-loss" and stop_price:
            order_params["triggers"] = {
                "reference": "last",
                "price": stop_price,
                "price_type": "static"
            }
        
        add_request = {
            "method": "add_order",
            "params": {
                "symbol": kraken_symbol,
                "token": self.token,
                "validate": validate,
                **order_params
            },
            "req_id": int(time.time() * 1000)
        }
        
        print(f"[KRAKEN-WS] Sending {order_type} order: {side} {quantity} {kraken_symbol}")
        
        # Send and wait for response
        for attempt in range(2):
            try:
                if not self.ws:
                    return False, "WebSocket not connected", None
                await self.ws.send(json.dumps(add_request))
                
                # Wait for add_order response, skipping other messages
                result = None
                for _ in range(15):  # Increased to handle execution updates from previous orders
                    response = await asyncio.wait_for(self.ws.recv(), timeout=10.0)
                    msg = json.loads(response)
                    
                    # Skip subscription/snapshot/update messages
                    if msg.get('method') == 'subscribe' or msg.get('type') in ['snapshot', 'update']:
                        continue
                    
                    # Found our add_order response
                    if msg.get('method') == 'add_order':
                        result = msg
                        break
                
                if result is None:
                    return False, "No add_order response received", None
                
                # Check for errors
                if result.get('error'):
                    error_msg = result.get('error')
                    
                    # Retry on token expiry
                    if attempt == 0 and any(err in str(error_msg) for err in ['TokenExpired', 'TokenInvalid', 'EAuth']):
                        print(f"[KRAKEN-WS] Token expired, refreshing and retrying...")
                        self.get_websocket_token(force_refresh=True)
                        await self.connect()
                        continue
                    
                    print(f"[KRAKEN-WS-ERROR] Order failed: {error_msg}")
                    return False, f"Kraken WS error: {error_msg}", result
                
                # Success
                if result.get('success') and result.get('result'):
                    order_id = result['result'].get('order_id', 'unknown')
                    print(f"[KRAKEN-WS-SUCCESS] ✅ Order placed: {order_id}")
                    return True, f"Order placed: {order_id}", result
                else:
                    return False, "Order did not succeed", result
                    
            except asyncio.TimeoutError:
                if attempt == 0:
                    print(f"[KRAKEN-WS] Timeout on attempt {attempt+1}, retrying...")
                    continue
                return False, "WebSocket timeout", None
            except Exception as e:
                if attempt == 0:
                    print(f"[KRAKEN-WS] Exception on attempt {attempt+1}: {e}")
                    continue
                return False, f"WebSocket exception: {e}", None
        
        return False, "All retry attempts exhausted", None
    
    async def cancel_order(self, order_id: str) -> Tuple[bool, str]:
        """
        Cancel an order via WebSocket v2.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            (success, message)
        """
        try:
            self.get_websocket_token()
        except Exception as e:
            return False, f"Failed to get WebSocket token: {e}"
        
        if not self.ws:
            await self.connect()
        
        cancel_request = {
            "method": "cancel_order",
            "params": {
                "order_id": [order_id],
                "token": self.token
            },
            "req_id": int(time.time() * 1000)
        }
        
        try:
            if not self.ws:
                return False, "WebSocket not connected"
            await self.ws.send(json.dumps(cancel_request))
            response = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
            result = json.loads(response)
            
            if result.get('success'):
                print(f"[KRAKEN-WS] ✅ Order {order_id} canceled")
                return True, f"Order {order_id} canceled"
            else:
                error = result.get('error', 'Unknown error')
                print(f"[KRAKEN-WS] ❌ Cancel failed: {error}")
                return False, f"Cancel failed: {error}"
                
        except Exception as e:
            print(f"[KRAKEN-WS] Cancel exception: {e}")
            return False, f"Cancel exception: {e}"
    
    def _place_limit_order_rest(self, symbol: str, side: str, quantity: float, price: float) -> Tuple[bool, Optional[str]]:
        """Place limit order via REST API with reduce_only flag for SPOT accounts"""
        try:
            kraken_symbol = self._normalize_kraken_symbol(symbol)
            
            urlpath = "/0/private/AddOrder"
            nonce = str(int(time.time() * 1000))
            data = {
                "nonce": nonce,
                "ordertype": "limit",
                "type": side,
                "volume": str(quantity),
                "pair": kraken_symbol,
                "price": str(price),
                "reduce_only": "true"
            }
            
            headers = {
                "API-Key": self.api_key,
                "API-Sign": self._get_kraken_signature(urlpath, data)
            }
            
            response = requests.post(self.rest_url + urlpath, headers=headers, data=data)
            result = response.json()
            
            if result.get('error') and len(result['error']) > 0:
                print(f"[REST-API] Limit order error: {result['error']}")
                return False, None
            
            order_ids = result.get('result', {}).get('txid', [])
            if order_ids:
                return True, order_ids[0]
            return False, None
            
        except Exception as e:
            print(f"[REST-API] Limit order exception: {e}")
            return False, None
    
    def _place_stop_loss_order_rest(self, symbol: str, side: str, quantity: float, stop_price: float) -> Tuple[bool, Optional[str]]:
        """Place stop-loss order via REST API with reduce_only flag for SPOT accounts"""
        try:
            kraken_symbol = self._normalize_kraken_symbol(symbol)
            
            urlpath = "/0/private/AddOrder"
            nonce = str(int(time.time() * 1000))
            data = {
                "nonce": nonce,
                "ordertype": "stop-loss",
                "type": side,
                "volume": str(quantity),
                "pair": kraken_symbol,
                "price": str(stop_price),
                "reduce_only": "true"
            }
            
            headers = {
                "API-Key": self.api_key,
                "API-Sign": self._get_kraken_signature(urlpath, data)
            }
            
            response = requests.post(self.rest_url + urlpath, headers=headers, data=data)
            result = response.json()
            
            if result.get('error') and len(result['error']) > 0:
                print(f"[REST-API] Stop-loss order error: {result['error']}")
                return False, None
            
            order_ids = result.get('result', {}).get('txid', [])
            if order_ids:
                return True, order_ids[0]
            return False, None
            
        except Exception as e:
            print(f"[REST-API] Stop-loss order exception: {e}")
            return False, None
    
    def _cancel_order_rest(self, order_id: str) -> bool:
        """Cancel order via REST API"""
        try:
            urlpath = "/0/private/CancelOrder"
            nonce = str(int(time.time() * 1000))
            data = {
                "nonce": nonce,
                "txid": order_id
            }
            
            headers = {
                "API-Key": self.api_key,
                "API-Sign": self._get_kraken_signature(urlpath, data)
            }
            
            response = requests.post(self.rest_url + urlpath, headers=headers, data=data)
            result = response.json()
            
            if result.get('error') and len(result['error']) > 0:
                print(f"[REST-API] Cancel order error: {result['error']}")
                return False
            
            print(f"[REST-API] Order {order_id} canceled")
            return True
            
        except Exception as e:
            print(f"[REST-API] Cancel order exception: {e}")
            return False
    
    def _check_order_filled(self, order_id: str) -> Tuple[bool, Optional[float]]:
        """
        Check if order is filled using REST API.
        
        Returns: (is_filled, fill_price)
        """
        try:
            urlpath = "/0/private/QueryOrders"
            nonce = str(int(time.time() * 1000))
            data = {
                "nonce": nonce,
                "txid": order_id
            }
            
            headers = {
                "API-Key": self.api_key,
                "API-Sign": self._get_kraken_signature(urlpath, data)
            }
            
            response = requests.post(self.rest_url + urlpath, headers=headers, data=data)
            result = response.json()
            
            if result.get('error') and len(result['error']) > 0:
                print(f"[KRAKEN-WS] Error checking order status: {result['error']}")
                return False, None
            
            orders = result.get('result', {})
            if order_id in orders:
                order = orders[order_id]
                status = order.get('status')
                if status in ['closed', 'filled']:
                    avg_price = float(order.get('price', 0)) if order.get('price') else None
                    return True, avg_price
            
            return False, None
            
        except Exception as e:
            print(f"[KRAKEN-WS] Exception checking order fill: {e}")
            return False, None
    
    async def place_sequential_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        take_profit_price: float,
        stop_loss_price: float,
        validate: bool = False
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Place sequential bracket order for SPOT accounts.
        
        Process:
        1. Place entry market order
        2. Wait for fill confirmation (max 5 seconds)
        3. Place take-profit limit order
        4. Place stop-loss order
        5. If TP or SL fails, cancel both and return error
        
        Args:
            symbol: Trading pair in CCXT format
            side: 'buy' or 'sell'
            quantity: Order quantity
            take_profit_price: Take profit price
            stop_loss_price: Stop loss trigger price
            validate: If True, validates without executing
            
        Returns:
            (success, message, result_dict)
        """
        print(f"[BRACKET-SEQ] Starting sequential bracket for {symbol}")
        print(f"[BRACKET-SEQ] Entry: {side} {quantity} @ market")
        print(f"[BRACKET-SEQ] TP: ${take_profit_price}, SL: ${stop_loss_price}")
        
        exit_side = 'sell' if side == 'buy' else 'buy'
        result_dict: Dict[str, Optional[str]] = {
            'entry_order_id': None,
            'tp_order_id': None,
            'sl_order_id': None
        }
        
        # STEP 1: Place entry market order
        success, message, entry_result = await self.add_order(
            symbol=symbol,
            side=side,
            order_type='market',
            quantity=quantity,
            order_userref=1000 + int(time.time() % 100000),  # Unique ref
            validate=validate
        )
        
        if not success:
            return False, f"Entry order failed: {message}", result_dict
        
        entry_order_id = entry_result.get('result', {}).get('order_id') if entry_result else None
        if not entry_order_id:
            return False, "Entry order succeeded but no order ID returned", result_dict
        
        result_dict['entry_order_id'] = entry_order_id
        print(f"[BRACKET-SEQ] ✅ Entry order placed: {entry_order_id}")
        
        # STEP 2: Wait for entry fill (max 5 seconds, check every 0.5s)
        if not validate:
            filled = False
            fill_price = None
            for attempt in range(10):
                await asyncio.sleep(0.5)
                filled, fill_price = self._check_order_filled(entry_order_id)
                if filled:
                    print(f"[BRACKET-SEQ] ✅ Entry filled @ ${fill_price}")
                    break
            
            if not filled:
                return False, f"Entry order {entry_order_id} not filled within 5 seconds", result_dict
        
        # STEP 3: Place take-profit limit order via REST API (more reliable than WebSocket)
        print(f"[BRACKET-SEQ] Placing TP via REST API...")
        try:
            tp_success, tp_order_id = self._place_limit_order_rest(
                symbol=symbol,
                side=exit_side,
                quantity=quantity,
                price=take_profit_price
            )
            
            if not tp_success:
                print(f"[BRACKET-SEQ] ❌ Take-profit failed, NO ROLLBACK NEEDED (entry already filled)")
                return False, f"Take-profit order failed. Entry filled but no TP protection!", result_dict
            
            result_dict['tp_order_id'] = tp_order_id
            print(f"[BRACKET-SEQ] ✅ Take-profit placed: {tp_order_id}")
            
        except Exception as e:
            print(f"[BRACKET-SEQ] ❌ TP exception: {e}")
            return False, f"Take-profit exception: {e}. Entry filled but no TP protection!", result_dict
        
        # STEP 4: Place stop-loss order via REST API
        print(f"[BRACKET-SEQ] Placing SL via REST API...")
        try:
            sl_success, sl_order_id = self._place_stop_loss_order_rest(
                symbol=symbol,
                side=exit_side,
                quantity=quantity,
                stop_price=stop_loss_price
            )
            
            if not sl_success:
                # Rollback: Cancel TP order
                print(f"[BRACKET-SEQ] ❌ Stop-loss failed, CANCELING TP ORDER for safety...")
                if result_dict['tp_order_id'] and not validate:
                    self._cancel_order_rest(result_dict['tp_order_id'])
                return False, f"Stop-loss order failed. Entry filled, TP canceled for safety.", result_dict
            
            result_dict['sl_order_id'] = sl_order_id
            print(f"[BRACKET-SEQ] ✅ Stop-loss placed: {sl_order_id}")
            
        except Exception as e:
            print(f"[BRACKET-SEQ] ❌ SL exception: {e}")
            # Rollback: Cancel TP order
            if result_dict['tp_order_id'] and not validate:
                self._cancel_order_rest(result_dict['tp_order_id'])
            return False, f"Stop-loss exception: {e}. Entry filled, TP canceled for safety.", result_dict
        
        print(f"[BRACKET-SEQ] 🎉 COMPLETE! Entry: {result_dict['entry_order_id']}, TP: {result_dict['tp_order_id']}, SL: {result_dict['sl_order_id']}")
        
        return True, f"Sequential bracket complete: Entry {result_dict['entry_order_id']}, TP {result_dict['tp_order_id']}, SL {result_dict['sl_order_id']}", result_dict
    
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
                if not self.ws:
                    return False, "WebSocket not connected", None
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
                        if self.ws:
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
