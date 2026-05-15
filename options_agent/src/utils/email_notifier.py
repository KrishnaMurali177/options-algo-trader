"""Email notifications for trade events via Gmail API (OAuth2)."""

from __future__ import annotations

import base64
import logging
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
        self.recipients = [r.strip() for r in recipient.split(",") if r.strip()] if recipient else []
        self.credentials_file = credentials_file
        self.token_file = token_file
        self._service = None
        self.enabled = bool(self.recipients) and _GMAIL_AVAILABLE
        if not self.recipients:
            logger.info("Email notifier disabled — no GMAIL_RECIPIENT configured")
        elif not _GMAIL_AVAILABLE:
            logger.info("Email notifier disabled — Gmail API packages not installed")
        else:
            logger.info("Email notifier enabled for %d recipient(s)", len(self.recipients))

    def _get_service(self, force_new: bool = False):
        if self._service is None or force_new:
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

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = "me"
        msg["To"] = ", ".join(self.recipients)
        msg.attach(MIMEText(html_body, "html"))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        body = {"raw": raw}

        for attempt in range(2):
            try:
                service.users().messages().send(
                    userId="me", body=body
                ).execute()
                logger.info("Email sent: %s", subject)
                return
            except (BrokenPipeError, ConnectionError, OSError) as e:
                if attempt == 0:
                    logger.warning("Gmail connection stale, reconnecting: %s", e)
                    service = self._get_service(force_new=True)
                    if not service:
                        return
                else:
                    logger.error("Email send failed after retry: %s", e)
            except Exception as e:
                logger.error("Email send failed: %s", e)
                return

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

    def notify_status_update(self, trades: list[dict], open_trades: list[dict],
                             total_scans: int, scan_triggers: int,
                             scan_rejects: int) -> None:
        now = datetime.now(ET)
        now_str = now.strftime("%I:%M %p ET")

        closed = [t for t in trades if t.get("closed")]
        total_dollar_pnl = 0.0
        has_fills = False
        trade_rows = ""
        for t in closed:
            direction = t.get("direction", "?")
            dir_label = "CALL" if "call" in direction else "PUT"
            symbol = t.get("symbol", "?")
            actual_entry = t.get("actual_entry")
            actual_exit = t.get("actual_exit")
            num_contracts = t.get("num_contracts", 1)
            exit_reason = t.get("exit_reason", "?")
            entry_time = t.get("time", "?")
            reason_labels = {
                "stop": "Stop", "decay_target": "Target (decay)",
                "target": "Target", "theta_exit": "Theta",
                "stagnation": "Stagnation", "time_stop": "Time Stop",
                "gainz": "Gainz", "gainz_exit": "Gainz",
            }
            reason_display = reason_labels.get(exit_reason, exit_reason)

            if actual_entry and actual_exit:
                has_fills = True
                pnl = (actual_exit - actual_entry) * num_contracts * 100
                total_dollar_pnl += pnl
                color = "#2e7d32" if pnl >= 0 else "#c62828"
                trade_rows += f'''<tr>
                    <td style="padding:4px 8px;">{dir_label} {symbol}</td>
                    <td>{entry_time}</td>
                    <td>${actual_entry:.2f} → ${actual_exit:.2f}</td>
                    <td style="color:{color}; font-weight:bold;">${pnl:+,.0f}</td>
                    <td>{reason_display}</td>
                </tr>\n'''
            else:
                entry = t.get("entry", 0)
                exit_price = t.get("underlying_exit_price", 0)
                stop = t.get("stop", 0)
                risk = abs(entry - stop)
                pnl_pts = (exit_price - entry) if "call" in direction else (entry - exit_price)
                pnl_r = pnl_pts / risk if risk > 0 else 0
                color = "#2e7d32" if pnl_pts >= 0 else "#c62828"
                sign = "+" if pnl_r >= 0 else ""
                trade_rows += f'''<tr>
                    <td style="padding:4px 8px;">{dir_label} {symbol}</td>
                    <td>{entry_time}</td>
                    <td>${entry:.2f} → ${exit_price:.2f}</td>
                    <td style="color:{color}; font-weight:bold;">{sign}{pnl_r:.2f}R</td>
                    <td>{reason_display}</td>
                </tr>\n'''

        if not trade_rows:
            trade_rows = '<tr><td colspan="5" style="padding:8px; color:#888;">No closed trades yet</td></tr>'

        open_rows = ""
        for t in open_trades:
            dir_label = "CALL" if "call" in t.get("direction", "") else "PUT"
            occ = t.get("occ_symbol", "")
            open_rows += f'''<tr>
                <td style="padding:4px 8px;">{dir_label} {t.get("symbol","?")}</td>
                <td>Entry ${t.get("entry",0):.2f}</td>
                <td>Stop ${t.get("stop",0):.2f}</td>
                <td>Target ${t.get("target",0):.2f}</td>
            </tr>\n'''
        if not open_rows:
            open_rows = '<tr><td colspan="4" style="padding:8px; color:#888;">No open positions</td></tr>'

        if has_fills:
            day_color = "#2e7d32" if total_dollar_pnl >= 0 else "#c62828"
            pnl_display = f"${total_dollar_pnl:+,.0f}"
        else:
            pnl_display = "—"
            day_color = "#555"

        subject = f"Agent Status {now.strftime('%H:%M')}: {len(closed)} trades, {total_scans} scans | {pnl_display}"

        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px;">
            <h2>📊 Agent Status — {now_str}</h2>

            <table style="border-collapse: collapse; margin-bottom: 16px;">
                <tr><td style="padding:4px 16px; font-weight:bold;">Realized P&L</td>
                    <td style="color:{day_color}; font-weight:bold; font-size:16px;">{pnl_display}</td></tr>
                <tr><td style="padding:4px 16px; font-weight:bold;">Closed Trades</td><td>{len(closed)}</td></tr>
                <tr><td style="padding:4px 16px; font-weight:bold;">Open Positions</td><td>{len(open_trades)}</td></tr>
                <tr><td style="padding:4px 16px; font-weight:bold;">Scans Today</td>
                    <td>{total_scans} ({scan_triggers} triggers, {scan_rejects} rejects)</td></tr>
            </table>

            <h3>Closed Trades</h3>
            <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
                <tr style="background:#f5f5f5; font-weight:bold;">
                    <td style="padding:4px 8px;">Symbol</td><td>Time</td><td>Fill</td><td>P&L</td><td>Reason</td>
                </tr>
                {trade_rows}
            </table>

            <h3>Open Positions</h3>
            <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
                {open_rows}
            </table>
        </div>
        """
        self._send(subject, html)

    def notify_daily_report(self, date_str: str, trades: list[dict],
                            total_scans: int) -> None:
        wins = 0
        losses = 0
        total_r = 0.0
        total_dollar_pnl = 0.0
        has_fills = False
        trade_rows = ""

        for t in trades:
            direction = t.get("direction", "?")
            dir_label = "CALL" if "call" in direction else "PUT"
            symbol = t.get("symbol", "?")
            entry = t.get("entry", 0)
            stop = t.get("stop", 0)
            exit_price = t.get("underlying_exit_price", 0)
            exit_reason = t.get("exit_reason", "?")
            occ = t.get("occ_symbol", "")
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

            reason_labels = {
                "stop": "Stop", "decay_target": "Target (decay)",
                "target": "Target", "theta_exit": "Theta",
                "stagnation": "Stagnation", "time_stop": "Time Stop",
                "gainz": "Gainz", "gainz_exit": "Gainz",
            }
            reason_display = reason_labels.get(exit_reason, exit_reason)
            color = "#2e7d32" if win else "#c62828"
            entry_time = t.get("time", "?")

            if actual_entry and actual_exit:
                entry_col = f"${actual_entry:.2f}"
                exit_col = f"${actual_exit:.2f}"
                pnl_col = f'<span style="color:{color}; font-weight:bold;">${option_pnl:+.0f}</span>'
            else:
                entry_col = f"${entry:.2f}"
                exit_col = f"${exit_price:.2f}"
                sign = "+" if pnl_r >= 0 else ""
                pnl_col = f'<span style="color:{color}; font-weight:bold;">{sign}{pnl_r:.2f}R</span>'

            trade_rows += f'''<tr>
                <td style="padding:4px 12px;">{dir_label} {symbol}</td>
                <td>{entry_time}</td>
                <td>{entry_col}</td>
                <td>{exit_col}</td>
                <td>{pnl_col}</td>
                <td>{reason_display}</td>
                <td style="font-size:11px; color:#888;">{occ}</td>
            </tr>\n'''

        if not trade_rows:
            trade_rows = '<tr><td colspan="7" style="padding:8px; color:#888;">No trades today</td></tr>'

        total_trades = wins + losses
        win_rate = f"{wins / total_trades * 100:.0f}%" if total_trades > 0 else "N/A"

        if has_fills:
            day_color = "#2e7d32" if total_dollar_pnl >= 0 else "#c62828"
            day_sign = "+" if total_dollar_pnl >= 0 else ""
            day_emoji = "📈" if total_dollar_pnl >= 0 else "📉"
            pnl_display = f"${total_dollar_pnl:+,.0f}"
            subject = f"Daily Report {date_str}: {day_sign}${abs(total_dollar_pnl):,.0f} | {wins}W {losses}L | {total_scans} scans"
        else:
            day_sign = "+" if total_r >= 0 else ""
            day_color = "#2e7d32" if total_r >= 0 else "#c62828"
            day_emoji = "📈" if total_r >= 0 else "📉"
            pnl_display = f"{day_sign}{total_r:.2f}R"
            subject = f"Daily Report {date_str}: {day_sign}{total_r:.2f}R | {wins}W {losses}L | {total_scans} scans"

        entry_exit_header = "Buy" if has_fills else "Entry"
        exit_header = "Sell" if has_fills else "Exit"

        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 700px;">
            <h2>{day_emoji} Daily Trade Report — {date_str}</h2>

            <table style="border-collapse: collapse; margin-bottom: 16px;">
                <tr>
                    <td style="padding:4px 16px; font-weight:bold;">Net P&L</td>
                    <td style="color:{day_color}; font-weight:bold; font-size:18px;">{pnl_display}</td>
                </tr>
                <tr>
                    <td style="padding:4px 16px; font-weight:bold;">Record</td>
                    <td>{wins}W — {losses}L ({win_rate})</td>
                </tr>
                <tr>
                    <td style="padding:4px 16px; font-weight:bold;">Total Scans</td>
                    <td>{total_scans}</td>
                </tr>
                <tr>
                    <td style="padding:4px 16px; font-weight:bold;">Trades Taken</td>
                    <td>{total_trades}</td>
                </tr>
            </table>

            <h3>Trade Log</h3>
            <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
                <tr style="background:#f5f5f5; font-weight:bold;">
                    <td style="padding:4px 12px;">Symbol</td>
                    <td>Time</td>
                    <td>{entry_exit_header}</td>
                    <td>{exit_header}</td>
                    <td>P&L</td>
                    <td>Reason</td>
                    <td>Contract</td>
                </tr>
                {trade_rows}
            </table>
        </div>
        """
        self._send(subject, html)
