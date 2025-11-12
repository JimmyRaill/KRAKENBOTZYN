# notifications.py - Multi-channel notification system for trade alerts
from __future__ import annotations

import os
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AlertPriority(Enum):
    """Alert priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertType(Enum):
    """Types of trading alerts."""
    TRADE_EXECUTED = "trade_executed"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    STOP_LOSS_HIT = "stop_loss_hit"
    TAKE_PROFIT_HIT = "take_profit_hit"
    DAILY_SUMMARY = "daily_summary"
    ERROR = "error"
    STRATEGY_SWITCH = "strategy_switch"
    REGIME_CHANGE = "regime_change"


@dataclass
class Alert:
    """Trading alert message."""
    type: AlertType
    priority: AlertPriority
    title: str
    message: str
    metadata: Dict[str, Any]
    timestamp: str = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()
    
    def to_telegram_message(self) -> str:
        """Format alert for Telegram."""
        emoji_map = {
            AlertType.TRADE_EXECUTED: "💰",
            AlertType.POSITION_OPENED: "📈",
            AlertType.POSITION_CLOSED: "📉",
            AlertType.STOP_LOSS_HIT: "🛑",
            AlertType.TAKE_PROFIT_HIT: "🎯",
            AlertType.DAILY_SUMMARY: "📊",
            AlertType.ERROR: "⚠️",
            AlertType.STRATEGY_SWITCH: "🔄",
            AlertType.REGIME_CHANGE: "📡"
        }
        
        emoji = emoji_map.get(self.type, "ℹ️")
        priority_marker = "❗" * (list(AlertPriority).index(self.priority) + 1)
        
        msg = f"{emoji} {priority_marker} *{self.title}*\n\n"
        msg += f"{self.message}\n\n"
        
        if self.metadata:
            msg += "_Details:_\n"
            for key, value in self.metadata.items():
                msg += f"• {key}: `{value}`\n"
        
        msg += f"\n🕐 {self.timestamp}"
        
        return msg
    
    def to_discord_embed(self) -> Dict[str, Any]:
        """Format alert as Discord embed."""
        color_map = {
            AlertPriority.LOW: 0x3b82f6,  # Blue
            AlertPriority.MEDIUM: 0xf59e0b,  # Orange
            AlertPriority.HIGH: 0xef4444,  # Red
            AlertPriority.CRITICAL: 0x991b1b  # Dark red
        }
        
        return {
            "title": self.title,
            "description": self.message,
            "color": color_map.get(self.priority, 0x3b82f6),
            "fields": [
                {"name": key, "value": str(value), "inline": True}
                for key, value in self.metadata.items()
            ],
            "footer": {"text": f"{self.type.value} • {self.timestamp}"}
        }


class NotificationManager:
    """Manages multi-channel notifications."""
    
    def __init__(self):
        self.telegram_enabled = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
        self.discord_enabled = bool(os.getenv("DISCORD_WEBHOOK_URL"))
        self.email_enabled = bool(os.getenv("SMTP_SERVER"))
        
        # Alert queue for batch sending
        self.alert_queue: list[Alert] = []
    
    async def send_alert(self, alert: Alert) -> Dict[str, bool]:
        """
        Send alert to all enabled channels.
        
        Returns:
            Dict with success status for each channel
        """
        results = {}
        
        if self.telegram_enabled:
            results["telegram"] = await self._send_telegram(alert)
        
        if self.discord_enabled:
            results["discord"] = await self._send_discord(alert)
        
        if self.email_enabled and alert.priority in [AlertPriority.HIGH, AlertPriority.CRITICAL]:
            results["email"] = await self._send_email(alert)
        
        return results
    
    async def _send_telegram(self, alert: Alert) -> bool:
        """Send alert to Telegram."""
        try:
            import aiohttp
            
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": alert.to_telegram_message(),
                "parse_mode": "Markdown"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as response:
                    return response.status == 200
        except Exception as e:
            print(f"[TELEGRAM-ERROR] {e}")
            return False
    
    async def _send_discord(self, alert: Alert) -> bool:
        """Send alert to Discord webhook."""
        try:
            import aiohttp
            
            webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
            
            data = {
                "embeds": [alert.to_discord_embed()]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data) as response:
                    return response.status in [200, 204]
        except Exception as e:
            print(f"[DISCORD-ERROR] {e}")
            return False
    
    async def _send_email(self, alert: Alert) -> bool:
        """Send alert via email (for high-priority alerts)."""
        try:
            import aiosmtplib
            from email.message import EmailMessage
            
            smtp_server = os.getenv("SMTP_SERVER")
            smtp_port = int(os.getenv("SMTP_PORT", "587"))
            smtp_user = os.getenv("SMTP_USER")
            smtp_pass = os.getenv("SMTP_PASSWORD")
            email_to = os.getenv("EMAIL_RECIPIENT")
            
            msg = EmailMessage()
            msg["Subject"] = f"[KrakenBot] {alert.title}"
            msg["From"] = smtp_user
            msg["To"] = email_to
            
            body = f"{alert.message}\n\n"
            body += "Details:\n"
            for key, value in alert.metadata.items():
                body += f"{key}: {value}\n"
            body += f"\nTimestamp: {alert.timestamp}"
            
            msg.set_content(body)
            
            await aiosmtplib.send(
                msg,
                hostname=smtp_server,
                port=smtp_port,
                username=smtp_user,
                password=smtp_pass,
                use_tls=True
            )
            
            return True
        except Exception as e:
            print(f"[EMAIL-ERROR] {e}")
            return False
    
    def queue_alert(self, alert: Alert):
        """Add alert to queue for batch sending."""
        self.alert_queue.append(alert)
    
    async def flush_queue(self) -> Dict[str, int]:
        """Send all queued alerts and return stats."""
        if not self.alert_queue:
            return {"sent": 0, "failed": 0}
        
        sent = 0
        failed = 0
        
        for alert in self.alert_queue:
            results = await self.send_alert(alert)
            if any(results.values()):
                sent += 1
            else:
                failed += 1
        
        self.alert_queue.clear()
        
        return {"sent": sent, "failed": failed}


# Synchronous wrapper for use in non-async code
def send_alert_sync(alert: Alert) -> None:
    """Synchronous wrapper to send alert (for use in autopilot)."""
    try:
        import asyncio
        
        manager = NotificationManager()
        
        # Run async function in sync context
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        loop.run_until_complete(manager.send_alert(alert))
    except Exception as e:
        print(f"[NOTIFICATION-ERROR] {e}")


# Helper functions for common alerts
def trade_executed_alert(symbol: str, side: str, size: float, price: float, reason: str) -> Alert:
    """Create a trade executed alert."""
    return Alert(
        type=AlertType.TRADE_EXECUTED,
        priority=AlertPriority.MEDIUM,
        title=f"{side.upper()} {symbol}",
        message=f"Executed {side} order for {symbol}",
        metadata={
            "Symbol": symbol,
            "Side": side,
            "Size": f"{size:.4f}",
            "Price": f"${price:.2f}",
            "Reason": reason
        }
    )


def stop_loss_alert(symbol: str, entry_price: float, exit_price: float, loss_usd: float) -> Alert:
    """Create a stop-loss hit alert."""
    return Alert(
        type=AlertType.STOP_LOSS_HIT,
        priority=AlertPriority.HIGH,
        title=f"Stop Loss Hit: {symbol}",
        message=f"Position closed at stop-loss",
        metadata={
            "Symbol": symbol,
            "Entry": f"${entry_price:.2f}",
            "Exit": f"${exit_price:.2f}",
            "Loss": f"-${abs(loss_usd):.2f}"
        }
    )


def daily_summary_alert(
    total_trades: int,
    win_rate: float,
    pnl_usd: float,
    equity: float
) -> Alert:
    """Create a daily summary alert."""
    return Alert(
        type=AlertType.DAILY_SUMMARY,
        priority=AlertPriority.LOW,
        title="Daily Trading Summary",
        message=f"Today's performance report",
        metadata={
            "Trades": total_trades,
            "Win Rate": f"{win_rate*100:.1f}%",
            "P&L": f"${pnl_usd:+.2f}",
            "Equity": f"${equity:.2f}"
        }
    )


def strategy_switch_alert(old_strategy: str, new_strategy: str, regime: str) -> Alert:
    """Create a strategy switch alert."""
    return Alert(
        type=AlertType.STRATEGY_SWITCH,
        priority=AlertPriority.MEDIUM,
        title="Strategy Switched",
        message=f"Switching from {old_strategy} to {new_strategy}",
        metadata={
            "Old Strategy": old_strategy,
            "New Strategy": new_strategy,
            "Market Regime": regime
        }
    )
