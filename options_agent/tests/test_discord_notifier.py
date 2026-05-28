"""Tests for DiscordNotifier and TradeNotifier dispatcher."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import MagicMock, patch

import pytest

from src.utils.discord_notifier import DiscordNotifier
from src.utils.trade_notifier import TradeNotifier


SAMPLE_TRIGGER = {
    "symbol": "SPY",
    "direction": "call",
    "quality": 8,
    "entry": 530.50,
    "stop": 529.00,
    "target": 533.00,
    "trade_mode": "0dte_option",
    "occ_symbol": "SPY260514C00531000",
    "num_contracts": 2,
    "option_premium": 1.25,
    "option_delta": 0.45,
    "option_strike": 531.0,
    "explosion": "high",
    "chop": 35,
}


class TestDiscordDisabled:
    def test_disabled_when_no_url(self):
        n = DiscordNotifier(webhook_url="")
        assert n.enabled is False

    def test_entry_noop_when_disabled(self):
        n = DiscordNotifier(webhook_url="")
        n.notify_trade_entry(SAMPLE_TRIGGER)

    def test_exit_noop_when_disabled(self):
        n = DiscordNotifier(webhook_url="")
        n.notify_trade_exit(SAMPLE_TRIGGER, "target", 533.00)


class TestDiscordEnabled:
    def test_enabled_when_url_set(self):
        n = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/test")
        assert n.enabled is True

    def test_entry_sends_embed(self):
        n = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/test")
        posted = {}

        def mock_post(payload):
            posted.update(payload)

        n._post = mock_post
        n.notify_trade_entry(SAMPLE_TRIGGER)

        assert len(posted["embeds"]) == 1
        embed = posted["embeds"][0]
        assert "CALL" in embed["title"]
        assert "SPY" in embed["title"]
        assert embed["color"] == 0x2e7d32
        field_names = [f["name"] for f in embed["fields"]]
        assert "Entry" in field_names
        assert "Stop" in field_names
        assert "Target" in field_names
        assert "Contract" in field_names

    def test_exit_sends_embed_win(self):
        n = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/test")
        posted = {}
        n._post = lambda p: posted.update(p)
        n.notify_trade_exit(SAMPLE_TRIGGER, "target", 533.00)

        embed = posted["embeds"][0]
        assert "TARGET HIT" in embed["title"]
        assert embed["color"] == 0x2e7d32
        pnl_field = next(f for f in embed["fields"] if f["name"] == "P&L")
        assert "1.67R" in pnl_field["value"]

    def test_exit_sends_embed_loss(self):
        n = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/test")
        posted = {}
        n._post = lambda p: posted.update(p)
        n.notify_trade_exit(SAMPLE_TRIGGER, "stop", 529.00)

        embed = posted["embeds"][0]
        assert "STOPPED OUT" in embed["title"]
        assert embed["color"] == 0xc62828
        pnl_field = next(f for f in embed["fields"] if f["name"] == "P&L")
        assert "-1.00R" in pnl_field["value"]

    def test_put_direction(self):
        n = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/test")
        posted = {}
        n._post = lambda p: posted.update(p)
        put_trigger = {**SAMPLE_TRIGGER, "direction": "put"}
        n.notify_trade_entry(put_trigger)

        embed = posted["embeds"][0]
        assert "PUT" in embed["title"]
        assert embed["color"] == 0xc62828


class TestDiscordRetry:
    def test_retries_on_connection_error(self):
        n = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/test")
        call_count = 0

        def mock_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Connection reset")

        with patch("src.utils.discord_notifier.urllib.request.urlopen", mock_urlopen):
            n._post({"content": "test"})
        assert call_count == 2

    def test_no_retry_on_http_error(self):
        n = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/test")
        call_count = 0

        def mock_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            import http.client
            resp = MagicMock()
            resp.read.return_value = b"Bad Request"
            raise __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
                "url", 400, "Bad Request", {}, resp
            )

        with patch("src.utils.discord_notifier.urllib.request.urlopen", mock_urlopen):
            n._post({"content": "test"})
        assert call_count == 1


class TestTradeNotifier:
    def test_no_channels_configured(self):
        tn = TradeNotifier(discord_webhook_url="")
        assert len(tn._channels) == 0
        tn.notify_trade_entry(SAMPLE_TRIGGER)

    def test_discord_only(self):
        tn = TradeNotifier(
            discord_webhook_url="https://discord.com/api/webhooks/test/test",
        )
        assert len(tn._channels) == 1
        assert isinstance(tn._channels[0], DiscordNotifier)

    def test_dispatches_to_all_channels(self):
        tn = TradeNotifier(discord_webhook_url="")
        mock_ch1 = MagicMock()
        mock_ch2 = MagicMock()
        tn._channels = [mock_ch1, mock_ch2]

        tn.notify_trade_entry(SAMPLE_TRIGGER)
        mock_ch1.notify_trade_entry.assert_called_once_with(SAMPLE_TRIGGER)
        mock_ch2.notify_trade_entry.assert_called_once_with(SAMPLE_TRIGGER)

    def test_one_channel_failure_doesnt_block_others(self):
        tn = TradeNotifier(discord_webhook_url="")
        failing = MagicMock()
        failing.notify_trade_entry.side_effect = Exception("boom")
        working = MagicMock()
        tn._channels = [failing, working]

        tn.notify_trade_entry(SAMPLE_TRIGGER)
        working.notify_trade_entry.assert_called_once_with(SAMPLE_TRIGGER)

    def test_exit_dispatches(self):
        tn = TradeNotifier(discord_webhook_url="")
        mock_ch = MagicMock()
        tn._channels = [mock_ch]

        tn.notify_trade_exit(SAMPLE_TRIGGER, "stop", 529.0)
        mock_ch.notify_trade_exit.assert_called_once_with(SAMPLE_TRIGGER, "stop", 529.0)
