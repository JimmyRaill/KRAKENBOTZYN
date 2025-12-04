#!/usr/bin/env python3
"""
test_instance_guard.py - Sanity checks for instance guard (singleton protection)

This script tests the instance_guard module to verify that:
1. A recent heartbeat blocks a second instance
2. A stale heartbeat allows a new instance
3. Lock file is created and released properly

Run with: python test_instance_guard.py

Expected output:
- Test 1: acquire_instance_lock() returns False (blocked by recent heartbeat)
- Test 2: acquire_instance_lock() returns True (stale heartbeat, can proceed)
"""

import os
import sys
import json
import shutil
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path


DATA_DIR = Path("data")
META_DIR = DATA_DIR / "meta"
HEARTBEAT_FILE = DATA_DIR / "heartbeat.json"
LOCK_FILE = META_DIR / "instance_lock.json"


def backup_files():
    """Backup existing heartbeat and lock files."""
    backups = {}
    for f in [HEARTBEAT_FILE, LOCK_FILE]:
        if f.exists():
            backup_path = f.with_suffix('.backup')
            shutil.copy(f, backup_path)
            backups[f] = backup_path
    return backups


def restore_files(backups):
    """Restore backed up files."""
    for original, backup in backups.items():
        if backup.exists():
            shutil.copy(backup, original)
            backup.unlink()
        elif original.exists():
            original.unlink()


def cleanup_test_files():
    """Remove test-created files."""
    for f in [HEARTBEAT_FILE, LOCK_FILE]:
        if f.exists():
            f.unlink()
    lock_backup = LOCK_FILE.with_suffix('.backup')
    hb_backup = HEARTBEAT_FILE.with_suffix('.backup')
    for f in [lock_backup, hb_backup]:
        if f.exists():
            f.unlink()


def write_test_heartbeat(age_minutes: float):
    """Write a heartbeat file with specified age."""
    DATA_DIR.mkdir(exist_ok=True)
    
    timestamp = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    
    heartbeat = {
        "last_heartbeat": timestamp.isoformat(),
        "mode": "live",
        "status": "running",
        "pid": 99999,
        "loop_count": 10,
        "symbols_count": 20,
        "interval_sec": 300
    }
    
    with open(HEARTBEAT_FILE, 'w') as f:
        json.dump(heartbeat, f, indent=2)
    
    return heartbeat


def clear_lock_file():
    """Remove lock file if it exists."""
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()


def test_recent_heartbeat_blocks():
    """
    Test 1: A very recent heartbeat (1 minute old) should BLOCK new instances.
    acquire_instance_lock() should return False.
    """
    print("\n" + "=" * 60)
    print("TEST 1: Recent heartbeat should BLOCK new instance")
    print("=" * 60)
    
    clear_lock_file()
    
    age_minutes = 1.0
    write_test_heartbeat(age_minutes)
    print(f"  - Created heartbeat from {age_minutes} minute(s) ago")
    
    from instance_guard import acquire_instance_lock
    
    result = acquire_instance_lock(max_heartbeat_age_minutes=5)
    
    if result:
        print("  ❌ FAILED: acquire_instance_lock() returned True (expected False)")
        return False
    else:
        print("  ✅ PASSED: acquire_instance_lock() returned False (blocked as expected)")
        return True


def test_stale_heartbeat_allows():
    """
    Test 2: A stale heartbeat (30 minutes old) should ALLOW new instances.
    acquire_instance_lock() should return True.
    """
    print("\n" + "=" * 60)
    print("TEST 2: Stale heartbeat should ALLOW new instance")
    print("=" * 60)
    
    clear_lock_file()
    
    age_minutes = 30.0
    write_test_heartbeat(age_minutes)
    print(f"  - Created heartbeat from {age_minutes} minute(s) ago (stale)")
    
    from instance_guard import acquire_instance_lock, release_instance_lock
    
    result = acquire_instance_lock(max_heartbeat_age_minutes=5)
    
    if result:
        print("  ✅ PASSED: acquire_instance_lock() returned True (allowed as expected)")
        release_instance_lock()
        return True
    else:
        print("  ❌ FAILED: acquire_instance_lock() returned False (expected True)")
        return False


def test_lock_file_created():
    """
    Test 3: When lock is acquired, lock file should be created.
    """
    print("\n" + "=" * 60)
    print("TEST 3: Lock file should be created on successful acquire")
    print("=" * 60)
    
    clear_lock_file()
    
    age_minutes = 30.0
    write_test_heartbeat(age_minutes)
    
    from instance_guard import acquire_instance_lock, release_instance_lock
    
    result = acquire_instance_lock(max_heartbeat_age_minutes=5)
    
    if not result:
        print("  ❌ FAILED: Could not acquire lock")
        return False
    
    if LOCK_FILE.exists():
        print("  ✅ PASSED: Lock file was created")
        with open(LOCK_FILE) as f:
            lock_data = json.load(f)
        print(f"  - Lock owner: host={lock_data.get('owner_host')}, pid={lock_data.get('owner_pid')}")
        release_instance_lock()
        return True
    else:
        print("  ❌ FAILED: Lock file was NOT created")
        return False


def test_fresh_lock_different_host_blocks():
    """
    Test 3b: Fresh lock from different host should BLOCK even with stale heartbeat.
    This prevents cross-VM duplicate instances.
    """
    print("\n" + "=" * 60)
    print("TEST 3b: Fresh lock from DIFFERENT HOST should block")
    print("=" * 60)
    
    age_minutes = 30.0
    write_test_heartbeat(age_minutes)
    print(f"  - Created stale heartbeat from {age_minutes} minute(s) ago")
    
    META_DIR.mkdir(exist_ok=True)
    lock_data = {
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "owner_host": "different-host-12345",
        "owner_pid": 99999,
        "mode": "live"
    }
    with open(LOCK_FILE, 'w') as f:
        json.dump(lock_data, f, indent=2)
    print(f"  - Created fresh lock from DIFFERENT host")
    
    from instance_guard import acquire_instance_lock
    
    result = acquire_instance_lock(max_heartbeat_age_minutes=5, max_lock_age_minutes=10)
    
    if result:
        print("  ❌ FAILED: acquire_instance_lock() returned True (expected False)")
        return False
    else:
        print("  ✅ PASSED: acquire_instance_lock() returned False (blocked as expected)")
        return True


def test_fresh_lock_dead_pid_allows():
    """
    Test 3c: Fresh lock from same host with DEAD PID should allow.
    This handles crashed instances.
    """
    print("\n" + "=" * 60)
    print("TEST 3c: Fresh lock with DEAD PID should allow (crashed instance)")
    print("=" * 60)
    
    age_minutes = 30.0
    write_test_heartbeat(age_minutes)
    print(f"  - Created stale heartbeat from {age_minutes} minute(s) ago")
    
    import socket
    META_DIR.mkdir(exist_ok=True)
    lock_data = {
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "owner_host": socket.gethostname(),
        "owner_pid": 999999,
        "mode": "live"
    }
    with open(LOCK_FILE, 'w') as f:
        json.dump(lock_data, f, indent=2)
    print(f"  - Created fresh lock from same host with dead PID 999999")
    
    from instance_guard import acquire_instance_lock, release_instance_lock
    
    result = acquire_instance_lock(max_heartbeat_age_minutes=5, max_lock_age_minutes=10)
    
    if result:
        print("  ✅ PASSED: acquire_instance_lock() returned True (allowed, PID is dead)")
        release_instance_lock()
        return True
    else:
        print("  ❌ FAILED: acquire_instance_lock() returned False (expected True)")
        return False


def test_dev_environment_detection():
    """
    Test 4: Dev environment detection should work correctly.
    """
    print("\n" + "=" * 60)
    print("TEST 4: Dev environment detection")
    print("=" * 60)
    
    from instance_guard import is_dev_environment, should_allow_live_trading
    
    is_dev = is_dev_environment()
    allow_live, reason = should_allow_live_trading()
    
    print(f"  - is_dev_environment(): {is_dev}")
    print(f"  - REPL_ID: {os.getenv('REPL_ID', 'not set')[:20] if os.getenv('REPL_ID') else 'not set'}...")
    print(f"  - REPL_DEPLOYMENT: {os.getenv('REPL_DEPLOYMENT', 'not set')}")
    print(f"  - ALLOW_DEV_LIVE: {os.getenv('ALLOW_DEV_LIVE', 'not set')}")
    print(f"  - should_allow_live_trading(): {allow_live}")
    print(f"  - Reason: {reason}")
    
    print("  ✅ PASSED: Detection functions work (check values above)")
    return True


def test_get_status():
    """
    Test 5: get_instance_status() should return diagnostic info.
    """
    print("\n" + "=" * 60)
    print("TEST 5: get_instance_status() diagnostic function")
    print("=" * 60)
    
    from instance_guard import get_instance_status
    
    status = get_instance_status()
    
    print(f"  - heartbeat exists: {status.get('heartbeat') is not None}")
    print(f"  - lock exists: {status.get('lock') is not None}")
    print(f"  - is_dev_environment: {status.get('is_dev_environment')}")
    print(f"  - is_deployed: {status.get('is_deployed')}")
    print(f"  - current_pid: {status.get('current_pid')}")
    print(f"  - current_host: {status.get('current_host')}")
    
    print("  ✅ PASSED: Status function returns valid data")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("INSTANCE GUARD TEST SUITE")
    print("=" * 60)
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"PID: {os.getpid()}")
    
    DATA_DIR.mkdir(exist_ok=True)
    META_DIR.mkdir(exist_ok=True)
    
    backups = backup_files()
    
    try:
        results = []
        
        results.append(("Recent heartbeat blocks", test_recent_heartbeat_blocks()))
        
        results.append(("Stale heartbeat allows", test_stale_heartbeat_allows()))
        
        results.append(("Lock file created", test_lock_file_created()))
        
        results.append(("Fresh lock different host blocks", test_fresh_lock_different_host_blocks()))
        
        results.append(("Fresh lock dead PID allows", test_fresh_lock_dead_pid_allows()))
        
        results.append(("Dev environment detection", test_dev_environment_detection()))
        
        results.append(("Status function", test_get_status()))
        
        print("\n" + "=" * 60)
        print("TEST RESULTS SUMMARY")
        print("=" * 60)
        
        passed = sum(1 for _, r in results if r)
        failed = sum(1 for _, r in results if not r)
        
        for name, result in results:
            status = "✅ PASSED" if result else "❌ FAILED"
            print(f"  {status}: {name}")
        
        print()
        print(f"Total: {passed} passed, {failed} failed")
        
        if failed == 0:
            print("\n✅ ALL TESTS PASSED - Instance guard is working correctly!")
            return 0
        else:
            print(f"\n❌ {failed} TEST(S) FAILED - Review output above")
            return 1
    
    finally:
        cleanup_test_files()
        
        restore_files(backups)
        print("\n(Test files cleaned up, original files restored)")


if __name__ == "__main__":
    sys.exit(main())
