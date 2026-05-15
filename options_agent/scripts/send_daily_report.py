#!/usr/bin/env python3
"""Send daily trade report via all configured notification channels."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.trade_notifier import TradeNotifier

ET = ZoneInfo("America/New_York")
JOURNAL_DIR = Path(__file__).resolve().parent.parent / "sweet_spot_journal"


def load_trades(date_str: str) -> list[dict]:
    trades = []
    for jf in JOURNAL_DIR.glob(f"{date_str}_*.json"):
        if "verdicts" in jf.name:
            continue
        data = json.loads(jf.read_text())
        trades.extend(data)
    trades.sort(key=lambda t: t.get("timestamp", ""))
    return trades


def count_scans(date_str: str) -> int:
    verdicts_file = JOURNAL_DIR / f"{date_str}_verdicts.jsonl"
    if not verdicts_file.exists():
        return 0
    return sum(1 for line in verdicts_file.read_text().strip().split("\n") if line)


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ET).strftime("%Y-%m-%d")
    trades = load_trades(date_str)
    total_scans = count_scans(date_str)

    notifier = TradeNotifier(
        gmail_recipient=os.getenv("GMAIL_RECIPIENT", ""),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
    )
    notifier.notify_daily_report(date_str, trades, total_scans)
    print(f"Daily report sent for {date_str}: {len(trades)} trades, {total_scans} scans")


if __name__ == "__main__":
    main()
