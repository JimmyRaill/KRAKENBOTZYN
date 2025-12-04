"""
Discord Notification system for Zin trading bot.
Sends alerts to your Discord channel via webhook for trades, daily/weekly summaries.
"""
import os
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any


def get_discord_webhook_url() -> Optional[str]:
    """Get Discord webhook URL from environment."""
    url = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not url:
        return None
    return url


def get_notification_config() -> dict:
    """Get notification configuration."""
    config_path = Path(__file__).parent / "notification_config.json"
    
    default_config = {
        "enabled": True,
        "notify_on_trades": True,
        "notify_on_startup": True,
        "notify_daily_summary": True,
        "notify_weekly_summary": True,
        "notify_on_errors": True,
        "daily_summary_hour": 18,
        "weekly_summary_day": "Sunday",
        "last_daily_sent": None,
        "last_weekly_sent": None,
        "bot_name": "Zin",
        "bot_avatar": None
    }
    
    if not config_path.exists():
        config_path.write_text(json.dumps(default_config, indent=2))
        return default_config
    
    try:
        return json.loads(config_path.read_text())
    except Exception:
        return default_config


def save_notification_config(config: dict):
    """Save notification configuration."""
    config_path = Path(__file__).parent / "notification_config.json"
    config_path.write_text(json.dumps(config, indent=2))


def send_discord_message(
    content: str = None,
    embed: Dict[str, Any] = None,
    username: str = "Zin",
    force: bool = False
) -> bool:
    """
    Send message to Discord via webhook.
    
    Args:
        content: Plain text message
        embed: Discord embed object for rich formatting
        username: Bot display name
        force: Send even if disabled
    
    Returns:
        True if sent successfully
    """
    config = get_notification_config()
    
    if not force and not config.get("enabled"):
        return False
    
    webhook_url = get_discord_webhook_url()
    if not webhook_url:
        print("[DISCORD] Webhook URL not configured")
        return False
    
    payload = {
        "username": username
    }
    
    if content:
        payload["content"] = content
    
    if embed:
        payload["embeds"] = [embed]
    
    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code in (200, 204):
            print(f"[DISCORD] Message sent successfully")
            return True
        else:
            print(f"[DISCORD-ERROR] Status {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        print(f"[DISCORD-ERROR] {e}")
        return False


def send_notification(message: str, force: bool = True) -> bool:
    """
    Simple wrapper to send a plain text notification to Discord.
    Used by main.py for startup notifications.
    
    Args:
        message: Plain text message to send
        force: Send even if notifications are disabled (default True)
    
    Returns:
        True if sent successfully
    """
    return send_discord_message(content=message, force=force)


def send_startup_test_ping() -> bool:
    """
    Send a test message when bot starts up to verify Discord integration.
    Returns True if test successful.
    """
    config = get_notification_config()
    
    if not config.get("enabled") or not config.get("notify_on_startup"):
        print("[DISCORD-TEST] Discord notifications disabled")
        return False
    
    from exchange_manager import get_mode_str, is_paper_mode
    
    mode = get_mode_str().upper()
    mode_emoji = "🔴" if mode == "LIVE" else "📝"
    
    embed = {
        "title": f"{mode_emoji} Zyn Trading Bot Started",
        "description": f"Trading mode: **{mode}**",
        "color": 0x00ff00,
        "fields": [
            {
                "name": "Status",
                "value": "✅ Online and ready",
                "inline": True
            },
            {
                "name": "Time",
                "value": datetime.now().strftime('%I:%M %p UTC'),
                "inline": True
            }
        ],
        "footer": {
            "text": "Discord notifications are working"
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    result = send_discord_message(embed=embed, force=True)
    
    if result:
        print("[DISCORD-TEST] ✅ Startup test ping sent successfully!")
    else:
        print("[DISCORD-TEST] ❌ Failed to send startup test ping")
    
    return result


def notify_trade(
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    reason: str,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None
):
    """Send Discord notification when Zyn makes a trade.
    
    Args:
        symbol: Trading pair (e.g., "AAVE/USD")
        side: "buy" or "sell"
        quantity: Amount of asset
        price: Entry/exit price
        reason: Signal reason
        stop_loss_price: Mental stop-loss price (optional)
        take_profit_price: Mental take-profit price (optional)
    """
    config = get_notification_config()
    
    if not config.get("notify_on_trades"):
        return
    
    is_buy = side.lower() == "buy"
    color = 0x00ff00 if is_buy else 0xff0000
    emoji = "🟢" if is_buy else "🔴"
    value = quantity * price
    
    fields = [
        {
            "name": "Quantity",
            "value": f"{quantity:.6f}",
            "inline": True
        },
        {
            "name": "Price",
            "value": f"${price:,.2f}",
            "inline": True
        },
        {
            "name": "Value",
            "value": f"${value:,.2f}",
            "inline": True
        }
    ]
    
    if stop_loss_price is not None and take_profit_price is not None:
        fields.append({
            "name": "Stop Loss",
            "value": f"${stop_loss_price:,.2f}",
            "inline": True
        })
        fields.append({
            "name": "Take Profit",
            "value": f"${take_profit_price:,.2f}",
            "inline": True
        })
        risk_reward = abs(take_profit_price - price) / abs(price - stop_loss_price) if abs(price - stop_loss_price) > 0 else 0
        fields.append({
            "name": "R:R",
            "value": f"{risk_reward:.1f}:1",
            "inline": True
        })
    
    fields.append({
        "name": "Reason",
        "value": reason[:200] if reason else "No reason provided",
        "inline": False
    })
    
    embed = {
        "title": f"{emoji} {side.upper()} {symbol}",
        "color": color,
        "fields": fields,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    send_discord_message(embed=embed)


def notify_position_exit(
    symbol: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    pnl_usd: float,
    pnl_pct: float,
    exit_type: str = "TP"
):
    """Send notification when position is closed."""
    config = get_notification_config()
    
    if not config.get("notify_on_trades"):
        return
    
    is_profit = pnl_usd >= 0
    color = 0x00ff00 if is_profit else 0xff0000
    emoji = "💰" if is_profit else "📉"
    sign = "+" if is_profit else ""
    
    embed = {
        "title": f"{emoji} Position Closed - {symbol}",
        "description": f"Exit type: **{exit_type}**",
        "color": color,
        "fields": [
            {
                "name": "Entry",
                "value": f"${entry_price:,.4f}",
                "inline": True
            },
            {
                "name": "Exit",
                "value": f"${exit_price:,.4f}",
                "inline": True
            },
            {
                "name": "Quantity",
                "value": f"{quantity:.6f}",
                "inline": True
            },
            {
                "name": "P&L",
                "value": f"{sign}${pnl_usd:,.2f} ({sign}{pnl_pct:.2f}%)",
                "inline": False
            }
        ],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    send_discord_message(embed=embed)


def notify_error(error_type: str, error_message: str, symbol: str = None):
    """Send notification for errors."""
    config = get_notification_config()
    
    if not config.get("notify_on_errors"):
        return
    
    embed = {
        "title": f"⚠️ Error: {error_type}",
        "description": error_message[:500],
        "color": 0xff6600,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if symbol:
        embed["fields"] = [{"name": "Symbol", "value": symbol, "inline": True}]
    
    send_discord_message(embed=embed)


def notify_daily_summary():
    """Send daily performance summary via Discord and log to Data Vault."""
    config = get_notification_config()
    
    last_sent = config.get("last_daily_sent")
    now = datetime.now()
    target_hour = config.get("daily_summary_hour", 18)
    
    if last_sent:
        last_sent_dt = datetime.fromisoformat(last_sent)
        if last_sent_dt.date() == now.date():
            return
    
    if now.hour < target_hour:
        return
    
    equity = 0
    change_usd = 0
    
    try:
        state_path = Path(__file__).parent / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            equity = state.get("equity_now_usd", 0)
            change_usd = state.get("equity_change_usd", 0)
        
        try:
            from data_logger import log_daily_summary, compute_daily_stats
            from trading_config import get_zin_version
            from exchange_manager import get_mode_str
            
            stats = compute_daily_stats()
            stats["zin_version"] = get_zin_version()
            stats["mode"] = get_mode_str()
            stats["equity_usd"] = equity
            stats["equity_change_usd"] = change_usd
            
            log_daily_summary(stats)
            print(f"[DATA-VAULT] Daily summary logged to data vault")
        except Exception as vault_err:
            print(f"[DATA-VAULT] Daily summary logging error (non-fatal): {vault_err}")
        
        if not config.get("notify_daily_summary"):
            config["last_daily_sent"] = now.isoformat()
            save_notification_config(config)
            return
        
        is_positive = change_usd >= 0
        color = 0x00ff00 if is_positive else 0xff0000
        emoji = "📈" if is_positive else "📉"
        sign = "+" if is_positive else ""
        
        embed = {
            "title": f"{emoji} Daily Summary",
            "color": color,
            "fields": [
                {
                    "name": "Portfolio",
                    "value": f"${equity:,.2f}",
                    "inline": True
                },
                {
                    "name": "Today's P&L",
                    "value": f"{sign}${change_usd:,.2f}",
                    "inline": True
                }
            ],
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if send_discord_message(embed=embed):
            config["last_daily_sent"] = now.isoformat()
            save_notification_config(config)
    
    except Exception as e:
        print(f"[DAILY-SUMMARY-ERROR] {e}")


def notify_weekly_summary():
    """Send weekly performance summary via Discord."""
    config = get_notification_config()
    
    if not config.get("notify_weekly_summary"):
        return
    
    last_sent = config.get("last_weekly_sent")
    now = datetime.now()
    target_day = config.get("weekly_summary_day", "Sunday")
    
    if now.strftime("%A") != target_day:
        return
    
    if last_sent:
        last_sent_dt = datetime.fromisoformat(last_sent)
        days_since = (now - last_sent_dt).days
        if days_since < 7:
            return
    
    try:
        state_path = Path(__file__).parent / "state.json"
        if not state_path.exists():
            return
        
        state = json.loads(state_path.read_text())
        equity = state.get("equity_now_usd", 0)
        
        embed = {
            "title": "🚀 Weekly Summary",
            "color": 0x5865F2,
            "fields": [
                {
                    "name": "Portfolio",
                    "value": f"${equity:,.2f}",
                    "inline": True
                }
            ],
            "footer": {
                "text": "See you next week!"
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if send_discord_message(embed=embed):
            config["last_weekly_sent"] = now.isoformat()
            save_notification_config(config)
    
    except Exception as e:
        print(f"[WEEKLY-SUMMARY-ERROR] {e}")


def check_summaries():
    """Check if it's time to send daily/weekly summaries."""
    notify_daily_summary()
    notify_weekly_summary()
