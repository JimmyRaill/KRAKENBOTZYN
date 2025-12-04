#!/usr/bin/env python3
"""
main.py - Production Entry Point for Zin Trading Bot (Reserved VM Deployment)

This module orchestrates both components of the Zin trading system:
1. Autopilot trading loop (from autopilot.py)
2. API/health server (FastAPI from api.py)

Both run concurrently using threading, with failure isolation so one
component crashing doesn't kill the other.

SAFETY FEATURES:
- Instance Guard: Prevents multiple live ZIN instances from trading simultaneously
- Dev Environment Safety: Dev workspace defaults to validate-only mode
- ALLOW_DEV_LIVE env var: Must be set to "1" to allow live trading in dev

Usage:
    python main.py

For Reserved VM deployment, configure .replit:
    [deployment]
    run = ["python3", "main.py"]
    deploymentTarget = "vm"
"""

import os
import sys
import json
import time
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from loguru import logger

from instance_guard import (
    acquire_instance_lock,
    should_allow_live_trading,
    is_dev_environment,
    get_instance_status
)


# ============================================================================
# HEARTBEAT READER (for /health endpoint)
# ============================================================================
HEARTBEAT_FILE = Path("data/heartbeat.json")

def read_heartbeat() -> dict:
    """
    Read the current heartbeat file.
    Returns status info including staleness check.
    Used by the /health endpoint in api.py.
    """
    try:
        if not HEARTBEAT_FILE.exists():
            return {
                "status": "no_heartbeat",
                "mode": "unknown",
                "last_heartbeat": None,
                "message": "Heartbeat file not found - autopilot may not have started yet"
            }
        
        with open(HEARTBEAT_FILE, 'r') as f:
            data = json.load(f)
        
        # Check staleness (>10 minutes = stale)
        last_hb = data.get("last_heartbeat")
        if last_hb:
            last_dt = datetime.fromisoformat(last_hb.replace('Z', '+00:00'))
            age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
            
            if age_seconds > 600:  # 10 minutes
                data["status"] = "stale"
                data["stale_seconds"] = int(age_seconds)
                data["message"] = f"Heartbeat is {int(age_seconds)}s old - autopilot may be stuck or crashed"
            else:
                data["age_seconds"] = int(age_seconds)
        
        return data
        
    except Exception as e:
        return {
            "status": "error",
            "mode": "unknown",
            "last_heartbeat": None,
            "message": f"Error reading heartbeat: {str(e)}"
        }


# ============================================================================
# DISCORD STARTUP NOTIFICATION
# ============================================================================

def send_startup_notification():
    """Send Discord notification when Zin starts up."""
    try:
        from discord_notifications import send_notification
        from exchange_manager import get_mode_str
        from trading_config import get_zin_version
        
        mode = get_mode_str().upper()
        version = get_zin_version()
        
        # Determine if this is a Reserved VM deployment
        is_deployed = os.getenv("REPLIT_DEPLOYMENT", "") == "1"
        deploy_type = "Reserved VM" if is_deployed else "Development Workspace"
        
        message = (
            f"🚀 **ZIN STARTUP** 🚀\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Version:** {version}\n"
            f"**Mode:** {mode}\n"
            f"**Environment:** {deploy_type}\n"
            f"**Time:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Autopilot and API server are now running."
        )
        
        send_notification(message)
        logger.info("[STARTUP] Discord notification sent")
        
    except Exception as e:
        logger.warning(f"[STARTUP] Failed to send Discord notification: {e}")


# ============================================================================
# AUTOPILOT THREAD
# ============================================================================

def start_autopilot():
    """
    Start the autopilot trading loop.
    This calls the existing autopilot.py logic exactly as-is.
    No trading logic is modified - only orchestration.
    """
    logger.info("[AUTOPILOT-THREAD] Starting autopilot trading loop...")
    
    try:
        # Import the autopilot module and run it exactly as if called from __main__
        import autopilot
        from kraken_health import kraken_health_check, get_health_summary
        
        # Run Kraken health check first (same as autopilot's __main__)
        logger.info("[AUTOPILOT-THREAD] Running Kraken health check...")
        health_results = kraken_health_check()
        logger.info(get_health_summary(health_results))
        
        # Check health results
        if not all(r.ok for r in health_results.values()):
            logger.warning("[AUTOPILOT-THREAD] Kraken API health check has issues")
            validate_mode = os.getenv("KRAKEN_VALIDATE_ONLY", "0") == "1"
            if not validate_mode:
                logger.error("[AUTOPILOT-THREAD] Cannot run in LIVE mode without valid Kraken credentials")
                return
            else:
                logger.warning("[AUTOPILOT-THREAD] Continuing in PAPER mode")
        else:
            logger.info("[AUTOPILOT-THREAD] Kraken API health check PASSED")
        
        # Call the existing run_forever() - this contains all the trading logic
        # The heartbeat is written inside run_forever() after each loop
        logger.info("[AUTOPILOT-THREAD] Entering run_forever()...")
        autopilot.run_forever()
        
    except Exception as e:
        logger.error(f"[AUTOPILOT-THREAD] Fatal error: {e}")
        logger.error(traceback.format_exc())
        # Write error heartbeat
        try:
            HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(HEARTBEAT_FILE, 'w') as f:
                json.dump({
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "mode": "unknown",
                    "status": "crashed",
                    "error": str(e)
                }, f, indent=2)
        except Exception:
            pass


def run_autopilot_thread():
    """
    Run autopilot in a separate thread with restart capability.
    If autopilot crashes, log it but don't crash the whole process.
    """
    while True:
        try:
            logger.info("[AUTOPILOT-THREAD] Starting autopilot thread...")
            start_autopilot()
        except Exception as e:
            logger.error(f"[AUTOPILOT-THREAD] Thread crashed: {e}")
            logger.error(traceback.format_exc())
        
        # If we get here, autopilot exited unexpectedly
        # Wait 30 seconds before attempting restart
        logger.warning("[AUTOPILOT-THREAD] Autopilot exited. Restarting in 30 seconds...")
        time.sleep(30)


# ============================================================================
# API SERVER THREAD
# ============================================================================

def run_api_server():
    """
    Run the FastAPI server (uvicorn).
    This serves the chat interface, dashboard, and health endpoints.
    """
    logger.info("[API-THREAD] Starting API server on port 5000...")
    
    try:
        # Import the FastAPI app
        from api import app
        
        # Run uvicorn (this blocks)
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=5000,
            log_level="info",
            access_log=True
        )
        
    except Exception as e:
        logger.error(f"[API-THREAD] Fatal error: {e}")
        logger.error(traceback.format_exc())


def run_api_thread():
    """
    Run API server in a thread with restart capability.
    """
    while True:
        try:
            logger.info("[API-THREAD] Starting API server thread...")
            run_api_server()
        except Exception as e:
            logger.error(f"[API-THREAD] Thread crashed: {e}")
            logger.error(traceback.format_exc())
        
        # If we get here, API server exited unexpectedly
        logger.warning("[API-THREAD] API server exited. Restarting in 10 seconds...")
        time.sleep(10)


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """
    Main entry point for Reserved VM deployment.
    Starts both autopilot and API server in parallel threads.
    
    SAFETY CHECKS (in order):
    1. Dev environment check - defaults to validate-only unless ALLOW_DEV_LIVE=1
    2. Instance guard - prevents multiple live instances
    """
    print("=" * 60)
    print("🤖 ZIN TRADING BOT - PRODUCTION STARTUP")
    print("=" * 60)
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"PID: {os.getpid()}")
    print("=" * 60)
    
    # Load environment
    from dotenv import load_dotenv
    load_dotenv()
    
    # Print config summary
    from exchange_manager import get_mode_str
    from trading_config import get_zin_version
    mode = get_mode_str()
    version = get_zin_version()
    
    print(f"Version: {version}")
    print(f"Mode: {mode.upper()}")
    print(f"Execution Mode: {os.getenv('EXECUTION_MODE', 'MARKET_ONLY')}")
    print(f"Environment: {'Reserved VM' if os.getenv('REPLIT_DEPLOYMENT') == '1' else 'Development Workspace'}")
    print("=" * 60)
    
    # Ensure data directory exists for heartbeat
    Path("data").mkdir(exist_ok=True)
    Path("data/meta").mkdir(exist_ok=True)
    
    # =========================================================================
    # SAFETY CHECK 1: Dev Environment Gate
    # =========================================================================
    # Dev workspaces default to validate-only mode for safety.
    # Set ALLOW_DEV_LIVE=1 to enable live trading in dev (not recommended).
    
    validate_only = os.getenv("KRAKEN_VALIDATE_ONLY", "0") == "1"
    is_live_mode = mode.lower() == "live" and not validate_only
    
    if is_live_mode:
        allow_live, reason = should_allow_live_trading()
        print(f"[SAFETY] {reason}")
        
        if not allow_live:
            print("=" * 60)
            print("[SAFETY] ⚠️  FORCING VALIDATE-ONLY MODE FOR SAFETY")
            print("[SAFETY] This process will NOT place real orders.")
            print("[SAFETY] To enable live trading in dev, set ALLOW_DEV_LIVE=1")
            print("=" * 60)
            os.environ["KRAKEN_VALIDATE_ONLY"] = "1"
            validate_only = True
            is_live_mode = False
    
    # =========================================================================
    # SAFETY CHECK 2: Instance Guard (Singleton Protection)
    # =========================================================================
    # Prevents multiple live ZIN instances from trading simultaneously.
    # Uses heartbeat.json and instance_lock.json to detect active instances.
    
    if is_live_mode:
        print("[INSTANCE-GUARD] Checking for other active ZIN instances...")
        
        if not acquire_instance_lock(mode="live"):
            print("=" * 60)
            print("[INSTANCE-GUARD] ⚠️  ANOTHER ACTIVE INSTANCE DETECTED!")
            print("[INSTANCE-GUARD] Forcing this process to validate-only mode.")
            print("[INSTANCE-GUARD] Only ONE live trading instance is allowed.")
            print("[INSTANCE-GUARD] Stop the other instance first, or wait 5+ minutes.")
            print("=" * 60)
            os.environ["KRAKEN_VALIDATE_ONLY"] = "1"
            validate_only = True
            is_live_mode = False
        else:
            print("[INSTANCE-GUARD] ✅ Lock acquired - this is the primary live instance")
    else:
        print(f"[INSTANCE-GUARD] Skipping lock (validate_only={validate_only}, mode={mode})")
    
    # Final mode after safety checks
    final_mode = "VALIDATE-ONLY (SAFE)" if validate_only else f"LIVE ({mode.upper()})"
    print("=" * 60)
    print(f"[FINAL MODE] {final_mode}")
    print("=" * 60)
    
    # =========================================================================
    # CRITICAL: Reload exchange manager AFTER safety checks finalize the mode
    # =========================================================================
    # The ExchangeManager singleton is created at import time, before safety checks.
    # Now that we've finalized KRAKEN_VALIDATE_ONLY, reload so it picks up the correct value.
    from exchange_manager import reload_exchange_config, is_paper_mode
    reload_exchange_config()
    
    # Log the actual exchange state for debugging
    is_deployed = os.getenv("REPLIT_DEPLOYMENT", "") == "1"
    deploy_env = "reserved_vm" if is_deployed else "dev"
    exchange_type = "PaperSimulator" if is_paper_mode() else "KrakenLive"
    print(f"[STARTUP] env={deploy_env} | mode={'validate-only' if validate_only else 'live'} | exchange={exchange_type}")
    
    # Sanity check: ensure env var and exchange state match
    env_validate = os.getenv("KRAKEN_VALIDATE_ONLY", "0") == "1"
    if env_validate != is_paper_mode():
        print(f"[WARNING] Mode mismatch! KRAKEN_VALIDATE_ONLY={env_validate} but is_paper_mode()={is_paper_mode()}")
    
    # Send startup notification to Discord
    send_startup_notification()
    
    # Create threads for both components
    autopilot_thread = threading.Thread(
        target=run_autopilot_thread,
        name="AutopilotThread",
        daemon=False  # Keep running even if main thread exits
    )
    
    api_thread = threading.Thread(
        target=run_api_thread,
        name="APIThread", 
        daemon=False
    )
    
    # Start both threads
    print("[MAIN] Starting autopilot thread...")
    autopilot_thread.start()
    
    print("[MAIN] Starting API server thread...")
    api_thread.start()
    
    print("[MAIN] ✅ Both threads started successfully")
    print("[MAIN] Zin is now running 24/7 on Reserved VM")
    print("=" * 60)
    
    # Wait for both threads (they should run forever)
    # If one crashes, the restart logic in each thread handler will restart it
    try:
        autopilot_thread.join()
        api_thread.join()
    except KeyboardInterrupt:
        print("\n[MAIN] Shutdown requested via Ctrl+C")
        sys.exit(0)


if __name__ == "__main__":
    main()
