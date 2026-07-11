"""Discord webhook notifications for trade events. No external packages required."""

from __future__ import annotations

import json
import logging
import os
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
    "closed_externally": "CLOSED (broker)",
}


class DiscordNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.enabled = bool(webhook_url)
        # Optional label shown as the Discord sender name, so messages from a
        # tagged agent (e.g. the shadow new-golden agents) are distinguishable
        # from the live/paper agents in the SAME channel. Empty = default sender.
        self.tag = os.environ.get("DISCORD_TAG", "").strip()
        if not self.enabled:
            logger.info("Discord notifier disabled — no DISCORD_WEBHOOK_URL configured")

    def _post(self, payload: dict) -> None:
        if not self.enabled:
            return
        if self.tag:
            payload.setdefault("username", self.tag)
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

    def notify_status_update(self, trades: list[dict], open_trades: list[dict],
                             total_scans: int, scan_triggers: int,
                             scan_rejects: int) -> None:
        now = datetime.now(ET)
        now_str = now.strftime("%I:%M %p ET")

        closed = [t for t in trades if t.get("closed")]
        total_dollar_pnl = 0.0
        has_fills = False
        trade_lines = []
        for t in closed:
            direction = t.get("direction", "?")
            dir_label = "CALL" if "call" in direction else "PUT"
            symbol = t.get("symbol", "?")
            actual_entry = t.get("actual_entry")
            actual_exit = t.get("actual_exit")
            num_contracts = t.get("num_contracts", 1)
            exit_reason = t.get("exit_reason", "?")
            entry_time = t.get("time", "?")
            reason_short = REASON_LABELS.get(exit_reason, exit_reason.upper())

            if actual_entry and actual_exit:
                has_fills = True
                pnl = (actual_exit - actual_entry) * num_contracts * 100
                total_dollar_pnl += pnl
                icon = "✅" if pnl >= 0 else "❌"
                trade_lines.append(
                    f"{icon} `{entry_time}` {dir_label} **{symbol}** "
                    f"${actual_entry:.2f} → ${actual_exit:.2f} **${pnl:+,.0f}** ({reason_short})"
                )
            else:
                entry = t.get("entry", 0)
                exit_price = t.get("underlying_exit_price", 0)
                stop = t.get("stop", 0)
                risk = abs(entry - stop)
                pnl_pts = (exit_price - entry) if "call" in direction else (entry - exit_price)
                pnl_r = pnl_pts / risk if risk > 0 else 0
                sign = "+" if pnl_r >= 0 else ""
                icon = "✅" if pnl_pts >= 0 else "❌"
                trade_lines.append(
                    f"{icon} `{entry_time}` {dir_label} **{symbol}** "
                    f"${entry:.2f} → ${exit_price:.2f} **{sign}{pnl_r:.2f}R** ({reason_short})"
                )

        open_lines = []
        for t in open_trades:
            dir_label = "CALL" if "call" in t.get("direction", "") else "PUT"
            open_lines.append(
                f"🔵 {dir_label} **{t.get('symbol','?')}** "
                f"Entry ${t.get('entry',0):.2f} | Stop ${t.get('stop',0):.2f} | Target ${t.get('target',0):.2f}"
            )

        desc_parts = []
        if trade_lines:
            desc_parts.append("**Closed Trades**\n" + "\n".join(trade_lines))
        else:
            desc_parts.append("*No closed trades yet*")
        if open_lines:
            desc_parts.append("**Open Positions**\n" + "\n".join(open_lines))
        else:
            desc_parts.append("*No open positions*")

        desc = "\n\n".join(desc_parts)

        if has_fills:
            pnl_display = f"**${total_dollar_pnl:+,.0f}**"
        else:
            pnl_display = "**—**"

        color = 0x3498db

        self._post({
            "embeds": [{
                "title": f"\U0001f4ca Agent Status — {now_str}",
                "color": color,
                "description": desc,
                "fields": [
                    {"name": "Realized P&L", "value": pnl_display, "inline": True},
                    {"name": "Trades", "value": f"{len(closed)} closed, {len(open_trades)} open", "inline": True},
                    {"name": "Scans", "value": f"{total_scans} ({scan_triggers} trig, {scan_rejects} rej)", "inline": True},
                ],
            }]
        })

    def notify_daily_report(self, date_str: str, trades: list[dict],
                            total_scans: int) -> None:
        # Only executed-and-closed trades belong in the daily P&L/record. A
        # still-open position (closed != True) has no exit price yet, so it
        # would render as a bogus "$entry → $0.00  -NNN R (?)" loss and corrupt
        # both the Net P&L and the W/L record. Non-executed signals (e.g.
        # skipped_no_0dte) likewise have no fills. Exclude both; surface any
        # still-open executed positions as a separate note.
        open_positions = [
            t for t in trades
            if t.get("trade_mode") == "0dte_option" and t.get("closed") is not True
        ]
        trades = [t for t in trades if t.get("closed") is True]

        wins = 0
        losses = 0
        total_r = 0.0
        total_dollar_pnl = 0.0
        has_fills = False
        trade_lines = []

        for t in trades:
            direction = t.get("direction", "?")
            dir_label = "CALL" if "call" in direction else "PUT"
            symbol = t.get("symbol", "?")
            entry = t.get("entry", 0)
            stop = t.get("stop", 0)
            exit_price = t.get("underlying_exit_price", 0)
            exit_reason = t.get("exit_reason", "?")
            num_contracts = t.get("num_contracts", 1)
            actual_entry = t.get("actual_entry")
            actual_exit = t.get("actual_exit")
            risk = abs(entry - stop)

            if "call" in direction:
                pnl_pts = exit_price - entry
            else:
                pnl_pts = entry - exit_price
            pnl_r = pnl_pts / risk if risk > 0 else 0
            total_r += pnl_r

            if actual_entry and actual_exit:
                has_fills = True
                option_pnl = (actual_exit - actual_entry) * num_contracts * 100
                total_dollar_pnl += option_pnl
            else:
                option_pnl = None

            win = (option_pnl >= 0) if option_pnl is not None else (pnl_pts >= 0)
            if win:
                wins += 1
            else:
                losses += 1

            reason_short = REASON_LABELS.get(exit_reason, exit_reason.upper())
            icon = "✅" if win else "❌"
            entry_time = t.get("time", "?")

            if actual_entry and actual_exit:
                trade_lines.append(
                    f"{icon} `{entry_time}` {dir_label} **{symbol}** "
                    f"${actual_entry:.2f} → ${actual_exit:.2f} "
                    f"**${option_pnl:+,.0f}** ({reason_short})"
                )
            else:
                sign = "+" if pnl_r >= 0 else ""
                trade_lines.append(
                    f"{icon} `{entry_time}` {dir_label} **{symbol}** "
                    f"${entry:.2f} → ${exit_price:.2f} **{sign}{pnl_r:.2f}R** ({reason_short})"
                )

        total_trades = wins + losses
        win_rate = f"{wins / total_trades * 100:.0f}%" if total_trades > 0 else "N/A"

        if has_fills:
            day_sign = "+" if total_dollar_pnl >= 0 else ""
            pnl_display = f"**${total_dollar_pnl:+,.0f}**"
        else:
            day_sign = "+" if total_r >= 0 else ""
            pnl_display = f"**{day_sign}{total_r:.2f}R**"

        day_emoji = "\U0001f4c8" if (total_dollar_pnl >= 0 if has_fills else total_r >= 0) else "\U0001f4c9"
        color = 0x2e7d32 if (total_dollar_pnl >= 0 if has_fills else total_r >= 0) else 0xc62828

        desc = "\n".join(trade_lines) if trade_lines else "*No closed trades today*"
        if open_positions:
            still_open = ", ".join(
                f"{t.get('time','?')} {t.get('symbol','?')}" for t in open_positions
            )
            desc += f"\n\n⏳ *{len(open_positions)} still open (excluded): {still_open}*"

        self._post({
            "embeds": [{
                "title": f"{day_emoji} Daily Report — {date_str}",
                "color": color,
                "description": desc,
                "fields": [
                    {"name": "Net P&L", "value": pnl_display, "inline": True},
                    {"name": "Record", "value": f"{wins}W — {losses}L ({win_rate})", "inline": True},
                    {"name": "Scans", "value": str(total_scans), "inline": True},
                ],
            }]
        })
