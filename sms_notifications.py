"""
SMS Notification system using Twilio for Zyn trading bot.
Sends text messages to your phone for trades, daily/weekly summaries.
"""
import os
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Tuple

def get_twilio_credentials() -> Tuple[Optional[object], Optional[str]]:
    """Get Twilio client and phone number from Replit connection."""
    try:
        hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
        x_replit_token = None
        
        if os.environ.get('REPL_IDENTITY'):
            x_replit_token = 'repl ' + os.environ.get('REPL_IDENTITY')
        elif os.environ.get('WEB_REPL_RENEWAL'):
            x_replit_token = 'depl ' + os.environ.get('WEB_REPL_RENEWAL')
        
        if not x_replit_token or not hostname:
            return None, None
        
        response = requests.get(
            f'https://{hostname}/api/v2/connection?include_secrets=true&connector_names=twilio',
            headers={
                'Accept': 'application/json',
                'X_REPLIT_TOKEN': x_replit_token
            }
        )
        
        data = response.json()
        items = data.get('items', [])
        if not items:
            return None, None
            
        connection_settings = items[0]
        settings = connection_settings.get('settings', {})
        
        account_sid = settings.get('account_sid')
        api_key = settings.get('api_key')
        api_key_secret = settings.get('api_key_secret')
        phone_number = settings.get('phone_number')
        
        if not all([account_sid, api_key, api_key_secret, phone_number]):
            return None, None
        
        from twilio.rest import Client
        client = Client(api_key, api_key_secret, account_sid)
        
        return client, phone_number
    except Exception as e:
        print(f"[TWILIO-INIT-ERROR] {e}")
        return None, None

def get_sms_config() -> dict:
    """Get SMS notification configuration."""
    config_path = Path(__file__).parent / "sms_config.json"
    
    default_config = {
        "enabled": False,
        "your_phone_number": "",  # User's phone number (format: +1234567890)
        "notify_on_trades": True,
        "notify_daily_summary": True,
        "notify_weekly_summary": True,
        "daily_summary_hour": 18,  # 6 PM
        "weekly_summary_day": "Sunday",
        "last_daily_sent": None,
        "last_weekly_sent": None,
        "quiet_hours_start": 22,  # 10 PM
        "quiet_hours_end": 8  # 8 AM
    }
    
    if not config_path.exists():
        config_path.write_text(json.dumps(default_config, indent=2))
        return default_config
    
    try:
        return json.loads(config_path.read_text())
    except Exception:
        return default_config

def save_sms_config(config: dict):
    """Save SMS configuration."""
    config_path = Path(__file__).parent / "sms_config.json"
    config_path.write_text(json.dumps(config, indent=2))

def is_quiet_hours() -> bool:
    """Check if current time is in quiet hours (don't send notifications)."""
    config = get_sms_config()
    current_hour = datetime.now().hour
    
    start = config.get("quiet_hours_start", 22)
    end = config.get("quiet_hours_end", 8)
    
    if start > end:  # Overnight quiet hours (e.g., 22:00 - 08:00)
        return current_hour >= start or current_hour < end
    else:
        return start <= current_hour < end

def send_sms(message: str, force: bool = False) -> bool:
    """
    Send SMS via Twilio.
    
    Args:
        message: Text message to send
        force: Send even during quiet hours
    
    Returns:
        True if sent successfully
    """
    config = get_sms_config()
    
    # Check if enabled
    if not config.get("enabled"):
        return False
    
    # Check if phone number configured
    your_phone = config.get("your_phone_number", "").strip()
    if not your_phone:
        print("[SMS] Your phone number not configured. Set it in sms_config.json")
        return False
    
    # Check quiet hours
    if not force and is_quiet_hours():
        print("[SMS] Skipping - quiet hours")
        return False
    
    # Get Twilio client
    client, from_number = get_twilio_credentials()
    if not client or not from_number:
        print("[SMS] Twilio not configured")
        return False
    
    try:
        message_obj = client.messages.create(
            body=message,
            from_=from_number,
            to=your_phone
        )
        print(f"[SMS-SENT] To {your_phone}, SID: {message_obj.sid}")
        return True
    except Exception as e:
        print(f"[SMS-ERROR] {e}")
        return False

def notify_trade(symbol: str, side: str, quantity: float, price: float, reason: str):
    """Send SMS notification when Zyn makes a trade."""
    config = get_sms_config()
    
    if not config.get("notify_on_trades"):
        return
    
    emoji = "🟢 BUY" if side.lower() == "buy" else "🔴 SELL"
    value = quantity * price
    
    message = f"Zyn Trade Alert\n"
    message += f"{emoji} {symbol}\n"
    message += f"Amount: {quantity:.4f} @ ${price:.2f}\n"
    message += f"Value: ${value:.2f}\n"
    message += f"Why: {reason}"
    
    send_sms(message)

def notify_daily_summary():
    """Send daily performance summary via SMS."""
    config = get_sms_config()
    
    if not config.get("notify_daily_summary"):
        return
    
    # Check if we already sent today
    last_sent = config.get("last_daily_sent")
    now = datetime.now()
    target_hour = config.get("daily_summary_hour", 18)
    
    if last_sent:
        last_sent_dt = datetime.fromisoformat(last_sent)
        if last_sent_dt.date() == now.date():
            return  # Already sent today
    
    # Only send at target hour or later
    if now.hour < target_hour:
        return
    
    # Get performance data
    try:
        from telemetry_db import get_db
        
        state_path = Path(__file__).parent / "state.json"
        if not state_path.exists():
            return
        
        state = json.loads(state_path.read_text())
        equity = state.get("equity_now_usd", 0)
        change_usd = state.get("equity_change_usd", 0)
        change_pct = state.get("equity_change_pct", 0)
        
        # Count today's trades
        with get_db() as conn:
            cursor = conn.cursor()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            cursor.execute(
                "SELECT COUNT(*) as count FROM trades WHERE timestamp >= ?",
                (today_start.isoformat(),)
            )
            trades_today = dict(cursor.fetchone())["count"]
        
        # Format message
        emoji = "📈" if change_usd >= 0 else "📉"
        sign = "+" if change_usd >= 0 else ""
        
        message = f"Zyn Daily Summary {emoji}\n"
        message += f"Portfolio: ${equity:.2f}\n"
        message += f"Today: {sign}${change_usd:.2f} ({sign}{change_pct:.2f}%)\n"
        message += f"Trades: {trades_today}"
        
        if send_sms(message):
            # Update last sent time
            config["last_daily_sent"] = now.isoformat()
            save_sms_config(config)
    
    except Exception as e:
        print(f"[DAILY-SUMMARY-ERROR] {e}")

def notify_weekly_summary():
    """Send weekly performance summary via SMS."""
    config = get_sms_config()
    
    if not config.get("notify_weekly_summary"):
        return
    
    # Check if we already sent this week
    last_sent = config.get("last_weekly_sent")
    now = datetime.now()
    target_day = config.get("weekly_summary_day", "Sunday")
    
    if now.strftime("%A") != target_day:
        return  # Not the right day
    
    if last_sent:
        last_sent_dt = datetime.fromisoformat(last_sent)
        days_since = (now - last_sent_dt).days
        if days_since < 7:
            return  # Already sent this week
    
    # Get performance data
    try:
        from trade_analyzer import get_performance_summary
        
        state_path = Path(__file__).parent / "state.json"
        if not state_path.exists():
            return
        
        state = json.loads(state_path.read_text())
        equity = state.get("equity_now_usd", 0)
        
        perf = get_performance_summary(days=7)
        total_trades = perf.get("total_trades", 0)
        win_rate = perf.get("win_rate", 0)
        total_return_pct = perf.get("total_return_pct", 0)
        total_return_usd = equity * (total_return_pct / 100)
        
        # Format message
        emoji = "🚀" if total_return_usd >= 0 else "⚠️"
        sign = "+" if total_return_usd >= 0 else ""
        
        message = f"Zyn Weekly Summary {emoji}\n"
        message += f"Portfolio: ${equity:.2f}\n"
        message += f"This Week: {sign}${total_return_usd:.2f} ({sign}{total_return_pct:.2f}%)\n"
        message += f"Trades: {total_trades}\n"
        message += f"Win Rate: {win_rate*100:.1f}%"
        
        if send_sms(message):
            # Update last sent time
            config["last_weekly_sent"] = now.isoformat()
            save_sms_config(config)
    
    except Exception as e:
        print(f"[WEEKLY-SUMMARY-ERROR] {e}")

def check_summaries():
    """Check if it's time to send daily/weekly summaries."""
    notify_daily_summary()
    notify_weekly_summary()
