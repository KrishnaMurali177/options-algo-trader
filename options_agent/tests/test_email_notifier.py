"""Tests for the TradeEmailNotifier — especially optional-feature behavior."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock

import pytest


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


class TestNotifierDisabledWithoutRecipient:
    def test_disabled_when_no_recipient(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="")
        assert notifier.enabled is False

    def test_notify_entry_noop_when_disabled(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="")
        notifier.notify_trade_entry(SAMPLE_TRIGGER)

    def test_notify_exit_noop_when_disabled(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="")
        notifier.notify_trade_exit(SAMPLE_TRIGGER, "target", 533.00)


class TestNotifierDisabledWithoutPackages:
    def test_disabled_when_gmail_unavailable(self):
        import src.utils.email_notifier as mod
        original = mod._GMAIL_AVAILABLE
        try:
            mod._GMAIL_AVAILABLE = False
            notifier = mod.TradeEmailNotifier(recipient="test@example.com")
            assert notifier.enabled is False
        finally:
            mod._GMAIL_AVAILABLE = original

    def test_notify_entry_noop_when_packages_missing(self):
        import src.utils.email_notifier as mod
        original = mod._GMAIL_AVAILABLE
        try:
            mod._GMAIL_AVAILABLE = False
            notifier = mod.TradeEmailNotifier(recipient="test@example.com")
            notifier.notify_trade_entry(SAMPLE_TRIGGER)
        finally:
            mod._GMAIL_AVAILABLE = original

    def test_get_gmail_service_raises_when_unavailable(self):
        import src.utils.email_notifier as mod
        original = mod._GMAIL_AVAILABLE
        try:
            mod._GMAIL_AVAILABLE = False
            with pytest.raises(ImportError, match="Gmail API packages not installed"):
                mod._get_gmail_service()
        finally:
            mod._GMAIL_AVAILABLE = original


class TestNotifierWithCredentials:
    def test_enabled_when_recipient_and_packages_present(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="test@example.com")
        assert notifier.enabled is True

    def test_disables_on_missing_credentials_file(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(
            recipient="test@example.com",
            credentials_file="/nonexistent/creds.json",
            token_file="/nonexistent/token.json",
        )
        assert notifier.enabled is True
        service = notifier._get_service()
        assert service is None
        assert notifier.enabled is False

    def test_send_skipped_when_disabled(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="")
        notifier._send("subject", "<p>body</p>")


class TestRetryOnBrokenPipe:
    def test_retries_on_broken_pipe(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="test@example.com")
        notifier.enabled = True

        execute_calls = 0
        def execute_side_effect():
            nonlocal execute_calls
            execute_calls += 1
            if execute_calls == 1:
                raise BrokenPipeError("Broken pipe")

        mock_service = MagicMock()
        mock_service.users.return_value.messages.return_value.send.return_value.execute = execute_side_effect
        notifier._service = mock_service

        fresh_service = MagicMock()
        original_get = notifier._get_service
        def patched_get(force_new=False):
            if force_new:
                notifier._service = fresh_service
                return fresh_service
            return original_get()
        notifier._get_service = patched_get

        notifier._send("Test Subject", "<p>test</p>")
        assert execute_calls == 1
        fresh_service.users.return_value.messages.return_value.send.return_value.execute.assert_called_once()

    def test_fails_after_retry_exhausted(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="test@example.com")
        notifier.enabled = True

        mock_service = MagicMock()
        mock_service.users.return_value.messages.return_value.send.return_value.execute.side_effect = BrokenPipeError("Broken pipe")

        notifier._service = mock_service
        notifier._get_service = lambda force_new=False: mock_service
        notifier._send("Test Subject", "<p>test</p>")
        assert mock_service.users.return_value.messages.return_value.send.return_value.execute.call_count == 2

    def test_no_retry_on_other_errors(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="test@example.com")
        notifier.enabled = True

        mock_service = MagicMock()
        mock_service.users.return_value.messages.return_value.send.return_value.execute.side_effect = ValueError("some error")

        notifier._service = mock_service
        notifier._send("Test Subject", "<p>test</p>")
        assert mock_service.users.return_value.messages.return_value.send.return_value.execute.call_count == 1


class TestEmailContent:
    def test_entry_email_contains_trade_details(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="test@example.com")
        notifier.enabled = True

        sent = {}
        def mock_send(subject, html):
            sent["subject"] = subject
            sent["html"] = html

        notifier._send = mock_send
        notifier.notify_trade_entry(SAMPLE_TRIGGER)

        assert "SPY" in sent["subject"]
        assert "CALL" in sent["subject"]
        assert "$530.50" in sent["html"]
        assert "$529.00" in sent["html"]
        assert "$533.00" in sent["html"]
        assert "8/10" in sent["html"]

    def test_exit_email_contains_pnl(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="test@example.com")
        notifier.enabled = True

        sent = {}
        def mock_send(subject, html):
            sent["subject"] = subject
            sent["html"] = html

        notifier._send = mock_send
        notifier.notify_trade_exit(SAMPLE_TRIGGER, "target", 533.00)

        assert "CLOSED" in sent["subject"]
        assert "TARGET HIT" in sent["subject"]
        assert "$533.00" in sent["html"]
        assert "1.67R" in sent["html"]

    def test_exit_email_loss(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="test@example.com")
        notifier.enabled = True

        sent = {}
        notifier._send = lambda s, h: sent.update(subject=s, html=h)
        notifier.notify_trade_exit(SAMPLE_TRIGGER, "stop", 529.00)

        assert "STOPPED OUT" in sent["subject"]
        assert "-1.00R" in sent["html"]

    def test_put_direction_label(self):
        from src.utils.email_notifier import TradeEmailNotifier
        notifier = TradeEmailNotifier(recipient="test@example.com")
        notifier.enabled = True

        sent = {}
        notifier._send = lambda s, h: sent.update(subject=s, html=h)
        put_trigger = {**SAMPLE_TRIGGER, "direction": "put"}
        notifier.notify_trade_entry(put_trigger)

        assert "PUT" in sent["subject"]


SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


class TestMonitorScript:
    def test_build_email_all_ok(self):
        from monitor_agent import build_email

        containers = {
            "options-agent-spy": "Up 2 hours (healthy)",
            "options-agent-qqq": "Up 2 hours (healthy)",
            "options-dashboard": "Up 2 hours (healthy)",
        }
        verdicts = [
            {"status": "trigger", "symbol": "SPY", "direction": "call", "quality": 8, "price": 530.5, "ts": "2026-05-14T10:30:00-04:00"},
            {"status": "reject", "symbol": "QQQ", "reason": "chop", "ts": "2026-05-14T10:35:00-04:00"},
        ]
        open_trades = [
            {"direction": "call", "symbol": "SPY", "entry": 530.5, "stop": 529.0, "target": 533.0},
        ]

        subject, html = build_email(containers, verdicts, open_trades)
        assert "ALL OK" in subject
        assert "1 triggers" in subject
        assert "1 rejects" in subject
        assert "TRIGGER" in html
        assert "REJECT" in html
        assert "SPY" in html

    def test_build_email_container_down(self):
        from monitor_agent import build_email

        containers = {
            "options-agent-spy": "Up 2 hours (healthy)",
            "options-dashboard": "Up 2 hours (healthy)",
        }
        subject, html = build_email(containers, [], [])
        assert "WARNING" in subject
        assert "NOT RUNNING" in html

    def test_build_email_no_scans(self):
        from monitor_agent import build_email

        containers = {
            "options-agent-spy": "Up 2 hours (healthy)",
            "options-agent-qqq": "Up 2 hours (healthy)",
            "options-dashboard": "Up 2 hours (healthy)",
        }
        subject, html = build_email(containers, [], [])
        assert "No scans" in html
