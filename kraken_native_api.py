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


# Global instance
_kraken_native_api: Optional[KrakenNativeAPI] = None


def get_kraken_native_api() -> KrakenNativeAPI:
    """Get global Kraken native API instance."""
    global _kraken_native_api
    if _kraken_native_api is None:
        _kraken_native_api = KrakenNativeAPI()
    return _kraken_native_api
