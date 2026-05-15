"""Unified trade notifier — dispatches to all configured channels (Gmail, Discord)."""

from __future__ import annotations

import logging

from src.utils.email_notifier import TradeEmailNotifier
from src.utils.discord_notifier import DiscordNotifier

logger = logging.getLogger(__name__)


class TradeNotifier:
    def __init__(self, gmail_recipient: str = "", discord_webhook_url: str = "",
                 gmail_credentials_file: str = "", gmail_token_file: str = ""):
        self._channels = []

        email = TradeEmailNotifier(
            recipient=gmail_recipient,
            credentials_file=gmail_credentials_file,
            token_file=gmail_token_file,
        )
        if email.enabled:
            self._channels.append(email)

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
