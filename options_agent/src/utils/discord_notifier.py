"""Discord webhook notifications for trade events. No external packages required."""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

REASON_LABELS = {
    "stop": "STOPPED OUT",
    "decay_target": "TARGET HIT (decay)",
    "target": "TARGET HIT",
    "theta_exit": "THETA EXIT",
    "stagnation": "STAGNATION EXIT",
    "time_stop": "TIME STOP",
    "gainz": "GAINZ EXIT",
}


class DiscordNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.enabled = bool(webhook_url)
        if not self.enabled:
            logger.info("Discord notifier disabled — no DISCORD_WEBHOOK_URL configured")

    def _post(self, payload: dict) -> None:
        if not self.enabled:
            return
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "OptionsAgent/1.0",
            },
        )
        for attempt in range(2):
            try:
                urllib.request.urlopen(req, timeout=10)
                logger.info("Discord message sent")
                return
            except urllib.error.HTTPError as e:
                logger.error("Discord webhook error %s: %s", e.code, e.read().decode()[:200])
                return
            except (ConnectionError, OSError) as e:
                if attempt == 0:
                    logger.warning("Discord connection error, retrying: %s", e)
                else:
                    logger.error("Discord send failed after retry: %s", e)
            except Exception as e:
                logger.error("Discord send failed: %s", e)
                return

    def notify_trade_entry(self, trigger: dict) -> None:
        direction = trigger.get("direction", "?")
        dir_label = "CALL" if "call" in direction else "PUT"
        symbol = trigger.get("symbol", "?")
        entry = trigger.get("entry", 0)
        stop = trigger.get("stop", 0)
        target = trigger.get("target", 0)
        quality = trigger.get("quality", "?")
        explosion = trigger.get("explosion", "?")
        chop = trigger.get("chop", "?")
        trade_mode = trigger.get("trade_mode", "?")
        occ = trigger.get("occ_symbol", "")
        contracts = trigger.get("num_contracts", "?")
        premium = trigger.get("option_premium", 0)
        delta = trigger.get("option_delta", 0)
        strike = trigger.get("option_strike", 0)
        now = datetime.now(ET).strftime("%I:%M %p ET")

        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = f"{reward / risk:.1f}" if risk > 0 else "?"

        color = 0x2e7d32 if "call" in direction else 0xc62828
        emoji = "\U0001f4c8" if "call" in direction else "\U0001f4c9"

        fields = [
            {"name": "Entry", "value": f"${entry:.2f}", "inline": True},
            {"name": "Stop", "value": f"${stop:.2f}", "inline": True},
            {"name": "Target", "value": f"${target:.2f}", "inline": True},
            {"name": "R:R", "value": f"1:{rr}", "inline": True},
            {"name": "Quality", "value": f"{quality}/10", "inline": True},
            {"name": "Explosion", "value": str(explosion), "inline": True},
            {"name": "Chop", "value": str(chop), "inline": True},
            {"name": "Mode", "value": trade_mode, "inline": True},
        ]

        if trade_mode == "0dte_option" and occ:
            fields.extend([
                {"name": "Contract", "value": occ, "inline": False},
                {"name": "Strike", "value": f"${strike:.2f}", "inline": True},
                {"name": "Delta", "value": f"{delta:.2f}", "inline": True},
                {"name": "Premium", "value": f"${premium:.2f}", "inline": True},
                {"name": "Contracts", "value": str(contracts), "inline": True},
            ])

        self._post({
            "embeds": [{
                "title": f"{emoji} {dir_label} {symbol} — TRADE OPENED",
                "color": color,
                "fields": fields,
                "footer": {"text": now},
            }]
        })

    def notify_trade_exit(self, trigger: dict, exit_reason: str,
                          exit_price: float) -> None:
        direction = trigger.get("direction", "?")
        dir_label = "CALL" if "call" in direction else "PUT"
        symbol = trigger.get("symbol", "?")
        entry = trigger.get("entry", 0)
        stop = trigger.get("stop", 0)
        occ = trigger.get("occ_symbol", "")
        now = datetime.now(ET).strftime("%I:%M %p ET")

        risk = abs(entry - stop)
        if "call" in direction:
            pnl_pts = exit_price - entry
        else:
            pnl_pts = entry - exit_price
        pnl_r = f"{pnl_pts / risk:.2f}R" if risk > 0 else "?"
        win = pnl_pts >= 0

        reason_display = REASON_LABELS.get(exit_reason, exit_reason.upper())
        color = 0x2e7d32 if win else 0xc62828
        emoji = "✅" if win else "❌"
        sign = "+" if pnl_pts >= 0 else ""

        fields = [
            {"name": "Symbol", "value": f"{dir_label} {symbol}", "inline": True},
            {"name": "Entry", "value": f"${entry:.2f}", "inline": True},
            {"name": "Exit", "value": f"${exit_price:.2f}", "inline": True},
            {"name": "P&L", "value": f"{sign}{pnl_pts:.2f} pts ({pnl_r})", "inline": True},
            {"name": "Reason", "value": reason_display, "inline": True},
        ]
        if occ:
            fields.append({"name": "Contract", "value": occ, "inline": False})

        self._post({
            "embeds": [{
                "title": f"{emoji} {reason_display} — {dir_label} {symbol}",
                "color": color,
                "fields": fields,
                "footer": {"text": now},
            }]
        })
