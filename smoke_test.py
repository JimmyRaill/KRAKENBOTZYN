"""
Smoke Test Utility - End-to-End Execution Validation

Tests the complete trading pipeline from signal to execution to logging.
Uses tiny position sizes for safety (default $5-10).
"""

import os
import time
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import ccxt
from loguru import logger

from kraken_health import kraken_health_check
from execution_manager import execute_market_entry, execute_market_exit
from diagnostic_tools import generate_full_diagnostic, print_diagnostic_summary


class SmokeTestResult:
    """Result of smoke test execution"""
    
    def __init__(self):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.passed = False
        self.steps_completed = []
        self.errors = []
        self.entry_order_id = None
        self.exit_order_id = None
        self.entry_price = None
        self.exit_price = None
        self.kraken_verified = False
        self.db_verified = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "passed": self.passed,
            "steps_completed": self.steps_completed,
            "errors": self.errors,
            "entry_order_id": self.entry_order_id,
            "exit_order_id": self.exit_order_id,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "kraken_verified": self.kraken_verified,
            "db_verified": self.db_verified
        }


def run_smoke_test(
    symbol: str = "ETH/USD",
    position_size_usd: float = 10.0,
    source: str = "smoke_test"
) -> SmokeTestResult:
    """
    Run end-to-end smoke test of market-only execution system.
    
    Args:
        symbol: Trading pair to test with
        position_size_usd: Position size in USD (default $10 for safety)
        source: Source tag for logging (default "smoke_test")
        
    Returns:
        SmokeTestResult with pass/fail status and details
    """
    result = SmokeTestResult()
    
    logger.info("[SMOKE-TEST] Starting end-to-end execution validation...")
    logger.info(f"[SMOKE-TEST] Symbol: {symbol}, Size: ${position_size_usd}, Source: {source}")
    
    # Step 1: Health check
    try:
        logger.info("[SMOKE-TEST] Step 1: Kraken health check...")
        health_results = kraken_health_check()
        
        if not all(r.ok for r in health_results.values()):
            result.errors.append("Kraken health check failed")
            logger.error("[SMOKE-TEST] ❌ Health check failed - cannot proceed")
            return result
        
        result.steps_completed.append("health_check")
        logger.info("[SMOKE-TEST] ✅ Step 1 complete: Health check passed")
    except Exception as e:
        result.errors.append(f"Health check error: {e}")
        logger.error(f"[SMOKE-TEST] ❌ Health check error: {e}")
        return result
    
    # Step 2: Entry order (market buy)
    try:
        logger.info(f"[SMOKE-TEST] Step 2: Placing market BUY for ${position_size_usd}...")
        
        entry_result = execute_market_entry(
            symbol=symbol,
            size_usd=position_size_usd,
            source=source,
            reason="Smoke test entry"
        )
        
        if not entry_result.success:
            result.errors.append(f"Entry failed: {entry_result.error}")
            logger.error(f"[SMOKE-TEST] ❌ Entry failed: {entry_result.error}")
            return result
        
        result.entry_order_id = entry_result.order_id
        result.entry_price = entry_result.fill_price
        result.steps_completed.append("entry_order")
        
        logger.info(f"[SMOKE-TEST] ✅ Step 2 complete: Entry filled at ${entry_result.fill_price:.2f}")
        logger.info(f"[SMOKE-TEST]    Order ID: {entry_result.order_id}")
        logger.info(f"[SMOKE-TEST]    Quantity: {entry_result.filled_qty:.6f}")
        
        # Wait a moment for order to settle
        time.sleep(2)
        
    except Exception as e:
        result.errors.append(f"Entry execution error: {e}")
        logger.error(f"[SMOKE-TEST] ❌ Entry error: {e}")
        return result
    
    # Step 3: Exit order (market sell)
    try:
        logger.info("[SMOKE-TEST] Step 3: Placing market SELL to close position...")
        
        exit_result = execute_market_exit(
            symbol=symbol,
            source=source,
            reason="Smoke test exit"
        )
        
        if not exit_result.success:
            result.errors.append(f"Exit failed: {exit_result.error}")
            logger.error(f"[SMOKE-TEST] ❌ Exit failed: {exit_result.error}")
            # Don't return - we have a hanging position but want to verify entry logging
        else:
            result.exit_order_id = exit_result.order_id
            result.exit_price = exit_result.fill_price
            result.steps_completed.append("exit_order")
            
            logger.info(f"[SMOKE-TEST] ✅ Step 3 complete: Exit filled at ${exit_result.fill_price:.2f}")
            logger.info(f"[SMOKE-TEST]    Order ID: {exit_result.order_id}")
        
        # Wait for logging to complete
        time.sleep(2)
        
    except Exception as e:
        result.errors.append(f"Exit execution error: {e}")
        logger.error(f"[SMOKE-TEST] ⚠️  Exit error: {e}")
        # Continue to verification even if exit failed
    
    # Step 4: Verify against Kraken
    try:
        logger.info("[SMOKE-TEST] Step 4: Verifying trades against Kraken API...")
        
        api_key = os.getenv("KRAKEN_API_KEY", "")
        api_secret = os.getenv("KRAKEN_API_SECRET", "")
        
        if not api_key or not api_secret:
            result.errors.append("Cannot verify - no Kraken credentials")
            logger.warning("[SMOKE-TEST] ⚠️  Cannot verify against Kraken - no credentials")
        else:
            exchange = ccxt.kraken({
                'apiKey': api_key,
                'secret': api_secret
            })
            
            # Fetch recent trades
            recent_trades = exchange.fetch_my_trades(symbol=symbol, limit=10)
            
            # Check if our entry order exists
            entry_found = any(t.get('order') == result.entry_order_id for t in recent_trades)
            
            if entry_found:
                result.kraken_verified = True
                result.steps_completed.append("kraken_verification")
                logger.info("[SMOKE-TEST] ✅ Step 4 complete: Entry order verified on Kraken")
            else:
                result.errors.append(f"Entry order {result.entry_order_id} not found on Kraken")
                logger.error(f"[SMOKE-TEST] ❌ Entry order {result.entry_order_id} NOT found on Kraken")
        
    except Exception as e:
        result.errors.append(f"Kraken verification error: {e}")
        logger.error(f"[SMOKE-TEST] ❌ Kraken verification error: {e}")
    
    # Step 5: Verify local DB logging
    try:
        logger.info("[SMOKE-TEST] Step 5: Verifying local database logging...")
        
        import sqlite3
        
        # Check executed_orders table
        conn = sqlite3.connect('evaluation_log.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) FROM executed_orders 
            WHERE order_id = ? AND source = ?
        """, (result.entry_order_id, source))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        if count > 0:
            result.db_verified = True
            result.steps_completed.append("db_verification")
            logger.info("[SMOKE-TEST] ✅ Step 5 complete: Entry logged in executed_orders")
        else:
            result.errors.append(f"Entry order {result.entry_order_id} not found in executed_orders")
            logger.error(f"[SMOKE-TEST] ❌ Entry NOT logged in executed_orders")
        
    except Exception as e:
        result.errors.append(f"DB verification error: {e}")
        logger.error(f"[SMOKE-TEST] ❌ DB verification error: {e}")
    
    # Final assessment
    result.passed = (
        len(result.steps_completed) >= 4 and  # At least health, entry, kraken, db
        result.kraken_verified and
        result.db_verified and
        len(result.errors) == 0
    )
    
    if result.passed:
        logger.info("[SMOKE-TEST] 🎉 PASSED - All steps completed successfully")
    else:
        logger.error(f"[SMOKE-TEST] ❌ FAILED - {len(result.errors)} error(s) detected")
        for error in result.errors:
            logger.error(f"[SMOKE-TEST]    - {error}")
    
    return result


def print_smoke_test_report(result: SmokeTestResult) -> str:
    """
    Format smoke test result as human-readable report.
    
    Args:
        result: SmokeTestResult instance
        
    Returns:
        Formatted report string
    """
    lines = [
        "=" * 60,
        "SMOKE TEST REPORT",
        "=" * 60,
        f"Timestamp: {result.timestamp}",
        f"Status: {'✅ PASSED' if result.passed else '❌ FAILED'}",
        "",
        "=== STEPS COMPLETED ===",
    ]
    
    for step in result.steps_completed:
        lines.append(f"  ✅ {step}")
    
    lines.extend([
        "",
        "=== ORDER DETAILS ===",
        f"Entry Order ID: {result.entry_order_id or 'N/A'}",
        f"Entry Price: ${result.entry_price:.4f}" if result.entry_price else "Entry Price: N/A",
        f"Exit Order ID: {result.exit_order_id or 'N/A'}",
        f"Exit Price: ${result.exit_price:.4f}" if result.exit_price else "Exit Price: N/A",
        "",
        "=== VERIFICATION ===",
        f"Kraken API: {'✅ Verified' if result.kraken_verified else '❌ Not verified'}",
        f"Local DB: {'✅ Verified' if result.db_verified else '❌ Not verified'}",
    ])
    
    if result.errors:
        lines.extend([
            "",
            "=== ERRORS ===",
        ])
        for error in result.errors:
            lines.append(f"  ❌ {error}")
    
    lines.append("=" * 60)
    
    return "\n".join(lines)


if __name__ == "__main__":
    # Standalone smoke test
    import sys
    
    symbol = sys.argv[1] if len(sys.argv) > 1 else "ETH/USD"
    size_usd = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    
    print(f"\n🔬 Running smoke test: {symbol} @ ${size_usd}\n")
    
    result = run_smoke_test(symbol=symbol, position_size_usd=size_usd)
    print(print_smoke_test_report(result))
    
    # Also run diagnostic report
    print("\n" + "=" * 60)
    print("DIAGNOSTIC REPORT (POST-TEST)")
    print("=" * 60 + "\n")
    
    diagnostic = generate_full_diagnostic()
    print(print_diagnostic_summary(diagnostic))
    
    sys.exit(0 if result.passed else 1)
