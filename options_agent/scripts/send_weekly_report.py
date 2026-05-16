"""Generate and send weekly performance report to Discord."""

from __future__ import annotations

import glob
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.alpaca_fills import enrich_trades_with_fills
from src.utils.discord_notifier import DiscordNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
JOURNAL_DIR = os.environ.get("JOURNAL_DIR", "/app/sweet_spot_journal")


def get_week_range() -> tuple[str, str]:
    today = datetime.now(ET).date()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()


def load_trades(start_date: str, end_date: str) -> list[dict]:
    trades = []
    for pattern_date in _date_range(start_date, end_date):
        for f in glob.glob(os.path.join(JOURNAL_DIR, f"{pattern_date}*.json")):
            if "verdicts" in f:
                continue
            with open(f) as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    trades.extend(data)
                else:
                    trades.append(data)
    return trades


def _date_range(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    dates = []
    while s <= e:
        dates.append(s.isoformat())
        s += timedelta(days=1)
    return dates


def count_scans(start_date: str, end_date: str) -> int:
    total = 0
    for d in _date_range(start_date, end_date):
        for f in glob.glob(os.path.join(JOURNAL_DIR, f"{d}*_verdicts.jsonl")):
            with open(f) as fh:
                total += sum(1 for _ in fh)
    return total


def send_weekly_report():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        logger.error("DISCORD_WEBHOOK_URL not set")
        return

    discord = DiscordNotifier(webhook_url)
    start_date, end_date = get_week_range()

    trades = load_trades(start_date, end_date)
    if not trades:
        logger.info("No trades found for %s to %s", start_date, end_date)
        discord._post({
            "embeds": [{
                "title": f"📊 Weekly Report — {start_date} to {end_date}",
                "color": 0x3498db,
                "description": "*No trades this week*",
            }]
        })
        return

    enrich_trades_with_fills(trades)

    closed = [t for t in trades if t.get("closed") and t.get("actual_exit")]
    open_trades = [t for t in trades if not t.get("closed") or not t.get("actual_exit")]

    total_pnl = 0.0
    wins = 0
    losses = 0
    trade_lines = []

    reason_labels = {
        "stop": "STOP", "decay_target": "TARGET", "stagnation": "STAGNATION",
        "time_stop": "TIME STOP", "target": "TARGET", "gainz": "GAINZ",
    }

    for t in closed:
        pnl = (t["actual_exit"] - t["actual_entry"]) * t.get("num_contracts", 3) * 100
        total_pnl += pnl
        win = pnl >= 0
        if win:
            wins += 1
        else:
            losses += 1
        dir_label = "CALL" if "call" in t.get("direction", "") else "PUT"
        date = t.get("timestamp", "")[:10]
        entry_time = t.get("time", "?")
        exit_reason = t.get("exit_reason", "?")
        reason_short = reason_labels.get(exit_reason, exit_reason.upper())
        icon = "✅" if win else "❌"
        trade_lines.append(
            f"{icon} `{date} {entry_time}` {dir_label} **{t.get('symbol', '?')}** "
            f"${t['actual_entry']:.2f} → ${t['actual_exit']:.2f} **${pnl:+,.0f}** ({reason_short})"
        )

    for t in open_trades:
        dir_label = "CALL" if "call" in t.get("direction", "") else "PUT"
        date = t.get("timestamp", "")[:10]
        entry_time = t.get("time", "?")
        trade_lines.append(
            f"⚠️ `{date} {entry_time}` {dir_label} **{t.get('symbol', '?')}** "
            f"${t.get('actual_entry', 0):.2f} — *no exit fill*"
        )

    total_trades = wins + losses
    win_rate = f"{wins}/{total_trades} ({wins / total_trades * 100:.0f}%)" if total_trades else "N/A"
    avg_pnl = total_pnl / total_trades if total_trades else 0
    total_scans = count_scans(start_date, end_date)

    desc = "\n".join(trade_lines)
    color = 0x2e7d32 if total_pnl >= 0 else 0xc62828
    day_emoji = "📈" if total_pnl >= 0 else "📉"

    discord._post({
        "embeds": [{
            "title": f"{day_emoji} Weekly Report — {start_date} to {end_date}",
            "color": color,
            "description": desc,
            "fields": [
                {"name": "Net P&L", "value": f"**${total_pnl:+,.0f}**", "inline": True},
                {"name": "Win Rate", "value": win_rate, "inline": True},
                {"name": "Avg/Trade", "value": f"${avg_pnl:+,.0f}", "inline": True},
                {"name": "Total Scans", "value": str(total_scans), "inline": True},
                {"name": "Trades", "value": f"{len(trades)} total ({total_trades} closed, {len(open_trades)} unresolved)", "inline": True},
            ],
            "footer": {"text": "Sweet Spot Agent | SPY + QQQ | 0DTE Options"},
        }]
    })
    logger.info("Weekly report sent — %s to %s: $%+.0f (%dW/%dL)",
                start_date, end_date, total_pnl, wins, losses)


if __name__ == "__main__":
    send_weekly_report()
