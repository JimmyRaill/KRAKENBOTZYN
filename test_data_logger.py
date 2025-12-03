#!/usr/bin/env python3
"""
test_data_logger.py - Manual test script for Data Vault logging system

Run this script to verify that all logging functions work correctly
and that files are created in the expected locations.

Usage:
    python test_data_logger.py
"""

import json
from pathlib import Path
from datetime import datetime, timezone


def test_data_logger():
    """Test all Data Vault logging functions."""
    print("=" * 60)
    print("DATA VAULT LOGGING TEST")
    print("=" * 60)
    
    from data_logger import (
        DataLogger,
        log_trade,
        log_decision,
        log_daily_summary,
        log_version,
        log_anomaly,
        log_anomaly_event,
        generate_decision_id,
        read_trades_for_date,
        compute_daily_stats,
        DATA_DIR,
        TRADES_DIR,
        DECISIONS_DIR,
        DAILY_DIR,
        META_DIR,
        ANOMALIES_DIR
    )
    
    print(f"\nData directory: {DATA_DIR.absolute()}")
    
    logger = DataLogger(zin_version="ZIN_TEST_V1")
    print(f"DataLogger initialized with version: {logger.zin_version}")
    
    print("\n--- Testing log_version ---")
    version_result = log_version({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zin_version": "ZIN_TEST_V1",
        "config": {
            "symbols": ["BTC/USD", "ETH/USD"],
            "paper_mode": True,
            "execution_mode": "MARKET_ONLY",
            "risk_per_trade_pct": 0.02
        },
        "comment": "Test version log for Data Vault verification"
    })
    print(f"log_version result: {'SUCCESS' if version_result else 'FAILED'}")
    
    decision_id = generate_decision_id("BTC/USD", "5m")
    print(f"\n--- Testing log_decision (decision_id: {decision_id}) ---")
    decision_result = log_decision({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zin_version": "ZIN_TEST_V1",
        "mode": "paper",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "decision": "NO_TRADE",
        "decision_id": decision_id,
        "indicators": {
            "rsi": 45.2,
            "sma20": 95000.0,
            "sma50": 94500.0,
            "atr": 250.0,
            "adx": 18.5,
            "bb_upper": 96000.0,
            "bb_middle": 95000.0,
            "bb_lower": 94000.0,
            "volume_percentile": 65.0
        },
        "regime": {
            "detected": "RANGE",
            "confidence": 0.7,
            "trend": "SIDEWAYS",
            "volatility": "MED_VOL"
        },
        "htf_context": {
            "trend_15m": "neutral",
            "trend_1h": "neutral",
            "dominant_trend": "neutral",
            "htf_aligned": True
        },
        "signal": {
            "action": "hold",
            "confidence": 0.0,
            "entry_price": 95000.0,
            "stop_loss": None,
            "take_profit": None,
            "reason": "ATR too low for regime filter"
        },
        "filters": {
            "symbol_whitelist_ok": True,
            "regime_filter_ok": False,
            "fee_gate_ok": True
        },
        "volume_usd_24h": 1500000000.0,
        "reason_code": "REGIME_FILTER_BLOCK"
    })
    print(f"log_decision result: {'SUCCESS' if decision_result else 'FAILED'}")
    
    print("\n--- Testing log_trade ---")
    trade_result = log_trade({
        "timestamp_open": datetime.now(timezone.utc).isoformat(),
        "timestamp_close": datetime.now(timezone.utc).isoformat(),
        "zin_version": "ZIN_TEST_V1",
        "mode": "paper",
        "symbol": "ETH/USD",
        "direction": "long",
        "entry_price": 3500.0,
        "exit_price": 3550.0,
        "size": 0.1,
        "pnl_abs": 5.0,
        "pnl_pct": 1.43,
        "max_favorable_excursion_pct": 2.0,
        "max_adverse_excursion_pct": -0.5,
        "reason_code": "TREND_PULLBACK",
        "regime": {
            "trend": "UP_TREND",
            "volatility": "MED_VOL"
        },
        "decision_id": decision_id,
        "fee": 0.52
    })
    print(f"log_trade result: {'SUCCESS' if trade_result else 'FAILED'}")
    
    print("\n--- Testing log_daily_summary ---")
    summary_result = log_daily_summary({
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "zin_version": "ZIN_TEST_V1",
        "mode": "paper",
        "total_trades": 5,
        "win_rate": 60.0,
        "total_pnl_abs": 25.50,
        "total_pnl_pct": 2.5,
        "max_drawdown_pct": 1.2,
        "biggest_win_pct": 1.5,
        "biggest_loss_pct": -0.8,
        "subjective_tag": "TESTING",
        "notes": "Test run for Data Vault verification"
    })
    print(f"log_daily_summary result: {'SUCCESS' if summary_result else 'FAILED'}")
    
    print("\n--- Testing log_anomaly ---")
    anomaly_result = log_anomaly({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zin_version": "ZIN_TEST_V1",
        "type": "TEST_ANOMALY",
        "description": "This is a test anomaly event for verification",
        "context": {
            "symbol": "BTC/USD",
            "test": True
        }
    })
    print(f"log_anomaly result: {'SUCCESS' if anomaly_result else 'FAILED'}")
    
    print("\n--- Testing log_anomaly_event helper ---")
    anomaly_event_result = log_anomaly_event(
        anomaly_type="API_ERROR",
        description="Test API error for verification",
        symbol="SOL/USD",
        error_code=500,
        endpoint="/api/test"
    )
    print(f"log_anomaly_event result: {'SUCCESS' if anomaly_event_result else 'FAILED'}")
    
    print("\n" + "=" * 60)
    print("VERIFYING FILES CREATED")
    print("=" * 60)
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    files_to_check = [
        (META_DIR / "versions.jsonl", "Version history"),
        (DECISIONS_DIR / f"{today}_decisions.jsonl", "Decisions log"),
        (TRADES_DIR / f"{today}_trades.jsonl", "Trades log"),
        (DAILY_DIR / f"{today}_summary.json", "Daily summary"),
        (ANOMALIES_DIR / "anomalies.jsonl", "Anomalies log")
    ]
    
    all_ok = True
    for file_path, description in files_to_check:
        if file_path.exists():
            size = file_path.stat().st_size
            print(f"  [OK] {description}: {file_path} ({size} bytes)")
            
            if file_path.suffix == '.jsonl':
                with open(file_path, 'r') as f:
                    lines = f.readlines()
                    for i, line in enumerate(lines[-3:], 1):
                        try:
                            data = json.loads(line.strip())
                            print(f"       Line {-3+i}: {list(data.keys())[:5]}...")
                        except json.JSONDecodeError as e:
                            print(f"       Line {-3+i}: INVALID JSON - {e}")
                            all_ok = False
            else:
                try:
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                    print(f"       Keys: {list(data.keys())[:5]}...")
                except json.JSONDecodeError as e:
                    print(f"       INVALID JSON - {e}")
                    all_ok = False
        else:
            print(f"  [MISSING] {description}: {file_path}")
            all_ok = False
    
    print("\n--- Testing read_trades_for_date ---")
    trades = read_trades_for_date(today)
    print(f"Trades read for {today}: {len(trades)} records")
    
    print("\n--- Testing compute_daily_stats ---")
    stats = compute_daily_stats(today)
    print(f"Daily stats: {json.dumps(stats, indent=2)}")
    
    print("\n" + "=" * 60)
    if all_ok:
        print("ALL TESTS PASSED!")
    else:
        print("SOME TESTS FAILED - Check output above")
    print("=" * 60)
    
    return all_ok


def test_snapshot_builder():
    """Test snapshot builder functionality."""
    print("\n" + "=" * 60)
    print("SNAPSHOT BUILDER TEST")
    print("=" * 60)
    
    from pathlib import Path
    from snapshot_builder import (
        build_snapshot,
        save_snapshot,
        force_snapshot,
        SNAPSHOTS_DIR
    )
    
    print("\n--- Testing build_snapshot ---")
    snapshot = build_snapshot()
    
    required_keys = ["logged_at", "zin_version", "mode", "snapshot_id", "date",
                     "account", "risk_config", "open_positions", 
                     "performance_summary", "system_health"]
    
    missing_keys = [k for k in required_keys if k not in snapshot]
    if missing_keys:
        print(f"  [FAIL] Missing top-level keys: {missing_keys}")
        return False
    else:
        print(f"  [OK] All required keys present: {list(snapshot.keys())}")
    
    print(f"\n  Snapshot details:")
    print(f"    - logged_at: {snapshot.get('logged_at')}")
    print(f"    - zin_version: {snapshot.get('zin_version')}")
    print(f"    - mode: {snapshot.get('mode')}")
    print(f"    - snapshot_id: {snapshot.get('snapshot_id')}")
    
    print("\n--- Testing account section ---")
    account = snapshot.get("account", {})
    print(f"  - total_equity_usd: {account.get('total_equity_usd')}")
    print(f"  - cash_balance_usd: {account.get('cash_balance_usd')}")
    print(f"  - balances: {len(account.get('balances', {}))} currencies")
    
    print("\n--- Testing risk_config section ---")
    risk = snapshot.get("risk_config", {})
    print(f"  - regime_min_atr_pct: {risk.get('regime_min_atr_pct')}")
    print(f"  - min_confidence: {risk.get('min_confidence')}")
    print(f"  - symbol_whitelist: {risk.get('symbol_whitelist')}")
    
    print("\n--- Testing open_positions section ---")
    positions = snapshot.get("open_positions", [])
    print(f"  - Open positions count: {len(positions)}")
    for pos in positions[:3]:
        print(f"    - {pos.get('symbol')}: {pos.get('side')}, entry={pos.get('entry_price')}")
    
    print("\n--- Testing save_snapshot ---")
    filepath = save_snapshot(snapshot)
    if filepath:
        print(f"  [OK] Snapshot saved to: {filepath}")
        
        snapshot_path = Path(filepath)
        if snapshot_path.exists():
            size = snapshot_path.stat().st_size
            print(f"  [OK] File exists with {size} bytes")
            
            import json
            with open(snapshot_path, 'r') as f:
                loaded = json.load(f)
            print(f"  [OK] Valid JSON with {len(loaded)} keys")
        else:
            print(f"  [FAIL] File not found: {filepath}")
            return False
    else:
        print(f"  [FAIL] save_snapshot returned None")
        return False
    
    print("\n--- Verifying snapshots directory ---")
    if SNAPSHOTS_DIR.exists():
        snapshot_files = list(SNAPSHOTS_DIR.glob("*.json"))
        print(f"  [OK] {len(snapshot_files)} snapshot file(s) in {SNAPSHOTS_DIR}")
        for sf in snapshot_files[-3:]:
            print(f"    - {sf.name}")
    else:
        print(f"  [WARN] Snapshots directory not found: {SNAPSHOTS_DIR}")
    
    print("\n" + "=" * 60)
    print("SNAPSHOT BUILDER TESTS PASSED!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    logger_ok = test_data_logger()
    snapshot_ok = test_snapshot_builder()
    
    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    print(f"  Data Logger: {'PASS' if logger_ok else 'FAIL'}")
    print(f"  Snapshot Builder: {'PASS' if snapshot_ok else 'FAIL'}")
    print("=" * 60)
