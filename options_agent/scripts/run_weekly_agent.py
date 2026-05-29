#!/usr/bin/env python3
"""Weekly options agent — CLI wrapper + daemon loop.

Thin script that delegates to weekly.agent for all logic.
Same daemon pattern as run_sweet_spot_agent.py with wall-clock safe sleep.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Add project root to path so imports work from /app in Docker
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.alpaca_data import is_trading_day
from src.utils.alpaca_paper import AlpacaPaperTrader
from src.utils.discord_notifier import DiscordNotifier
from weekly.agent import run_weekly_day
from weekly.state.position_manager import WeeklyPositionManager

ET = ZoneInfo("America/New_York")

logger = logging.getLogger("weekly_agent")

JOURNAL_DIR = Path(__file__).resolve().parent.parent / "weekly_journal"


def _safe_sleep(seconds: float, label: str = "waiting") -> None:
    """Wall-clock aware sleep (survives system suspend)."""
    deadline = time.time() + seconds
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(300, remaining))


def get_et_now() -> datetime:
    return datetime.now(ET)


def main():
    parser = argparse.ArgumentParser(description="Weekly Options Agent")
    parser.add_argument("--symbol", "-s", default="SPY", help="Symbol to trade")
    parser.add_argument("--daemon", action="store_true", help="Run every trading day")
    parser.add_argument("--min-dte", type=int, default=3, help="Minimum DTE")
    parser.add_argument("--max-dte", type=int, default=7, help="Maximum DTE")
    parser.add_argument("--target-delta", type=float, default=0.35, help="Target delta")
    parser.add_argument("--max-premium", type=float, default=500.0, help="Max dollars per trade")
    parser.add_argument("--max-open", type=int, default=2, help="Max open positions per symbol")
    parser.add_argument("--verbose-rejects", action="store_true", help="Log rejected scans")
    parser.add_argument("--no-paper", action="store_true", help="Journal only, no orders")
    args = parser.parse_args()

    # Logging
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Components
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    state_mgr = WeeklyPositionManager(JOURNAL_DIR)

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    trader = AlpacaPaperTrader(api_key, secret_key) if not args.no_paper else None

    discord_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    discord = DiscordNotifier(discord_url) if discord_url else None

    if trader:
        try:
            acct = trader.get_account()
            equity = float(acct.get("equity", 0))
            buying_power = float(acct.get("buying_power", 0))
            logger.info("Alpaca paper account: $%.2f buying power, $%.2f equity", buying_power, equity)
        except Exception as e:
            logger.warning("Account check failed: %s", e)

    if args.daemon:
        logger.info("Starting weekly agent in daemon mode — %s", args.symbol)
        while True:
            now = get_et_now()

            if now.weekday() >= 5 or not is_trading_day(now):
                if now.weekday() >= 5:
                    days_until = (7 - now.weekday()) % 7 or 7
                    next_day = (now + timedelta(days=days_until)).replace(hour=9, minute=25, second=0)
                    label = "Weekend"
                else:
                    next_day = (now + timedelta(days=1)).replace(hour=9, minute=25, second=0)
                    label = "Market holiday"
                sleep_sec = (next_day - now).total_seconds()
                logger.info("%s. Sleeping %.1f hours...", label, sleep_sec / 3600)
                _safe_sleep(max(sleep_sec, 3600), f"weekly_{args.symbol}_closed")

            elif now.hour < 9 or (now.hour == 9 and now.minute < 25):
                wait_until = now.replace(hour=9, minute=25, second=0, microsecond=0)
                sleep_sec = (wait_until - now).total_seconds()
                logger.info("Sleeping %.0f min until pre-market...", sleep_sec / 60)
                _safe_sleep(max(sleep_sec, 60), f"weekly_{args.symbol}_pre")

            elif now.hour < 16:
                run_weekly_day(
                    args.symbol, trader, state_mgr, discord,
                    min_dte=args.min_dte, max_dte=args.max_dte,
                    target_delta=args.target_delta,
                    max_premium=args.max_premium,
                    max_open=args.max_open,
                    verbose_rejects=args.verbose_rejects,
                )
                tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=25, second=0)
                sleep_sec = (tomorrow - get_et_now()).total_seconds()
                logger.info("Market closed. Sleeping %.1f hours until tomorrow...", sleep_sec / 3600)
                _safe_sleep(max(sleep_sec, 3600), f"weekly_{args.symbol}_post")
            else:
                _safe_sleep(3600, f"weekly_{args.symbol}_after")
    else:
        # Single day run
        if trader is None:
            logger.error("Single-day mode requires --no-paper to be removed")
            return
        run_weekly_day(
            args.symbol, trader, state_mgr, discord,
            min_dte=args.min_dte, max_dte=args.max_dte,
            target_delta=args.target_delta,
            max_premium=args.max_premium,
            max_open=args.max_open,
            verbose_rejects=args.verbose_rejects,
        )


if __name__ == "__main__":
    main()
