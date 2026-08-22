"""Unified trade notifier — dispatches to all configured channels (Discord)."""

from __future__ import annotations

import logging

from src.utils.discord_notifier import DiscordNotifier

logger = logging.getLogger(__name__)


class TradeNotifier:
    def __init__(self, discord_webhook_url: str = ""):
        self._channels = []

        discord = DiscordNotifier(webhook_url=discord_webhook_url)
        if discord.enabled:
            self._channels.append(discord)

        if not self._channels:
            logger.info("No notification channels configured — notifications disabled")

    def notify_trade_entry(self, trigger: dict) -> None:
        for ch in self._channels:
            try:
                ch.notify_trade_entry(trigger)
            except Exception as e:
                logger.error("Notification failed (%s): %s", type(ch).__name__, e)

    def notify_trade_exit(self, trigger: dict, exit_reason: str,
                          exit_price: float) -> None:
        for ch in self._channels:
            try:
                ch.notify_trade_exit(trigger, exit_reason, exit_price)
            except Exception as e:
                logger.error("Notification failed (%s): %s", type(ch).__name__, e)

    def notify_status_update(self, trades: list[dict], open_trades: list[dict],
                             total_scans: int, scan_triggers: int,
                             scan_rejects: int) -> None:
        for ch in self._channels:
            try:
                ch.notify_status_update(trades, open_trades, total_scans,
                                        scan_triggers, scan_rejects)
            except Exception as e:
                logger.error("Notification failed (%s): %s", type(ch).__name__, e)

    def notify_daily_report(self, date_str: str, trades: list[dict],
                            total_scans: int, warning: str | None = None) -> None:
        for ch in self._channels:
            try:
                ch.notify_daily_report(date_str, trades, total_scans, warning=warning)
            except Exception as e:
                logger.error("Notification failed (%s): %s", type(ch).__name__, e)

    def notify_alert(self, title: str, description: str, level: str = "warning") -> None:
        for ch in self._channels:
            try:
                ch.notify_alert(title, description, level=level)
            except Exception as e:
                logger.error("Notification failed (%s): %s", type(ch).__name__, e)
