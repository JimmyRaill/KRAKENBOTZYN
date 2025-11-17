"""
Kraken API Health Check System

Validates API credentials and connectivity before live trading operations.
Provides structured health status for diagnostics and fail-fast behavior.
"""

import os
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import ccxt
from loguru import logger


class KrakenHealthResult:
    """Structured result from Kraken health check"""
    
    def __init__(self, ok: bool, message: str, details: Optional[Dict[str, Any]] = None):
        self.ok = ok
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()
    
    def __repr__(self):
        status = "OK" if self.ok else "FAILED"
        return f"KrakenHealth[{status}]: {self.message}"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp
        }


def check_kraken_credentials() -> KrakenHealthResult:
    """
    Validate Kraken API credentials exist in environment.
    
    Returns:
        KrakenHealthResult indicating if credentials are configured
    """
    api_key = os.getenv("KRAKEN_API_KEY", "").strip()
    api_secret = os.getenv("KRAKEN_API_SECRET", "").strip()
    
    if not api_key or not api_secret:
        return KrakenHealthResult(
            ok=False,
            message="CRITICAL: No Kraken API credentials. Live trading and live data are DISABLED.",
            details={
                "has_api_key": bool(api_key),
                "has_api_secret": bool(api_secret)
            }
        )
    
    return KrakenHealthResult(
        ok=True,
        message="Kraken API credentials found",
        details={
            "api_key_length": len(api_key),
            "api_secret_length": len(api_secret)
        }
    )


def check_kraken_connectivity(exchange: Optional[ccxt.kraken] = None) -> KrakenHealthResult:
    """
    Test Kraken API connectivity by fetching account balance.
    
    Args:
        exchange: Optional pre-initialized exchange instance
        
    Returns:
        KrakenHealthResult indicating if API is reachable and authenticated
    """
    try:
        if exchange is None:
            api_key = os.getenv("KRAKEN_API_KEY", "")
            api_secret = os.getenv("KRAKEN_API_SECRET", "")
            
            if not api_key or not api_secret:
                return KrakenHealthResult(
                    ok=False,
                    message="Cannot test connectivity: missing credentials"
                )
            
            exchange = ccxt.kraken({
                'apiKey': api_key,
                'secret': api_secret
            })
        
        # Test fetch_balance (requires auth)
        balance = exchange.fetch_balance()
        
        total_balance_usd = float(balance.get('total', {}).get('USD', 0))
        free_balance_usd = float(balance.get('free', {}).get('USD', 0))
        
        return KrakenHealthResult(
            ok=True,
            message="Kraken API connectivity OK",
            details={
                "total_usd": total_balance_usd,
                "free_usd": free_balance_usd,
                "currencies": list(balance.get('total', {}).keys())
            }
        )
        
    except ccxt.AuthenticationError as e:
        return KrakenHealthResult(
            ok=False,
            message=f"Kraken authentication failed: {str(e)}",
            details={"error_type": "AuthenticationError"}
        )
    except ccxt.NetworkError as e:
        return KrakenHealthResult(
            ok=False,
            message=f"Kraken network error: {str(e)}",
            details={"error_type": "NetworkError"}
        )
    except Exception as e:
        return KrakenHealthResult(
            ok=False,
            message=f"Kraken connectivity check failed: {str(e)}",
            details={"error_type": type(e).__name__}
        )


def check_kraken_trade_history(exchange: Optional[ccxt.kraken] = None, limit: int = 5) -> KrakenHealthResult:
    """
    Test ability to fetch trade history from Kraken.
    
    Args:
        exchange: Optional pre-initialized exchange instance
        limit: Number of recent trades to fetch
        
    Returns:
        KrakenHealthResult indicating if trade history is accessible
    """
    try:
        if exchange is None:
            api_key = os.getenv("KRAKEN_API_KEY", "")
            api_secret = os.getenv("KRAKEN_API_SECRET", "")
            
            if not api_key or not api_secret:
                return KrakenHealthResult(
                    ok=False,
                    message="Cannot fetch trade history: missing credentials"
                )
            
            exchange = ccxt.kraken({
                'apiKey': api_key,
                'secret': api_secret
            })
        
        trades = exchange.fetch_my_trades(limit=limit)
        
        return KrakenHealthResult(
            ok=True,
            message=f"Trade history accessible ({len(trades)} recent trades found)",
            details={
                "trade_count": len(trades),
                "limit": limit
            }
        )
        
    except ccxt.AuthenticationError as e:
        return KrakenHealthResult(
            ok=False,
            message=f"Cannot access trade history: authentication failed - {str(e)}",
            details={"error_type": "AuthenticationError"}
        )
    except Exception as e:
        return KrakenHealthResult(
            ok=False,
            message=f"Trade history check failed: {str(e)}",
            details={"error_type": type(e).__name__}
        )


def kraken_health_check(exchange: Optional[ccxt.kraken] = None) -> Dict[str, KrakenHealthResult]:
    """
    Run comprehensive Kraken API health check.
    
    Args:
        exchange: Optional pre-initialized exchange instance
        
    Returns:
        Dictionary of check names to KrakenHealthResult objects
    """
    logger.info("[KRAKEN-HEALTH] Starting comprehensive health check...")
    
    results = {}
    
    # Check 1: Credentials
    cred_result = check_kraken_credentials()
    results['credentials'] = cred_result
    logger.info(f"[KRAKEN-HEALTH] Credentials: {cred_result.message}")
    
    if not cred_result.ok:
        logger.error("[KRAKEN-HEALTH] FAILED: No valid credentials - stopping health check")
        return results
    
    # Check 2: Connectivity & Balance
    conn_result = check_kraken_connectivity(exchange)
    results['connectivity'] = conn_result
    logger.info(f"[KRAKEN-HEALTH] Connectivity: {conn_result.message}")
    
    if not conn_result.ok:
        logger.error(f"[KRAKEN-HEALTH] FAILED: {conn_result.message}")
        return results
    
    # Check 3: Trade History Access
    history_result = check_kraken_trade_history(exchange)
    results['trade_history'] = history_result
    logger.info(f"[KRAKEN-HEALTH] Trade History: {history_result.message}")
    
    # Overall status
    all_ok = all(r.ok for r in results.values())
    if all_ok:
        logger.info("[KRAKEN-HEALTH] ✅ ALL CHECKS PASSED - Kraken API is fully operational")
    else:
        logger.error("[KRAKEN-HEALTH] ❌ HEALTH CHECK FAILED - Live trading may not work correctly")
    
    return results


def get_health_summary(results: Dict[str, KrakenHealthResult]) -> str:
    """
    Generate human-readable summary of health check results.
    
    Args:
        results: Dictionary of check results from kraken_health_check()
        
    Returns:
        Formatted summary string
    """
    lines = ["=== KRAKEN API HEALTH CHECK ==="]
    
    for check_name, result in results.items():
        status_icon = "✅" if result.ok else "❌"
        lines.append(f"{status_icon} {check_name}: {result.message}")
        
        if result.details:
            for key, value in result.details.items():
                lines.append(f"   - {key}: {value}")
    
    overall = "PASSED" if all(r.ok for r in results.values()) else "FAILED"
    lines.append(f"\nOverall Status: {overall}")
    lines.append("=" * 35)
    
    return "\n".join(lines)


if __name__ == "__main__":
    # Standalone health check test
    results = kraken_health_check()
    print(get_health_summary(results))
