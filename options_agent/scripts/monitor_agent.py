#!/usr/bin/env python3
"""Agent health monitor — checks containers and emails a 30-min digest of verdicts."""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
JOURNAL_DIR = Path(__file__).resolve().parent.parent / "sweet_spot_journal"


def get_container_status():
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"],
        capture_output=True, text=True
    )
    containers = {}
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            containers[parts[0]] = parts[1]
    return containers


def get_recent_verdicts(minutes=30):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    verdicts_file = JOURNAL_DIR / f"{today}_verdicts.jsonl"
    if not verdicts_file.exists():
        return []

    cutoff = datetime.now(ET) - timedelta(minutes=minutes)
    recent = []
    for line in verdicts_file.read_text().strip().split("\n"):
        if not line:
            continue
        v = json.loads(line)
        ts = datetime.fromisoformat(v["ts"])
        if ts >= cutoff:
            recent.append(v)
    return recent


def get_open_trades():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    trades = []
    for jf in JOURNAL_DIR.glob(f"{today}_*.json"):
        if "verdicts" in jf.name:
            continue
        data = json.loads(jf.read_text())
        for t in data:
            if not t.get("closed", False):
                trades.append(t)
    return trades


def build_email(containers, verdicts, open_trades):
    now = datetime.now(ET).strftime("%I:%M %p ET")

    # Container health
    expected = ["options-agent-spy", "options-agent-qqq", "options-dashboard"]
    health_rows = ""
    all_ok = True
    for name in expected:
        status = containers.get(name, "NOT RUNNING")
        ok = "healthy" in status.lower() or "up" in status.lower()
        if not ok:
            all_ok = False
        color = "#2e7d32" if ok else "#c62828"
        icon = "&#9989;" if ok else "&#10060;"
        health_rows += f'<tr><td style="padding:4px 12px;">{icon} {name}</td><td style="color:{color};">{status}</td></tr>\n'

    # Verdict summary
    triggers = [v for v in verdicts if v.get("status") == "trigger"]
    rejects = [v for v in verdicts if v.get("status") == "reject"]

    reject_reasons = {}
    for r in rejects:
        key = f"{r.get('symbol', '?')} — {r.get('reason', '?')}"
        reject_reasons[key] = reject_reasons.get(key, 0) + 1

    verdict_rows = ""
    if triggers:
        for t in triggers:
            verdict_rows += f'''<tr style="background:#e8f5e9;">
                <td style="padding:4px 12px;">&#9889; TRIGGER</td>
                <td>{t.get("symbol","?")}</td>
                <td>{t.get("direction","?")}</td>
                <td>Q={t.get("quality","?")}</td>
                <td>${t.get("price",0):.2f}</td>
                <td>{t.get("ts","?")[11:16]}</td>
            </tr>\n'''
    if reject_reasons:
        for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1]):
            verdict_rows += f'''<tr>
                <td style="padding:4px 12px; color:#888;">&#10060; REJECT</td>
                <td colspan="4">{reason}</td>
                <td>x{count}</td>
            </tr>\n'''

    if not verdict_rows:
        verdict_rows = '<tr><td colspan="6" style="padding:8px; color:#888;">No scans in the last 30 minutes</td></tr>'

    # Open trades
    trade_rows = ""
    if open_trades:
        for t in open_trades:
            dir_label = "CALL" if "call" in t.get("direction", "") else "PUT"
            trade_rows += f'''<tr>
                <td style="padding:4px 12px;">{dir_label} {t.get("symbol","?")}</td>
                <td>Entry ${t.get("entry",0):.2f}</td>
                <td>Stop ${t.get("stop",0):.2f}</td>
                <td>Target ${t.get("target",0):.2f}</td>
            </tr>\n'''
    else:
        trade_rows = '<tr><td colspan="4" style="padding:8px; color:#888;">No open positions</td></tr>'

    status_emoji = "&#9989;" if all_ok else "&#9888;&#65039;"

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px;">
        <h2>{status_emoji} Agent Status — {now}</h2>

        <h3>Container Health</h3>
        <table style="border-collapse: collapse; width: 100%;">
            {health_rows}
        </table>

        <h3>Sweet Spot Scans (last 30 min)</h3>
        <p style="color:#555;">{len(triggers)} trigger(s), {len(rejects)} reject(s)</p>
        <table style="border-collapse: collapse; width: 100%;">
            {verdict_rows}
        </table>

        <h3>Open Positions</h3>
        <table style="border-collapse: collapse; width: 100%;">
            {trade_rows}
        </table>
    </div>
    """

    subject_tag = "ALL OK" if all_ok else "WARNING"
    subject = f"Agent Monitor [{subject_tag}]: {len(triggers)} triggers, {len(rejects)} rejects — {now}"
    return subject, html


def send_via_docker(subject, html):
    """Send email by running a one-shot Docker command."""
    escaped_subject = subject.replace("'", "'\\''")
    escaped_html = html.replace("'", "'\\''")
    cmd = [
        "docker-compose", "-f",
        str(Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"),
        "--profile", "live", "run", "--rm", "agent-spy",
        "python", "-c",
        f"from src.utils.email_notifier import TradeEmailNotifier; "
        f"n = TradeEmailNotifier(recipient='rohith.krishna4795@gmail.com'); "
        f"n._send('''{escaped_subject}''', '''{escaped_html}''')"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Email send failed: {result.stderr}", file=sys.stderr)
        return False
    return True


def main():
    containers = get_container_status()
    verdicts = get_recent_verdicts(30)
    open_trades = get_open_trades()
    subject, html = build_email(containers, verdicts, open_trades)

    if send_via_docker(subject, html):
        print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] Monitor email sent: {subject}")
    else:
        print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] Monitor email FAILED", file=sys.stderr)


if __name__ == "__main__":
    main()
