"""
Kraken Native REST API Client
Provides direct Kraken API access for features not supported by CCXT,
specifically bracket orders with conditional close parameters.
"""

import os
import time
import base64
import hashlib
import hmac
import urllib.parse
import requests
from typing import Dict, Any, Optional, Tuple


class KrakenNativeAPI:
    """Native Kraken REST API client for advanced order features."""
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        """
        Initialize Kraken native API client.
        
        Args:
            api_key: Kraken API public key (defaults to KRAKEN_API_KEY env var)
            api_secret: Kraken API private key (defaults to KRAKEN_API_SECRET env var)
        """
        self.api_key = api_key or os.getenv("KRAKEN_API_KEY", "")
        self.api_secret = api_secret or os.getenv("KRAKEN_API_SECRET", "")
        self.api_url = "https://api.kraken.com"
        
        if not self.api_key or not self.api_secret:
            raise ValueError("Kraken API credentials not found in environment variables")
    
    def _get_signature(self, urlpath: str, data: Dict[str, str]) -> str:
        """
        Generate Kraken API-Sign signature for authentication.
        
        Args:
            urlpath: API endpoint path (e.g., '/0/private/AddOrder')
            data: POST parameters dict
            
        Returns:
            Base64-encoded signature string
        """
        # URL-encode the POST data
        postdata = urllib.parse.urlencode(data)
        
        # Combine nonce + POST data and encode
        encoded = (str(data['nonce']) + postdata).encode()
        
        # Create message: urlpath + SHA256(nonce + POST data)
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        
        # HMAC-SHA512 with base64-decoded secret key
        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        
        # Base64 encode the signature
        sigdigest = base64.b64encode(mac.digest())
        
        return sigdigest.decode()
    
    def _make_request(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make authenticated request to Kraken API.
        
        Args:
            endpoint: API endpoint (e.g., '/0/private/AddOrder')
            data: Request parameters
            
        Returns:
            API response JSON
        """
        # Add nonce if not present
        if 'nonce' not in data:
            data['nonce'] = str(int(time.time() * 1000))
        
        # Convert all values to strings for signature
        str_data = {k: str(v) for k, v in data.items()}
        
        # Generate signature
        signature = self._get_signature(endpoint, str_data)
        
        # Create headers
        headers = {
            'API-Key': self.api_key,
            'API-Sign': signature
        }
        
        # Make request
        url = self.api_url + endpoint
        response = requests.post(url, headers=headers, data=str_data)
        
        return response.json()
    
    def add_order_with_conditional_close(
        self,
        pair: str,
        order_type: str,
        side: str,
        volume: float,
        price: Optional[float] = None,
        close_ordertype: Optional[str] = None,
        close_price: Optional[str] = None,
        close_price2: Optional[str] = None,
        validate: bool = False
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Place order with conditional close (bracket order).
        
        CRITICAL: Kraken only supports ONE conditional close per order.
        You can attach EITHER take-profit OR stop-loss, not both.
        
        Args:
            pair: Trading pair (e.g., 'XBTUSD', 'ARUSD')
            order_type: Order type ('market', 'limit')
            side: 'buy' or 'sell'
            volume: Order quantity
            price: Limit price (required for 'limit' orders)
            close_ordertype: Conditional close type ('stop-loss', 'take-profit', 'stop-loss-limit', 'take-profit-limit')
            close_price: Conditional close trigger price (can be absolute or '-5%' format)
            close_price2: Limit price for conditional close limit orders
            validate: If True, validates order without placing it
            
        Returns:
            (success, message, response_dict)
        """
        # Build order parameters
        data = {
            'nonce': str(int(time.time() * 1000)),
            'pair': pair,
            'type': side,
            'ordertype': order_type,
            'volume': str(volume),
            'validate': 'true' if validate else 'false'
        }
        
        # Add price for limit orders
        if order_type == 'limit' and price is not None:
            data['price'] = str(price)
        
        # Add conditional close parameters
        if close_ordertype:
            data['close[ordertype]'] = close_ordertype
            if close_price is not None:
                data['close[price]'] = str(close_price)
            if close_price2 is not None:
                data['close[price2]'] = str(close_price2)
        
        print(f"[KRAKEN-NATIVE] Placing {side} {order_type} order: {volume} {pair}")
        if close_ordertype:
            print(f"[KRAKEN-NATIVE] Conditional close: {close_ordertype} @ {close_price}")
        
        try:
            response = self._make_request('/0/private/AddOrder', data)
            
            # Check for errors
            if response.get('error') and len(response['error']) > 0:
                error_msg = ', '.join(response['error'])
                print(f"[KRAKEN-NATIVE-ERROR] {error_msg}")
                return False, f"Kraken API error: {error_msg}", response
            
            # Extract result
            result = response.get('result', {})
            tx_ids = result.get('txid', [])
            descr = result.get('descr', {})
            
            if tx_ids:
                order_id = tx_ids[0] if tx_ids else 'unknown'
                order_descr = descr.get('order', 'no description')
                close_descr = descr.get('close', '')
                
                print(f"[KRAKEN-NATIVE-SUCCESS] Order placed: {order_id}")
                print(f"[KRAKEN-NATIVE-SUCCESS] {order_descr}")
                if close_descr:
                    print(f"[KRAKEN-NATIVE-SUCCESS] Conditional close: {close_descr}")
                
                return True, f"Order {order_id} placed successfully", result
            else:
                return False, "No transaction ID returned", response
                
        except Exception as e:
            error_msg = str(e)
            print(f"[KRAKEN-NATIVE-EXCEPTION] {error_msg}")
            return False, f"Exception: {error_msg}", None
    
    def place_oco_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_type: str = 'market',
        entry_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        validate: bool = False
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Place OCO bracket order with BOTH take-profit and stop-loss.
        
        Uses Kraken's native 'stop-loss-profit' ordertype which creates
        true exchange-level OCO behavior:
        - When TP fills -> SL auto-cancels
        - When SL fills -> TP auto-cancels
        
        CRITICAL: Works on SPOT accounts (no margin/leverage required).
        
        Args:
            symbol: Trading pair in CCXT format (e.g., 'BTC/USD', 'AR/USD')
            side: 'buy' or 'sell'
            quantity: Order quantity
            entry_type: 'market' or 'limit'
            entry_price: Required if entry_type='limit'
            stop_loss_price: Stop loss trigger price (absolute)
            take_profit_price: Take profit trigger price (absolute)
            validate: If True, validates without executing
            
        Returns:
            (success, message, result_dict)
        """
        # Normalize symbol to Kraken format (BTC/USD -> XBTUSD)
        kraken_pair = self._normalize_symbol_to_kraken_pair(symbol)
        
        if not stop_loss_price or not take_profit_price:
            return False, "Both stop_loss_price and take_profit_price are required for OCO brackets", None
        
        # Build order parameters
        data = {
            'nonce': str(int(time.time() * 1000)),
            'pair': kraken_pair,
            'type': side,
            'ordertype': entry_type,
            'volume': str(quantity),
            'validate': 'true' if validate else 'false'
        }
        
        # Add entry price for limit orders
        if entry_type == 'limit':
            if entry_price is None:
                return False, "entry_price required for limit orders", None
            data['price'] = str(entry_price)
        
        # Add OCO bracket using stop-loss-profit ordertype
        # CRITICAL: This creates TRUE OCO behavior at exchange level
        data['close[ordertype]'] = 'stop-loss-profit'
        data['close[price]'] = str(stop_loss_price)   # SL trigger
        data['close[price2]'] = str(take_profit_price)  # TP trigger
        
        print(f"[KRAKEN-OCO] Placing {side} {entry_type} order: {quantity} {symbol}")
        print(f"[KRAKEN-OCO] Stop-Loss: ${stop_loss_price:.4f}")
        print(f"[KRAKEN-OCO] Take-Profit: ${take_profit_price:.4f}")
        
        try:
            response = self._make_request('/0/private/AddOrder', data)
            
            # Check for errors
            if response.get('error') and len(response['error']) > 0:
                error_msg = ', '.join(response['error'])
                print(f"[KRAKEN-OCO-ERROR] {error_msg}")
                return False, f"Kraken OCO error: {error_msg}", response
            
            # Extract result
            result = response.get('result', {})
            tx_ids = result.get('txid', [])
            descr = result.get('descr', {})
            
            if tx_ids:
                order_id = tx_ids[0] if tx_ids else 'unknown'
                order_descr = descr.get('order', 'no description')
                close_descr = descr.get('close', '')
                
                print(f"[KRAKEN-OCO-SUCCESS] ✅ Order placed: {order_id}")
                print(f"[KRAKEN-OCO-SUCCESS] Entry: {order_descr}")
                if close_descr:
                    print(f"[KRAKEN-OCO-SUCCESS] OCO Bracket: {close_descr}")
                
                # For market orders, query fill details after brief wait
                fill_data = None
                if entry_type == 'market' and not validate:
                    print(f"[KRAKEN-OCO] Querying order details for fill data...")
                    time.sleep(1)  # Brief wait for market order to fill
                    
                    order_query = self.query_orders([order_id])
                    if order_query.get('result'):
                        order_details = order_query['result'].get(order_id, {})
                        fill_data = {
                            'status': order_details.get('status'),
                            'filled': float(order_details.get('vol_exec', 0)),
                            'average': float(order_details.get('price', 0)) if order_details.get('price') else None,
                            'remaining': float(order_details.get('vol', 0)) - float(order_details.get('vol_exec', 0))
                        }
                        print(f"[KRAKEN-OCO] Fill: {fill_data['filled']:.8f} @ ${fill_data['average']:.4f}" if fill_data['average'] else "[KRAKEN-OCO] Fill data retrieved")
                
                # Include fill data in result if available
                enriched_result = result.copy()
                if fill_data:
                    enriched_result['fill_data'] = fill_data
                
                return True, f"OCO bracket order {order_id} placed successfully", enriched_result
            else:
                return False, "No transaction ID returned", response
                
        except Exception as e:
            error_msg = str(e)
            print(f"[KRAKEN-OCO-EXCEPTION] {error_msg}")
            return False, f"Exception: {error_msg}", None
    
    def query_orders(self, order_ids: list) -> Dict[str, Any]:
        """
        Query order details from Kraken.
        
        Args:
            order_ids: List of Kraken order IDs to query
            
        Returns:
            Kraken API response with order details
        """
        data = {
            'nonce': str(int(time.time() * 1000)),
            'txid': ','.join(order_ids)
        }
        
        try:
            response = self._make_request('/0/private/QueryOrders', data)
            return response
        except Exception as e:
            print(f"[KRAKEN-QUERY] Error querying orders: {e}")
            return {'error': [str(e)]}
    
    def _normalize_symbol_to_kraken_pair(self, symbol: str) -> str:
        """
        Convert CCXT symbol format to Kraken pair format.
        
        Examples:
            'BTC/USD' -> 'XBTUSD'
            'ETH/USD' -> 'ETHUSD'
            'AR/USD' -> 'ARUSD'
            'DOGE/USD' -> 'XDGUSD'
        
        Args:
            symbol: Symbol in CCXT format (e.g., 'BTC/USD')
            
        Returns:
            Kraken pair format (e.g., 'XBTUSD')
        """
        # Remove slash
        pair = symbol.replace('/', '')
        
        # Apply Kraken symbol mappings
        symbol_map = {
            'BTC': 'XBT',
            'DOGE': 'XDG',
        }
        
        # Split into base and quote
        if 'USD' in pair:
            base = pair.replace('USD', '')
            quote = 'USD'
            
            # Map base if needed
            base = symbol_map.get(base, base)
            
            return base + quote
        
        # Fallback: just remove slash
        return pair


# Global instance
_kraken_native_api: Optional[KrakenNativeAPI] = None


def get_kraken_native_api() -> KrakenNativeAPI:
    """Get global Kraken native API instance."""
    global _kraken_native_api
    if _kraken_native_api is None:
        _kraken_native_api = KrakenNativeAPI()
    return _kraken_native_api
