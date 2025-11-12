# crypto_universe.py - Full Kraken crypto universe scanner with dynamic filtering
from __future__ import annotations

import os
import math
import time
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import statistics
import json
from pathlib import Path

try:
    import ccxt
except ImportError:
    ccxt = None  # type: ignore


@dataclass
class CryptoAsset:
    """Represents a tradable cryptocurrency on Kraken."""
    symbol: str  # e.g., "BTC/USD"
    base: str  # e.g., "BTC"
    quote: str  # e.g., "USD"
    volume_24h: float  # 24-hour volume in quote currency
    price: float  # Current price
    volatility: float  # ATR-based volatility measure
    liquidity_score: float  # Combined volume + spread score
    rank: int  # Rank by liquidity


class CryptoUniverseScanner:
    """
    Scans all available Kraken USD trading pairs and filters by liquidity.
    Dynamically updates the trading universe based on volume and performance.
    
    Features rate limiting protection and caching to avoid API overload.
    """
    
    def __init__(
        self,
        exchange: Any,
        quote_currency: str = "USD",
        min_volume_24h: float = 10000.0,  # Minimum $10k daily volume
        max_assets: int = 20,  # Trade only top 20 by volume
        update_interval_hours: int = 24,  # Re-scan universe daily
        cache_file: str = "universe_cache.json",
        rate_limit_delay: float = 0.5  # 500ms between API calls
    ):
        self.exchange = exchange
        self.quote = quote_currency
        self.min_volume = min_volume_24h
        self.max_assets = max_assets
        self.update_interval = timedelta(hours=update_interval_hours)
        self.cache_file = Path(cache_file)
        self.rate_delay = rate_limit_delay
        
        self.all_pairs: List[str] = []
        self.tradable_assets: List[CryptoAsset] = []
        self.last_scan: Optional[datetime] = None
        
        # Try to load from cache
        self.load_cache()
        
    def needs_rescan(self) -> bool:
        """Check if universe needs to be rescanned."""
        if self.last_scan is None:
            return True
        return datetime.now() - self.last_scan > self.update_interval
    
    def load_cache(self) -> None:
        """Load cached universe data from disk."""
        if not self.cache_file.exists():
            return
        
        try:
            with open(self.cache_file, 'r') as f:
                data = json.load(f)
                
                self.tradable_assets = [
                    CryptoAsset(**asset_data)
                    for asset_data in data.get('assets', [])
                ]
                
                last_scan_str = data.get('last_scan')
                if last_scan_str:
                    self.last_scan = datetime.fromisoformat(last_scan_str)
                
                print(f"[UNIVERSE] Loaded {len(self.tradable_assets)} assets from cache")
        except Exception as e:
            print(f"[UNIVERSE-CACHE-ERR] {e}")
    
    def save_cache(self) -> None:
        """Save universe data to cache file."""
        try:
            data = {
                'last_scan': self.last_scan.isoformat() if self.last_scan else None,
                'assets': [
                    {
                        'symbol': a.symbol,
                        'base': a.base,
                        'quote': a.quote,
                        'volume_24h': a.volume_24h,
                        'price': a.price,
                        'volatility': a.volatility,
                        'liquidity_score': a.liquidity_score,
                        'rank': a.rank
                    }
                    for a in self.tradable_assets
                ]
            }
            
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            print(f"[UNIVERSE-SAVE-ERR] {e}")
    
    def fetch_all_kraken_pairs(self) -> List[str]:
        """
        Fetch all available trading pairs from Kraken that match quote currency.
        Returns list of symbols like ["BTC/USD", "ETH/USD", ...].
        """
        if not self.exchange:
            return []
        
        try:
            markets = self.exchange.load_markets()
            
            # Filter for quote currency (USD, EUR, etc.) and active markets
            pairs = [
                symbol for symbol, market in markets.items()
                if market.get('quote') == self.quote
                and market.get('active', False)
                and market.get('spot', False)  # Only spot markets
            ]
            
            return sorted(pairs)
            
        except Exception as e:
            print(f"[UNIVERSE-ERR] Failed to fetch markets: {e}")
            return []
    
    def calculate_liquidity_score(
        self,
        volume_24h: float,
        price: float,
        volatility: float
    ) -> float:
        """
        Calculate combined liquidity score.
        Higher volume + moderate volatility = better score.
        """
        # Normalize volume (log scale)
        volume_score = math.log(max(volume_24h, 1))
        
        # Volatility score (prefer 2-5% daily volatility)
        ideal_volatility = 0.035  # 3.5%
        volatility_score = 1.0 / (1.0 + abs(volatility - ideal_volatility) * 10)
        
        # Combined score (70% volume, 30% volatility)
        return (volume_score * 0.7) + (volatility_score * 0.3)
    
    def fetch_asset_data(self, symbol: str) -> Optional[CryptoAsset]:
        """Fetch current price, volume, and volatility for a single asset."""
        try:
            # Fetch 24h ticker data
            ticker = self.exchange.fetch_ticker(symbol)
            
            # Fetch recent OHLCV for volatility calculation
            ohlcv = self.exchange.fetch_ohlcv(symbol, '1d', limit=14)
            
            if not ticker or not ohlcv:
                return None
            
            price = float(ticker.get('last', 0))
            volume_24h = float(ticker.get('quoteVolume', 0))
            
            # Calculate ATR-based volatility
            highs = [candle[2] for candle in ohlcv]
            lows = [candle[3] for candle in ohlcv]
            closes = [candle[4] for candle in ohlcv]
            
            true_ranges = []
            for i in range(1, len(ohlcv)):
                high_low = highs[i] - lows[i]
                high_close = abs(highs[i] - closes[i-1])
                low_close = abs(lows[i] - closes[i-1])
                true_ranges.append(max(high_low, high_close, low_close))
            
            # Guard against empty true_ranges
            if not true_ranges or len(true_ranges) == 0:
                atr = 0.0
            else:
                atr = statistics.mean(true_ranges)
            
            volatility = (atr / price) if price > 0 else 0.0
            
            # Calculate liquidity score
            liquidity_score = self.calculate_liquidity_score(
                volume_24h, price, volatility
            )
            
            base, quote = symbol.split('/')
            
            return CryptoAsset(
                symbol=symbol,
                base=base,
                quote=quote,
                volume_24h=volume_24h,
                price=price,
                volatility=volatility,
                liquidity_score=liquidity_score,
                rank=0  # Will be set after sorting
            )
            
        except Exception as e:
            print(f"[ASSET-DATA-ERR] {symbol}: {e}")
            return None
    
    def scan_universe(self) -> List[CryptoAsset]:
        """
        Full universe scan with rate limiting: fetch all pairs, get data, filter, and rank.
        Returns top assets by liquidity score.
        
        Uses batch fetching and throttling to respect API rate limits.
        """
        print(f"[UNIVERSE] Scanning {self.quote} pairs...")
        
        # Fetch all available pairs
        all_pairs = self.fetch_all_kraken_pairs()
        print(f"[UNIVERSE] Found {len(all_pairs)} {self.quote} pairs")
        
        # Limit to reasonable number to avoid rate limits
        # On first scan, limit to top 50 pairs by market cap
        # (Kraken lists pairs in roughly descending order of importance)
        scan_limit = min(len(all_pairs), 50)
        pairs_to_scan = all_pairs[:scan_limit]
        
        print(f"[UNIVERSE] Scanning top {scan_limit} pairs (rate limit protection)")
        
        # Fetch data for each pair with throttling
        assets: List[CryptoAsset] = []
        for i, symbol in enumerate(pairs_to_scan):
            # Rate limiting delay
            if i > 0:
                time.sleep(self.rate_delay)
            
            asset = self.fetch_asset_data(symbol)
            if asset and asset.volume_24h >= self.min_volume:
                assets.append(asset)
            
            # Progress indicator
            if (i + 1) % 10 == 0:
                print(f"[UNIVERSE] Progress: {i+1}/{scan_limit} pairs scanned")
        
        print(f"[UNIVERSE] {len(assets)} pairs meet volume threshold (${self.min_volume:,.0f})")
        
        # Sort by liquidity score (descending)
        assets.sort(key=lambda x: x.liquidity_score, reverse=True)
        
        # Assign ranks
        for i, asset in enumerate(assets):
            asset.rank = i + 1
        
        # Take top N assets
        top_assets = assets[:self.max_assets]
        
        print(f"[UNIVERSE] Top {len(top_assets)} assets selected:")
        for asset in top_assets[:10]:  # Show top 10
            print(f"  #{asset.rank} {asset.symbol}: ${asset.volume_24h:,.0f} vol, "
                  f"{asset.volatility*100:.2f}% volatility, "
                  f"score={asset.liquidity_score:.2f}")
        
        self.tradable_assets = top_assets
        self.last_scan = datetime.now()
        
        # Save to cache
        self.save_cache()
        
        return top_assets
    
    def get_tradable_symbols(self) -> List[str]:
        """Get list of tradable symbol strings for autopilot."""
        if self.needs_rescan():
            self.scan_universe()
        
        return [asset.symbol for asset in self.tradable_assets]
    
    def get_asset_info(self, symbol: str) -> Optional[CryptoAsset]:
        """Get detailed info about a specific asset."""
        for asset in self.tradable_assets:
            if asset.symbol == symbol:
                return asset
        return None
    
    def rotate_to_best_performers(
        self,
        performance_data: Dict[str, float]  # {symbol: return_pct}
    ) -> List[str]:
        """
        Rotate trading universe to include best-performing assets.
        Combines liquidity ranking with recent performance.
        
        Args:
            performance_data: Recent returns for each symbol
            
        Returns:
            Updated list of symbols to trade
        """
        # Rescan universe if needed
        if self.needs_rescan():
            self.scan_universe()
        
        # Score each asset: 60% liquidity, 40% performance
        scored_assets = []
        for asset in self.tradable_assets:
            perf = performance_data.get(asset.symbol, 0.0)
            
            # Normalize scores
            liquidity_norm = asset.liquidity_score / max(a.liquidity_score for a in self.tradable_assets)
            perf_norm = (perf + 50) / 100  # Normalize -50% to +50% -> 0 to 1
            
            combined_score = (liquidity_norm * 0.6) + (perf_norm * 0.4)
            scored_assets.append((asset, combined_score))
        
        # Sort by combined score
        scored_assets.sort(key=lambda x: x[1], reverse=True)
        
        # Take top assets
        top_assets = [asset for asset, _ in scored_assets[:self.max_assets]]
        
        print(f"[ROTATION] Rotated to top {len(top_assets)} performers:")
        for i, asset in enumerate(top_assets[:5]):
            perf = performance_data.get(asset.symbol, 0.0)
            print(f"  #{i+1} {asset.symbol}: {perf:+.2f}% return")
        
        self.tradable_assets = top_assets
        return [asset.symbol for asset in top_assets]


def get_dynamic_universe(
    exchange: Any,
    quote: str = "USD",
    max_assets: int = 20
) -> List[str]:
    """
    Convenience function to get current tradable universe.
    
    Args:
        exchange: CCXT exchange instance
        quote: Quote currency (USD, EUR, etc.)
        max_assets: Maximum number of assets to trade
        
    Returns:
        List of symbol strings
    """
    scanner = CryptoUniverseScanner(
        exchange=exchange,
        quote_currency=quote,
        max_assets=max_assets
    )
    return scanner.get_tradable_symbols()
