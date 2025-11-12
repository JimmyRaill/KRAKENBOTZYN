import os


def daily_target_pct():
    r = float(os.getenv("TARGET_ANNUAL_RETURN_PCT", "15"))
    return (1 + r / 100.0)**(1 / 365.0) - 1


def aggression_factor(realized_pnl_today: float, equity_usd: float) -> float:
    """>1 = a bit more size, <1 = a bit less. Tight clamps to avoid whiplash."""
    if equity_usd <= 0: return 1.0
    tgt_usd = daily_target_pct() * equity_usd
    gap = (tgt_usd - realized_pnl_today) / max(1.0, abs(tgt_usd))
    return max(0.8, min(1.2, 1.0 + 0.3 * gap))
