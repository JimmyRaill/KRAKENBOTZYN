"""
Time Context Module - Provides time/date awareness and temporal intelligence.
Helps the bot understand when to trade and recognize time-based patterns.
"""
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List


def get_current_datetime() -> datetime:
    """Get current datetime in UTC."""
    return datetime.now(timezone.utc)


def get_current_timestamp() -> float:
    """Get current Unix timestamp."""
    return time.time()


def format_datetime(dt: Optional[datetime] = None) -> str:
    """Format datetime for display."""
    if dt is None:
        dt = get_current_datetime()
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def get_time_info() -> Dict[str, Any]:
    """Get comprehensive time information."""
    now = get_current_datetime()
    
    return {
        "timestamp": time.time(),
        "datetime": format_datetime(now),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "year": now.year,
        "month": now.month,
        "month_name": now.strftime("%B"),
        "day": now.day,
        "day_of_week": now.strftime("%A"),
        "day_of_week_num": now.weekday(),
        "hour": now.hour,
        "minute": now.minute,
        "is_weekend": now.weekday() >= 5,
        "is_weekday": now.weekday() < 5,
        "week_of_year": now.isocalendar()[1],
        "quarter": (now.month - 1) // 3 + 1,
    }


def is_market_hours(market: str = "crypto") -> Dict[str, Any]:
    """
    Check if markets are currently open.
    Note: Crypto markets are 24/7, but traditional markets have hours.
    """
    now = get_current_datetime()
    
    if market.lower() == "crypto":
        return {
            "is_open": True,
            "market": "crypto",
            "note": "Crypto markets are open 24/7"
        }
    
    # Traditional stock market hours (NYSE/NASDAQ) - 9:30 AM - 4:00 PM ET, Mon-Fri
    # This is a simplified check - would need timezone conversion for accuracy
    weekday = now.weekday()
    hour = now.hour
    
    if market.lower() in ["stock", "stocks", "nyse", "nasdaq"]:
        is_open = (weekday < 5) and (14 <= hour < 21)  # Approximate UTC hours
        return {
            "is_open": is_open,
            "market": market,
            "note": "NYSE/NASDAQ open Mon-Fri 9:30 AM - 4:00 PM ET"
        }
    
    return {
        "is_open": None,
        "market": market,
        "note": "Unknown market type"
    }


def get_time_of_day() -> str:
    """Get descriptive time of day."""
    hour = get_current_datetime().hour
    
    if 0 <= hour < 6:
        return "late_night"
    elif 6 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    else:
        return "night"


def time_since(timestamp: float) -> str:
    """Get human-readable time difference."""
    delta = time.time() - timestamp
    
    if delta < 60:
        return f"{int(delta)} seconds ago"
    elif delta < 3600:
        minutes = int(delta / 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif delta < 86400:
        hours = int(delta / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = int(delta / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"


def get_temporal_features() -> Dict[str, Any]:
    """
    Get temporal features useful for pattern recognition.
    These can be used to learn time-based trading patterns.
    """
    info = get_time_info()
    now = get_current_datetime()
    
    # Cyclical encoding for time features (useful for ML)
    hour_sin = round(2 * 3.14159 * info["hour"] / 24, 4)
    day_sin = round(2 * 3.14159 * info["day_of_week_num"] / 7, 4)
    month_sin = round(2 * 3.14159 * info["month"] / 12, 4)
    
    return {
        **info,
        "time_of_day": get_time_of_day(),
        "is_month_start": info["day"] <= 7,
        "is_month_end": info["day"] >= 22,
        "is_quarter_start": info["month"] in [1, 4, 7, 10] and info["day"] <= 7,
        "is_quarter_end": info["month"] in [3, 6, 9, 12] and info["day"] >= 22,
        "hour_sin": hour_sin,
        "day_of_week_sin": day_sin,
        "month_sin": month_sin,
    }


def get_context_summary() -> str:
    """Get a human-readable time context summary."""
    info = get_time_info()
    market = is_market_hours("crypto")
    
    lines = [
        f"Current Time: {info['datetime']}",
        f"Day: {info['day_of_week']}, {info['month_name']} {info['day']}, {info['year']}",
        f"Time of Day: {get_time_of_day().replace('_', ' ').title()}",
        f"Market Status: {'Open' if market['is_open'] else 'Closed'} (Crypto 24/7)",
    ]
    
    if info['is_weekend']:
        lines.append("Note: Weekend - traditional markets closed")
    
    return "\n".join(lines)


# Example usage for bot prompts
def get_prompt_context() -> str:
    """Get concise time context for LLM prompts."""
    info = get_time_info()
    return (
        f"Current time: {info['datetime']} "
        f"({info['day_of_week']}, {get_time_of_day()})"
    )
