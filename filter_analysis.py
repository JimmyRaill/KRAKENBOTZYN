#!/usr/bin/env python3
"""
Filter Analysis Tool
Analyzes evaluation logs to determine how many trades were blocked by specific filters.
"""

import sys
sys.path.insert(0, '/home/runner/workspace')

from evaluation_log import get_last_evaluations
from datetime import datetime, timedelta
from typing import Dict, List, Any
import re

def analyze_filter_blocking(hours: int = 24) -> Dict[str, Any]:
    """
    Analyze evaluations to see which filters are blocking trades.
    
    Returns statistics on:
    - Total evaluations
    - RANGE regime evaluations
    - Blocked by BB position
    - Blocked by RSI
    - Blocked by both
    - Would pass in aggressive mode
    """
    # Get all evaluations
    all_evals = get_last_evaluations(limit=10000)
    
    # Filter to last N hours
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    recent_evals = [
        e for e in all_evals 
        if datetime.fromisoformat(e['timestamp_utc'].replace('Z', '+00:00')) > cutoff_time
    ]
    
    # Statistics
    stats = {
        'total_evaluations': len(recent_evals),
        'range_regime': 0,
        'range_blocked_by_bb': 0,
        'range_blocked_by_rsi': 0,
        'range_blocked_by_both': 0,
        'range_would_pass_aggressive': 0,
        'no_trade_regime': 0,
        'other_regimes': 0,
        'by_symbol': {}
    }
    
    for ev in recent_evals:
        symbol = ev.get('symbol', 'UNKNOWN')
        regime = ev.get('regime', '')
        reason = ev.get('reason', '')
        rsi = ev.get('rsi')
        decision = ev.get('decision', '')
        
        # Initialize symbol stats
        if symbol not in stats['by_symbol']:
            stats['by_symbol'][symbol] = {
                'total': 0,
                'range': 0,
                'range_blocked': 0,
                'no_trade': 0
            }
        
        stats['by_symbol'][symbol]['total'] += 1
        
        # Analyze by regime
        if regime == 'range':
            stats['range_regime'] += 1
            stats['by_symbol'][symbol]['range'] += 1
            
            # Check if blocked (decision is HOLD and reason mentions setup)
            if decision == 'HOLD' and 'no setup' in reason.lower():
                stats['by_symbol'][symbol]['range_blocked'] += 1
                
                # Parse BB position and RSI from reason
                # Example: "RANGE but no setup (price at 59% of band, RSI=56.8)"
                bb_match = re.search(r'price at (\d+)% of band', reason)
                rsi_match = re.search(r'RSI=([0-9.]+)', reason)
                
                bb_position = int(bb_match.group(1)) if bb_match else None
                rsi_value = float(rsi_match.group(1)) if rsi_match else rsi
                
                # Check what blocked it
                blocked_by_bb = bb_position is not None and bb_position > 40
                blocked_by_rsi = rsi_value is not None and rsi_value >= 45
                
                if blocked_by_bb and blocked_by_rsi:
                    stats['range_blocked_by_both'] += 1
                elif blocked_by_bb:
                    stats['range_blocked_by_bb'] += 1
                elif blocked_by_rsi:
                    stats['range_blocked_by_rsi'] += 1
                
                # Would it pass in aggressive mode? (BB ≤50, RSI <55)
                would_pass_aggressive = (
                    (bb_position is None or bb_position <= 50) and
                    (rsi_value is None or rsi_value < 55)
                )
                if would_pass_aggressive:
                    stats['range_would_pass_aggressive'] += 1
        
        elif regime == 'no_trade':
            stats['no_trade_regime'] += 1
            stats['by_symbol'][symbol]['no_trade'] += 1
        else:
            stats['other_regimes'] += 1
    
    return stats


def print_analysis(hours: int = 24):
    """Print human-readable analysis"""
    stats = analyze_filter_blocking(hours)
    
    print(f"=== FILTER ANALYSIS (Last {hours} hours) ===\n")
    print(f"Total Evaluations: {stats['total_evaluations']}")
    print(f"  RANGE regime: {stats['range_regime']}")
    print(f"  NO_TRADE regime: {stats['no_trade_regime']}")
    print(f"  Other regimes: {stats['other_regimes']}")
    print()
    
    print("RANGE Regime Breakdown:")
    print(f"  Total RANGE evaluations: {stats['range_regime']}")
    print(f"  Blocked by BB position (>40%): {stats['range_blocked_by_bb']}")
    print(f"  Blocked by RSI (≥45): {stats['range_blocked_by_rsi']}")
    print(f"  Blocked by BOTH: {stats['range_blocked_by_both']}")
    print()
    
    total_blocked = stats['range_blocked_by_bb'] + stats['range_blocked_by_rsi'] + stats['range_blocked_by_both']
    if total_blocked > 0:
        bb_pct = (stats['range_blocked_by_bb'] / total_blocked) * 100
        rsi_pct = (stats['range_blocked_by_rsi'] / total_blocked) * 100
        both_pct = (stats['range_blocked_by_both'] / total_blocked) * 100
        
        print(f"Of {total_blocked} blocked RANGE opportunities:")
        print(f"  {bb_pct:.1f}% blocked ONLY by BB position")
        print(f"  {rsi_pct:.1f}% blocked ONLY by RSI")
        print(f"  {both_pct:.1f}% blocked by BOTH")
    print()
    
    print("AGGRESSIVE MODE Impact:")
    print(f"  Current filters block: {total_blocked} RANGE opportunities")
    print(f"  Aggressive mode would allow: {stats['range_would_pass_aggressive']} of those")
    if total_blocked > 0:
        improvement = (stats['range_would_pass_aggressive'] / total_blocked) * 100
        print(f"  Improvement: +{improvement:.1f}% more trades")
    print()
    
    print("By Symbol:")
    for symbol, counts in sorted(stats['by_symbol'].items()):
        print(f"  {symbol}:")
        print(f"    Total evals: {counts['total']}")
        print(f"    RANGE: {counts['range']} ({counts['range_blocked']} blocked)")
        print(f"    NO_TRADE: {counts['no_trade']}")
    print()
    
    return stats


if __name__ == "__main__":
    import sys
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    print_analysis(hours)
