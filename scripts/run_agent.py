#!/usr/bin/env python
"""CLI entry point — run the trading agent for a single symbol."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from src.agent import TradingAgent


def main():
    parser = argparse.ArgumentParser(description="Options Trading Agent — single run")
    parser.add_argument("--symbol", "-s", required=True, help="Stock symbol (e.g., AAPL)")
    parser.add_argument("--dry-run", action="store_true", default=None,
                        help="Log orders without executing (overrides .env)")
    parser.add_argument("--live", action="store_true",
                        help="Enable live trading (CAUTION)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else getattr(logging, settings.log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    dry_run = True  # safe default
    if args.live:
        dry_run = False
        logging.warning("🔴 LIVE TRADING ENABLED — real orders will be placed!")
    elif args.dry_run is not None:
        dry_run = True
    else:
        dry_run = settings.dry_run

    agent = TradingAgent()
    summary = asyncio.run(agent.run(symbol=args.symbol.upper(), dry_run=dry_run))

    print("\n" + "=" * 60)
    print("AGENT EXECUTION SUMMARY")
    print("=" * 60)
    print(json.dumps(summary, indent=2, default=str))
    print("=" * 60)

    if summary.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()

