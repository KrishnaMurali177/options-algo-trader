"""Email notifications for trade events via Gmail API (OAuth2)."""

from __future__ import annotations

import base64
import logging
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    _GMAIL_AVAILABLE = True
except ImportError:
    _GMAIL_AVAILABLE = False

ET = ZoneInfo("America/New_York")

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _get_gmail_service(credentials_file: str = "", token_file: str = ""):
    if not _GMAIL_AVAILABLE:
        raise ImportError(
            "Gmail API packages not installed. "
            "Install with: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        )
    creds_path = Path(credentials_file) if credentials_file else CONFIG_DIR / "gmail_credentials.json"
    token_path = Path(token_file) if token_file else CONFIG_DIR / "gmail_token.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"Gmail credentials not found at {creds_path}. "
                    "Download from Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client ID → Download JSON"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


class TradeEmailNotifier:
    def __init__(self, recipient: str, credentials_file: str = "", token_file: str = ""):
        self.recipient = recipient
        self.credentials_file = credentials_file
        self.token_file = token_file
        self._service = None
        self.enabled = bool(recipient) and _GMAIL_AVAILABLE
        if not recipient:
            logger.info("Email notifier disabled — no GMAIL_RECIPIENT configured")
        elif not _GMAIL_AVAILABLE:
            logger.info("Email notifier disabled — Gmail API packages not installed")

    def _get_service(self):
        if self._service is None:
            try:
                self._service = _get_gmail_service(self.credentials_file, self.token_file)
            except FileNotFoundError as e:
                logger.warning("Gmail API not configured: %s", e)
                self.enabled = False
                return None
            except Exception as e:
                logger.error("Gmail API init failed: %s", e)
                self.enabled = False
                return None
        return self._service

    def _send(self, subject: str, html_body: str) -> None:
        if not self.enabled:
            return
        service = self._get_service()
        if not service:
            return
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = "me"
            msg["To"] = self.recipient
            msg.attach(MIMEText(html_body, "html"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            logger.info("Email sent: %s", subject)
        except Exception as e:
            logger.error("Email send failed: %s", e)

    def notify_trade_entry(self, trigger: dict) -> None:
        now = datetime.now(ET).strftime("%I:%M %p ET")
        symbol = trigger.get("symbol", "?")
        direction = trigger.get("direction", "?")
        dir_label = "CALL" if "call" in direction else "PUT"
        quality = trigger.get("quality", "?")
        entry = trigger.get("entry", 0)
        stop = trigger.get("stop", 0)
        target = trigger.get("target", 0)
        trade_mode = trigger.get("trade_mode", "?")
        occ = trigger.get("occ_symbol", "")
        contracts = trigger.get("num_contracts", "?")
        premium = trigger.get("option_premium", 0)
        delta = trigger.get("option_delta", 0)
        strike = trigger.get("option_strike", 0)
        explosion = trigger.get("explosion", "?")
        chop = trigger.get("chop", "?")

        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = f"{reward / risk:.1f}" if risk > 0 else "?"

        subject = f"TRADE OPENED: {dir_label} {symbol} @ ${entry:.2f}"

        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 500px;">
            <h2 style="color: {'#2e7d32' if 'call' in direction else '#c62828'};">
                {'📈' if 'call' in direction else '📉'} {dir_label} {symbol}
            </h2>
            <table style="border-collapse: collapse; width: 100%;">
                <tr><td style="padding: 4px 12px; font-weight: bold;">Time</td><td>{now}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Entry</td><td>${entry:.2f}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Stop</td><td>${stop:.2f}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Target</td><td>${target:.2f}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">R:R</td><td>1:{rr}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Quality</td><td>{quality}/10</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Explosion</td><td>{explosion}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Chop</td><td>{chop}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Mode</td><td>{trade_mode}</td></tr>
        """
        if trade_mode == "0dte_option" and occ:
            html += f"""
                <tr><td style="padding: 4px 12px; font-weight: bold;">Contract</td><td>{occ}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Strike</td><td>${strike:.2f}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Delta</td><td>{delta:.2f}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Premium</td><td>${premium:.2f}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Contracts</td><td>{contracts}</td></tr>
            """
        html += """
            </table>
        </div>
        """
        self._send(subject, html)

    def notify_trade_exit(self, trigger: dict, exit_reason: str,
                          exit_price: float) -> None:
        now = datetime.now(ET).strftime("%I:%M %p ET")
        symbol = trigger.get("symbol", "?")
        direction = trigger.get("direction", "?")
        dir_label = "CALL" if "call" in direction else "PUT"
        entry = trigger.get("entry", 0)
        stop = trigger.get("stop", 0)
        trade_mode = trigger.get("trade_mode", "?")
        occ = trigger.get("occ_symbol", "")

        risk = abs(entry - stop)
        if "call" in direction:
            pnl_pts = exit_price - entry
        else:
            pnl_pts = entry - exit_price
        pnl_r = f"{pnl_pts / risk:.2f}R" if risk > 0 else "?"
        win = pnl_pts >= 0

        reason_labels = {
            "stop": "STOPPED OUT",
            "decay_target": "TARGET HIT (decay)",
            "target": "TARGET HIT",
            "theta_exit": "THETA EXIT",
            "stagnation": "STAGNATION EXIT",
            "time_stop": "TIME STOP",
            "gainz": "GAINZ EXIT",
        }
        reason_display = reason_labels.get(exit_reason, exit_reason.upper())

        color = "#2e7d32" if win else "#c62828"
        emoji = "✅" if win else "❌"

        subject = f"TRADE CLOSED: {dir_label} {symbol} — {reason_display} ({pnl_r})"

        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 500px;">
            <h2 style="color: {color};">{emoji} {reason_display}</h2>
            <table style="border-collapse: collapse; width: 100%;">
                <tr><td style="padding: 4px 12px; font-weight: bold;">Symbol</td><td>{dir_label} {symbol}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Time</td><td>{now}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Entry</td><td>${entry:.2f}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Exit</td><td>${exit_price:.2f}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">P&L</td>
                    <td style="color: {color}; font-weight: bold;">{'+' if pnl_pts >= 0 else ''}{pnl_pts:.2f} pts ({pnl_r})</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Reason</td><td>{reason_display}</td></tr>
                <tr><td style="padding: 4px 12px; font-weight: bold;">Mode</td><td>{trade_mode}</td></tr>
        """
        if occ:
            html += f"""
                <tr><td style="padding: 4px 12px; font-weight: bold;">Contract</td><td>{occ}</td></tr>
            """
        html += """
            </table>
        </div>
        """
        self._send(subject, html)
