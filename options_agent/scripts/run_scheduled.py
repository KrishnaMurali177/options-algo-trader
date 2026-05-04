#!/usr/bin/env python
"""Scheduled agent — runs at configured market scan times (Eastern Time)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import settings
from src.agent import TradingAgent

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Default watchlist — extend as needed
WATCHLIST = os.environ.get("WATCHLIST", "AAPL,MSFT,SPY").split(",")


async def scan_and_trade():
    """Run the agent for each symbol in the watchlist."""
    agent = TradingAgent()
    for symbol in WATCHLIST:
        symbol = symbol.strip().upper()
        logger.info("━━━ Scanning %s ━━━", symbol)
        summary = await agent.run(symbol=symbol, dry_run=settings.dry_run)
        logger.info("Result for %s: %s — %s",
                     symbol, summary.get("status"), summary.get("message", ""))


def main():
    scheduler = AsyncIOScheduler(timezone="America/New_York")

    # Parse scan times from settings, e.g. "09:35,12:00,15:30"
    for time_str in settings.market_scan_times.split(","):
        time_str = time_str.strip()
        hour, minute = time_str.split(":")
        trigger = CronTrigger(
            day_of_week="mon-fri",
            hour=int(hour),
            minute=int(minute),
            timezone="America/New_York",
        )
        scheduler.add_job(scan_and_trade, trigger, id=f"scan_{time_str}", replace_existing=True)
        logger.info("Scheduled scan at %s ET (Mon-Fri)", time_str)

    scheduler.start()
    logger.info("Scheduler started. Press Ctrl+C to stop.")

    # Keep the event loop running
    loop = asyncio.new_event_loop()
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler.")
        scheduler.shutdown()


if __name__ == "__main__":
    main()

