#!/usr/bin/env python3
"""
instance_guard.py - Singleton Protection for ZIN Trading Bot

This module prevents multiple live ZIN instances from trading simultaneously.
It uses a combination of heartbeat.json and a lock file to detect active instances.

Key Functions:
    acquire_instance_lock() - Returns True if this process can trade live
    release_instance_lock() - Cleans up lock on graceful shutdown

Configuration:
    INSTANCE_MAX_HEARTBEAT_AGE_MINUTES - Max age of heartbeat before considered stale (default: 5)
    INSTANCE_LOCK_AGE_MINUTES - Max age of lock file before considered stale (default: 10)

Safety Behavior:
    - If another active instance is detected, this process will NOT be allowed to trade live
    - The calling code should flip to validate-only mode or exit
    - Paper/validate-only modes bypass this check entirely
"""

import os
import json
import socket
import atexit
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from loguru import logger


HEARTBEAT_FILE = Path("data/heartbeat.json")
INSTANCE_LOCK_FILE = Path("data/meta/instance_lock.json")

_has_lock = False


def _get_utc_now() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)


def _parse_iso_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime. Returns None on failure."""
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace('Z', '+00:00')
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    """Read and parse a JSON file. Returns None if file doesn't exist or is invalid."""
    try:
        if not path.exists():
            return None
        with open(path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[INSTANCE-GUARD] Failed to read {path}: {e}")
        return None


def _write_json_file(path: Path, data: Dict[str, Any]) -> bool:
    """Write data to JSON file atomically. Returns True on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = path.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)
        temp_file.replace(path)
        return True
    except Exception as e:
        logger.error(f"[INSTANCE-GUARD] Failed to write {path}: {e}")
        return False


def _check_heartbeat_active(max_age_minutes: int) -> tuple[bool, str]:
    """
    Check if heartbeat.json indicates an active instance.
    
    Returns:
        (is_active, reason_message)
    """
    heartbeat = _read_json_file(HEARTBEAT_FILE)
    if not heartbeat:
        return False, "No heartbeat file found"
    
    last_hb_str = heartbeat.get("last_heartbeat")
    if not last_hb_str:
        return False, "Heartbeat file has no last_heartbeat field"
    
    last_hb = _parse_iso_timestamp(last_hb_str)
    if not last_hb:
        return False, f"Could not parse heartbeat timestamp: {last_hb_str}"
    
    now = _get_utc_now()
    age = now - last_hb
    age_minutes = age.total_seconds() / 60
    
    status = heartbeat.get("status", "unknown")
    mode = heartbeat.get("mode", "unknown")
    loop_count = heartbeat.get("loop_count", 0)
    
    if age_minutes <= max_age_minutes:
        return True, (
            f"Active heartbeat detected: mode={mode}, status={status}, "
            f"loop_count={loop_count}, age={age_minutes:.1f}m (threshold: {max_age_minutes}m)"
        )
    
    return False, f"Heartbeat is stale: {age_minutes:.1f}m ago (threshold: {max_age_minutes}m)"


def _check_lock_file_active(max_age_minutes: int) -> tuple[bool, str]:
    """
    Check if instance_lock.json indicates an active instance.
    
    Returns:
        (is_active, reason_message)
    """
    lock_data = _read_json_file(INSTANCE_LOCK_FILE)
    if not lock_data:
        return False, "No lock file found"
    
    locked_at_str = lock_data.get("locked_at")
    if not locked_at_str:
        return False, "Lock file has no locked_at field"
    
    locked_at = _parse_iso_timestamp(locked_at_str)
    if not locked_at:
        return False, f"Could not parse lock timestamp: {locked_at_str}"
    
    now = _get_utc_now()
    age = now - locked_at
    age_minutes = age.total_seconds() / 60
    
    owner_host = lock_data.get("owner_host", "unknown")
    owner_pid = lock_data.get("owner_pid", "unknown")
    mode = lock_data.get("mode", "unknown")
    
    if age_minutes <= max_age_minutes:
        return True, (
            f"Active lock detected: host={owner_host}, pid={owner_pid}, "
            f"mode={mode}, age={age_minutes:.1f}m (threshold: {max_age_minutes}m)"
        )
    
    return False, f"Lock is stale: {age_minutes:.1f}m ago (threshold: {max_age_minutes}m)"


def _write_lock_file(mode: str) -> bool:
    """Write our process info to the lock file."""
    lock_data = {
        "locked_at": _get_utc_now().isoformat(),
        "owner_host": socket.gethostname(),
        "owner_pid": os.getpid(),
        "mode": mode
    }
    return _write_json_file(INSTANCE_LOCK_FILE, lock_data)


def _is_pid_running(pid: int) -> bool:
    """Check if a process with given PID is running on this system."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_instance_lock(
    mode: str = "live",
    max_heartbeat_age_minutes: Optional[int] = None,
    max_lock_age_minutes: Optional[int] = None
) -> bool:
    """
    Attempt to acquire the singleton instance lock for live trading.
    
    SAFETY LOGIC (stricter than before):
    - If heartbeat is fresh (<threshold) → BLOCK (another instance is actively trading)
    - If lock is fresh (<threshold) AND owned by different host → BLOCK (another VM may be trading)
    - If lock is fresh (<threshold) AND owned by same host → Check if PID is still running
      - If PID running → BLOCK
      - If PID dead → Allow (previous instance crashed)
    - If both heartbeat AND lock are stale/absent → ALLOW
    
    Args:
        mode: The trading mode this process wants to run in ("live" or "paper")
        max_heartbeat_age_minutes: Max age of heartbeat before considered stale.
            Default: INSTANCE_MAX_HEARTBEAT_AGE_MINUTES env var or 5.
        max_lock_age_minutes: Max age of lock file before considered stale.
            Default: INSTANCE_LOCK_AGE_MINUTES env var or 10.
    
    Returns:
        True if this process can proceed with live trading.
        False if another active instance was detected.
    
    Side Effects:
        - On success, writes our process info to instance_lock.json
        - Registers atexit handler to clean up lock on exit
    """
    global _has_lock
    
    if max_heartbeat_age_minutes is None:
        max_heartbeat_age_minutes = int(os.getenv("INSTANCE_MAX_HEARTBEAT_AGE_MINUTES", "5"))
    
    if max_lock_age_minutes is None:
        max_lock_age_minutes = int(os.getenv("INSTANCE_LOCK_AGE_MINUTES", "10"))
    
    logger.info(f"[INSTANCE-GUARD] Checking for active ZIN instances...")
    logger.info(f"[INSTANCE-GUARD] Thresholds: heartbeat_age={max_heartbeat_age_minutes}m, lock_age={max_lock_age_minutes}m")
    
    heartbeat_active, heartbeat_msg = _check_heartbeat_active(max_heartbeat_age_minutes)
    logger.info(f"[INSTANCE-GUARD] Heartbeat check: {heartbeat_msg}")
    
    lock_active, lock_msg = _check_lock_file_active(max_lock_age_minutes)
    logger.info(f"[INSTANCE-GUARD] Lock file check: {lock_msg}")
    
    # BLOCK: Fresh heartbeat means another instance is actively trading
    if heartbeat_active:
        logger.error("=" * 70)
        logger.error("[INSTANCE-GUARD] ⚠️  ANOTHER ACTIVE ZIN INSTANCE DETECTED!")
        logger.error("=" * 70)
        logger.error(f"[INSTANCE-GUARD] {heartbeat_msg}")
        if lock_active:
            logger.error(f"[INSTANCE-GUARD] {lock_msg}")
        logger.error("[INSTANCE-GUARD] This process will NOT be allowed to trade live.")
        logger.error("[INSTANCE-GUARD] To proceed, stop the other instance first, or wait for it to become stale.")
        logger.error("=" * 70)
        return False
    
    # BLOCK: Fresh lock from different host (even if heartbeat is stale)
    # This could be a cross-VM situation where VM1's heartbeat hasn't propagated yet
    if lock_active:
        lock_data = _read_json_file(INSTANCE_LOCK_FILE)
        if lock_data:
            lock_host = lock_data.get("owner_host", "")
            lock_pid = lock_data.get("owner_pid", 0)
            current_host = socket.gethostname()
            
            if lock_host != current_host:
                # Different host - cannot verify if PID is running, so be safe
                logger.error("=" * 70)
                logger.error("[INSTANCE-GUARD] ⚠️  LOCK HELD BY DIFFERENT HOST!")
                logger.error("=" * 70)
                logger.error(f"[INSTANCE-GUARD] Lock owner: host={lock_host}, pid={lock_pid}")
                logger.error(f"[INSTANCE-GUARD] Current host: {current_host}")
                logger.error("[INSTANCE-GUARD] Cannot verify if other host's process is still running.")
                logger.error("[INSTANCE-GUARD] This process will NOT be allowed to trade live.")
                logger.error(f"[INSTANCE-GUARD] Wait {max_lock_age_minutes}+ minutes for lock to expire, or manually remove lock file.")
                logger.error("=" * 70)
                return False
            
            # Same host - check if owner PID is still running
            if _is_pid_running(lock_pid):
                logger.error("=" * 70)
                logger.error("[INSTANCE-GUARD] ⚠️  LOCK HELD BY RUNNING PROCESS!")
                logger.error("=" * 70)
                logger.error(f"[INSTANCE-GUARD] {lock_msg}")
                logger.error(f"[INSTANCE-GUARD] Owner PID {lock_pid} is still running on this host.")
                logger.error("[INSTANCE-GUARD] This process will NOT be allowed to trade live.")
                logger.error("[INSTANCE-GUARD] Stop the other process first.")
                logger.error("=" * 70)
                return False
            else:
                # Owner PID is dead - safe to take over
                logger.warning(f"[INSTANCE-GUARD] Lock exists but owner PID {lock_pid} is not running.")
                logger.warning("[INSTANCE-GUARD] Previous instance likely crashed. Taking over lock.")
    
    # If we get here, either:
    # - Both heartbeat and lock are stale/absent, OR
    # - Lock exists but owner PID is dead (crashed instance)
    
    if _write_lock_file(mode):
        logger.info(f"[INSTANCE-GUARD] ✅ Lock acquired successfully for this process (pid={os.getpid()}, mode={mode})")
        _has_lock = True
        atexit.register(release_instance_lock)
        return True
    else:
        logger.error("[INSTANCE-GUARD] Failed to write lock file - proceeding anyway but lock may not be claimed.")
        return True


def release_instance_lock() -> None:
    """
    Release the instance lock on graceful shutdown.
    
    This is registered as an atexit handler when acquire_instance_lock() succeeds.
    It removes the lock file so other instances can start without waiting for staleness.
    
    Note: This is best-effort. If the process crashes, the lock file will remain
    and other instances will need to wait for the staleness threshold.
    """
    global _has_lock
    
    if not _has_lock:
        return
    
    try:
        if INSTANCE_LOCK_FILE.exists():
            lock_data = _read_json_file(INSTANCE_LOCK_FILE)
            if lock_data and lock_data.get("owner_pid") == os.getpid():
                INSTANCE_LOCK_FILE.unlink()
                logger.info(f"[INSTANCE-GUARD] Lock released on shutdown (pid={os.getpid()})")
            else:
                logger.warning("[INSTANCE-GUARD] Lock file exists but owned by different process - not removing")
        _has_lock = False
    except Exception as e:
        logger.warning(f"[INSTANCE-GUARD] Failed to release lock on shutdown: {e}")


def is_dev_environment() -> bool:
    """
    Detect if we're running in a Replit development workspace vs Reserved VM.
    
    Reserved VM deployments set REPL_DEPLOYMENT=1.
    Development workspaces have REPL_ID but not REPL_DEPLOYMENT.
    """
    is_deployed = os.getenv("REPL_DEPLOYMENT", "") == "1"
    is_replit = bool(os.getenv("REPL_ID", ""))
    
    if is_deployed:
        return False
    elif is_replit:
        return True
    else:
        return False


def should_allow_live_trading() -> tuple[bool, str]:
    """
    Determine if this environment should be allowed to trade live.
    
    This implements the ALLOW_DEV_LIVE safety gate:
    - In dev workspace: ALLOW_DEV_LIVE must be "1" to trade live
    - In Reserved VM (REPL_DEPLOYMENT=1): Always allowed (production)
    - Outside Replit: Use ALLOW_DEV_LIVE check
    
    Returns:
        (allowed, reason_message)
    """
    allow_dev_live = os.getenv("ALLOW_DEV_LIVE", "0") == "1"
    is_dev = is_dev_environment()
    is_deployed = os.getenv("REPL_DEPLOYMENT", "") == "1"
    
    if is_deployed:
        return True, "Reserved VM deployment - live trading allowed"
    
    if is_dev:
        if allow_dev_live:
            return True, "Dev environment with ALLOW_DEV_LIVE=1 - live trading allowed"
        else:
            return False, (
                "Dev environment detected. Live trading is DISABLED by default for safety. "
                "Set ALLOW_DEV_LIVE=1 to enable (not recommended)."
            )
    
    if allow_dev_live:
        return True, "ALLOW_DEV_LIVE=1 - live trading allowed"
    else:
        return False, "ALLOW_DEV_LIVE not set - defaulting to safe mode"


def get_instance_status() -> Dict[str, Any]:
    """
    Get current instance guard status for diagnostics.
    
    Returns a dict with:
        - heartbeat: current heartbeat data or None
        - lock: current lock data or None
        - is_dev: whether we're in dev environment
        - allow_live: whether live trading is allowed
    """
    return {
        "heartbeat": _read_json_file(HEARTBEAT_FILE),
        "lock": _read_json_file(INSTANCE_LOCK_FILE),
        "is_dev_environment": is_dev_environment(),
        "is_deployed": os.getenv("REPL_DEPLOYMENT", "") == "1",
        "allow_dev_live": os.getenv("ALLOW_DEV_LIVE", "0") == "1",
        "current_pid": os.getpid(),
        "current_host": socket.gethostname()
    }
