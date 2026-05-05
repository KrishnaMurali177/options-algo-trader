#!/usr/bin/env python
"""Autonomous Sweet Spot Agent — runs every trading day, paper trades sweet spots.

This agent:
  1. Starts at 9:30 AM ET on weekdays
  2. Scans for sweet spots every 5 minutes until 3:30 PM
  3. Places bracket orders on Alpaca paper account when triggered
  4. Logs all activity to sweet_spot_journal/
  5. Sends a daily summary at market close

Run as a background service:
    # One-time test
    python scripts/run_sweet_spot_agent.py

    # Daemonized (keeps running daily)
    python scripts/run_sweet_spot_agent.py --daemon

    # With custom settings
    python scripts/run_sweet_spot_agent.py --daemon --qty 10 --max-chop 5

Schedule with cron/launchd (alternative to --daemon):
    # crontab -e
    30 9 * * 1-5 cd /path/to/options_agent && python scripts/run_sweet_spot_agent.py >> logs/agent.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import yfinance as yf

from src.utils.alpaca_data import fetch_bars as alpaca_fetch_bars
from src.market_analyzer import MarketAnalyzer
from src.opening_range import OpeningRangeAnalyzer
from src.recent_momentum import RecentMomentumAnalyzer
from src.momentum_cascade import MomentumCascadeDetector
from src.utils.quality_scorer import compute_quality_score
from src.utils.choppiness import compute_choppiness
from src.utils.gainz import gainz_signal


def _check_gainz_exit(symbol: str, direction: str, body_ratio: float,
                      rsi_overbought: float, rsi_oversold: float) -> bool:
    """Return True if the latest completed 5-min bar fires an opposing Gainz signal."""
    try:
        bars = alpaca_fetch_bars(symbol, days_back=1, interval="5min")
        if len(bars) < 16:
            return False
        # Use the last completed bar (penultimate row — last row may be in-progress)
        bar = bars.iloc[-2]
        close = bars["Close"].astype(float)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi_series = 100 - (100 / (1 + rs))
        rsi_val = float(rsi_series.iloc[-2])
        sig = gainz_signal(
            float(bar["Open"]), float(bar["High"]), float(bar["Low"]), float(bar["Close"]),
            rsi_val, body_ratio_min=body_ratio,
            rsi_overbought=rsi_overbought, rsi_oversold=rsi_oversold,
        )
        if direction == "buy_call" and sig == "sell":
            return True
        if direction == "buy_put" and sig == "buy":
            return True
        return False
    except Exception as e:
        logger.warning("Gainz exit check failed for %s: %s", symbol, e)
        return False

# ── Setup ──
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
JOURNAL_DIR = Path(__file__).resolve().parent.parent / "sweet_spot_journal"
JOURNAL_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "sweet_spot_agent.log"),
    ],
)
logger = logging.getLogger("sweet_spot_agent")


def get_et_now() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("US/Eastern"))


def is_market_hours() -> bool:
    now = get_et_now()
    if now.weekday() >= 5:
        return False
    return now.hour * 60 + now.minute >= 9 * 60 + 30 and now.hour < 16


def is_past_or(min_after_open: int = 65) -> bool:
    """Wait until at least 65 min after open (10:35) for OR to form."""
    now = get_et_now()
    minutes_since_open = (now.hour * 60 + now.minute) - (9 * 60 + 30)
    return minutes_since_open >= min_after_open


def check_sweet_spot(symbol: str, max_chop: int = 5, regime_guard: bool = True) -> dict | None:
    """Evaluate sweet spot conditions. Returns trigger dict or None."""
    try:
        analyzer = MarketAnalyzer()
        indicators = analyzer.analyze(symbol, timeframe="15min")

        or_analyzer = OpeningRangeAnalyzer()
        or_result = or_analyzer.analyze(indicators)
        or_direction = or_result.breakout_direction if or_result else "neutral"
        or_momentum = or_result.momentum_score if or_result else 0

        rc_analyzer = RecentMomentumAnalyzer()
        rc_result = rc_analyzer.analyze(indicators)
        recent_dir = rc_result.direction if rc_result else "neutral"
        recent_momentum = rc_result.momentum_score if rc_result else 0

        direction = "buy_call" if or_momentum >= 25 else "buy_put" if or_momentum <= -25 else None
        if direction is None:
            return None

        # Regime guard: block counter-trend trades unless RSI extreme (mirror of replay logic)
        if regime_guard:
            bullish_regime = indicators.sma_20 > indicators.sma_50
            bearish_regime = indicators.sma_20 < indicators.sma_50
            if direction == "buy_put" and bullish_regime and indicators.rsi_14 <= 70:
                return None
            if direction == "buy_call" and bearish_regime and indicators.rsi_14 >= 30:
                return None

        quality_result = compute_quality_score(
            direction=direction,
            current_price=indicators.current_price,
            sma_20=indicators.sma_20,
            sma_50=indicators.sma_50,
            vix=indicators.vix,
            volume=1.0,
            volume_sma_20=1.0,
            or_direction=or_direction,
            or_momentum=or_momentum,
            or_confirmed=abs(or_momentum) >= 40,
            recent_dir=recent_dir,
            recent_momentum=recent_momentum,
            zlema_trend=indicators.zlema_trend,
        )
        quality = quality_result.score

        cascade = MomentumCascadeDetector().analyze(
            indicators, quality_score=quality,
            or_momentum=or_momentum, recent_momentum=recent_momentum,
        )

        bars = alpaca_fetch_bars(symbol, days_back=1, interval="5min")
        chop = compute_choppiness(bars)

        # Sweet spot criteria
        if not (3 <= quality <= 7):
            return None
        if cascade.explosion_score < 4:
            return None
        if chop.chop_score > max_chop:
            return None

        now = get_et_now()
        range_high = or_result.range_high if or_result else indicators.current_price
        range_low = or_result.range_low if or_result else indicators.current_price
        range_width = range_high - range_low

        # ── Entry confirmation: price must be in upper/lower 25% of range or beyond ──
        breakout_threshold = range_width * 0.25
        if direction == "buy_call" and indicators.current_price < (range_high - breakout_threshold):
            return None
        if direction == "buy_put" and indicators.current_price > (range_low + breakout_threshold):
            return None

        # Target multiplier scales with cascade strength
        if cascade.explosion_score >= 8:
            target_mult = 1.5  # Explosive — let it run
        elif cascade.explosion_score >= 6:
            target_mult = 1.25  # Strong — extended target
        else:
            target_mult = 1.0  # Moderate — standard 1R

        if direction == "buy_call":
            entry = range_high + range_width * 0.10
            stop = (range_high + range_low) / 2
            risk = entry - stop
            target_1 = entry + risk * target_mult
        else:
            entry = range_low - range_width * 0.10
            stop = (range_high + range_low) / 2
            risk = stop - entry
            target_1 = entry - risk * target_mult

        return {
            "timestamp": now.isoformat(),
            "time": now.strftime("%H:%M"),
            "symbol": symbol,
            "direction": direction,
            "price": indicators.current_price,
            "quality": quality,
            "explosion": cascade.explosion_score,
            "chop": chop.chop_score,
            "or_momentum": or_momentum,
            "recent_momentum": recent_momentum,
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target_1, 2),
            "target_mult": target_mult,
            "range_high": round(range_high, 2),
            "range_low": round(range_low, 2),
        }

    except Exception as e:
        logger.error("Sweet spot check failed: %s", e)
        return None


def run_day(symbol: str, qty: int, max_chop: int, paper_trade: bool,
            max_trades_per_day: int = 3, max_stops_per_day: int = 1,
            scan_start_min: int = 60,
            gainz_exit: bool = True,
            gainz_body_ratio: float = 0.7,
            gainz_rsi_overbought: float = 70.0,
            gainz_rsi_oversold: float = 30.0,
            trade_shares: bool = False,
            contracts: int = 1,
            target_delta: float = 0.50,
            cascade_size_low: int = 3,
            cascade_size_mid: int = 3,
            cascade_size_high: int = 3,
            regime_guard: bool = True):
    """Run the agent for one trading day."""
    today = date.today()
    journal_file = JOURNAL_DIR / f"{today.isoformat()}.json"
    triggers = json.loads(journal_file.read_text()) if journal_file.exists() else []

    trader = None
    if paper_trade:
        try:
            from src.utils.alpaca_paper import AlpacaPaperTrader
            trader = AlpacaPaperTrader()
            pnl = trader.get_today_pnl()
            logger.info("Paper account: $%.0f equity, $%.0f buying power", pnl["equity"], pnl["buying_power"])
        except Exception as e:
            logger.error("Paper trader init failed: %s — running journal-only", e)

    logger.info("═══ Sweet Spot Agent: %s — %s ═══", symbol, today)
    logger.info("Settings: qty=%d, max_chop=%d, max_trades=%d, max_stops=%d, scan_start=%dmin, regime_guard=%s, paper=%s, mode=%s",
                qty, max_chop, max_trades_per_day, max_stops_per_day, scan_start_min,
                "ON" if regime_guard else "OFF", bool(trader),
                "shares" if trade_shares else f"0DTE options (contracts={contracts}, delta={target_delta})")

    last_trigger = None
    scan_count = 0
    trades_today = 0
    stops_today = 0
    open_directions: dict[str, str] = {}  # symbol/occ_symbol → "buy_call" / "buy_put"
    open_options: dict[str, dict] = {}  # occ_symbol → {entry_premium, stop_price, target_price, direction}

    while True:
        now = get_et_now()

        if not is_market_hours():
            if now.hour >= 16:
                break
            time.sleep(60)
            continue

        # ── Gainz early-exit check (runs every loop, even after entry cutoff) ──
        if gainz_exit and trader and open_directions:
            try:
                live_symbols = {p["symbol"] for p in trader.get_positions()}
            except Exception as e:
                logger.warning("Failed to fetch positions for Gainz check: %s", e)
                live_symbols = set()
            # Drop tracked symbols that are no longer open (stop/target/EOD already fired)
            open_directions = {s: d for s, d in open_directions.items() if s in live_symbols}
            for sym, dir_ in list(open_directions.items()):
                if _check_gainz_exit(sym, dir_, gainz_body_ratio,
                                     gainz_rsi_overbought, gainz_rsi_oversold):
                    logger.info("⚡ GAINZ EXIT: closing %s %s on opposing reversal signal",
                                sym, "CALL" if "call" in dir_ else "PUT")
                    close_result = trader.close_position(sym)
                    # Stamp the matching trigger with the Gainz exit info
                    for trig in triggers:
                        if trig.get("symbol") == sym and not trig.get("closed"):
                            trig["exit_reason"] = "gainz_exit"
                            trig["exit_time"] = get_et_now().isoformat()
                            if close_result:
                                trig["close_order_id"] = close_result["order_id"]
                            trig["closed"] = True
                            break
                    journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                    open_directions.pop(sym, None)

        # ── Options stop/target/time-stop monitoring (underlying-based, since no bracket for options) ──
        if not trade_shares and trader and open_options:
            try:
                current_price_bars = alpaca_fetch_bars(symbol, days_back=1, interval="5min")
                if len(current_price_bars) > 0:
                    underlying_price = float(current_price_bars["Close"].iloc[-1])
                    for occ_sym, opt_info in list(open_options.items()):
                        should_close = False
                        close_reason = ""
                        if "call" in opt_info["direction"]:
                            if underlying_price <= opt_info["stop_price"]:
                                should_close = True
                                close_reason = "stop"
                            elif underlying_price >= opt_info["target_price"]:
                                should_close = True
                                close_reason = "target"
                        else:  # put
                            if underlying_price >= opt_info["stop_price"]:
                                should_close = True
                                close_reason = "stop"
                            elif underlying_price <= opt_info["target_price"]:
                                should_close = True
                                close_reason = "target"

                        # Time stop: close at 15:30 ET (15 min before broker's 3:45 forced close)
                        if not should_close and now.hour == 15 and now.minute >= 30:
                            should_close = True
                            close_reason = "time_stop"

                        if should_close:
                            logger.info("⚡ OPTIONS %s: closing %s (%s at $%.2f)",
                                        close_reason.upper(), occ_sym, opt_info["direction"], underlying_price)
                            close_result = trader.close_options_position(occ_sym)
                            for trig in triggers:
                                if trig.get("occ_symbol") == occ_sym and not trig.get("closed"):
                                    trig["exit_reason"] = close_reason
                                    trig["exit_time"] = get_et_now().isoformat()
                                    trig["underlying_exit_price"] = underlying_price
                                    if close_result:
                                        trig["close_order_id"] = close_result["order_id"]
                                    trig["closed"] = True
                                    break
                            journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                            open_options.pop(occ_sym, None)
                            open_directions.pop(occ_sym, None)
                            if close_reason == "stop":
                                stops_today += 1
            except Exception as e:
                logger.warning("Options monitoring failed: %s", e)

        # Stop opening NEW trades after 2:00 PM (still monitor existing for exits)
        if now.hour >= 14:
            # After 15:30, if no open positions, we're done for the day
            if now.hour == 15 and now.minute >= 30 and not open_directions:
                logger.info("Time stop reached (15:30 ET) and no open positions. Done for today.")
                break
            if not open_directions:
                break
            time.sleep(300)
            continue

        # Wait for OR to form + scan start delay
        if not is_past_or(min_after_open=scan_start_min):
            logger.info("Waiting for scan window (OR + %d min)...", scan_start_min)
            time.sleep(60)
            continue

        # ── Daily limits ──
        if max_trades_per_day > 0 and trades_today >= max_trades_per_day:
            logger.info("Daily trade limit reached (%d/%d). Monitoring open positions only.", trades_today, max_trades_per_day)
            if not open_directions:
                break
            time.sleep(300)
            continue
        if max_stops_per_day > 0 and stops_today >= max_stops_per_day:
            logger.info("Daily stop limit reached (%d/%d). Halting for protection.", stops_today, max_stops_per_day)
            break

        scan_count += 1
        trigger = check_sweet_spot(symbol, max_chop=max_chop, regime_guard=regime_guard)

        if trigger:
            # Deduplicate (15 min cooldown)
            if last_trigger and (now - last_trigger).total_seconds() < 900:
                logger.debug("Skipping duplicate trigger within 15 min")
            else:
                last_trigger = now
                trades_today += 1
                triggers.append(trigger)
                journal_file.write_text(json.dumps(triggers, indent=2, default=str))

                dir_label = "CALL" if "call" in trigger["direction"] else "PUT"
                logger.info(
                    "⚡ SWEET SPOT: %s %s Q=%d E=%d C=%d Mom=%+d "
                    "Entry=$%.2f Stop=$%.2f Target=$%.2f",
                    dir_label, symbol, trigger["quality"], trigger["explosion"],
                    trigger["chop"], trigger["or_momentum"],
                    trigger["entry"], trigger["stop"], trigger["target"],
                )

                if trader:
                    try:
                        if trade_shares:
                            # Legacy: trade shares with bracket order
                            order = trader.place_sweet_spot_trade(
                                symbol=symbol,
                                direction=trigger["direction"],
                                qty=qty,
                                entry=None,  # Market for immediate fill
                                stop=trigger["stop"],
                                target=trigger["target"],
                                time_in_force="day",
                            )
                            trigger["order_id"] = order["order_id"]
                            trigger["trade_mode"] = "shares"
                            open_directions[symbol] = trigger["direction"]
                        else:
                            # 0DTE options: fetch chain, pick contract, buy-to-open
                            from src.utils.alpaca_data import get_0dte_chain
                            opt_type = "call" if "call" in trigger["direction"] else "put"
                            contract = get_0dte_chain(symbol, option_type=opt_type, target_delta=target_delta)
                            if contract:
                                # Cascade-tier contract sizing
                                explosion = trigger.get("explosion", 4)
                                if explosion >= 8:
                                    num_contracts = contracts * cascade_size_high
                                elif explosion >= 6:
                                    num_contracts = contracts * cascade_size_mid
                                else:
                                    num_contracts = contracts * cascade_size_low

                                order = trader.place_options_trade(
                                    occ_symbol=contract["occ_symbol"],
                                    direction=trigger["direction"],
                                    qty=num_contracts,
                                    limit_price=contract["mid"],  # Limit at mid for better fill
                                    time_in_force="day",
                                )
                                trigger["order_id"] = order["order_id"]
                                trigger["occ_symbol"] = contract["occ_symbol"]
                                trigger["option_strike"] = contract["strike"]
                                trigger["option_delta"] = contract["delta"]
                                trigger["option_premium"] = contract["mid"]
                                trigger["trade_mode"] = "0dte_option"
                                trigger["num_contracts"] = num_contracts

                                # Track for stop/target monitoring (underlying-based)
                                open_options[contract["occ_symbol"]] = {
                                    "entry_premium": contract["mid"],
                                    "stop_price": trigger["stop"],
                                    "target_price": trigger["target"],
                                    "direction": trigger["direction"],
                                    "delta": contract["delta"],
                                }
                                open_directions[contract["occ_symbol"]] = trigger["direction"]
                                logger.info("  📝 0DTE %s order: %s strike=$%.2f Δ=%.2f premium=$%.2f × %d contracts",
                                            opt_type.upper(), contract["occ_symbol"],
                                            contract["strike"], contract["delta"], contract["mid"], num_contracts)
                            else:
                                logger.warning("  ⚠️ No 0DTE contract found — falling back to shares")
                                order = trader.place_sweet_spot_trade(
                                    symbol=symbol,
                                    direction=trigger["direction"],
                                    qty=qty,
                                    entry=None,
                                    stop=trigger["stop"],
                                    target=trigger["target"],
                                    time_in_force="day",
                                )
                                trigger["order_id"] = order["order_id"]
                                trigger["trade_mode"] = "shares_fallback"
                                open_directions[symbol] = trigger["direction"]

                        journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                        logger.info("  📝 Order placed: %s", trigger.get("order_id", "?")[:12])
                    except Exception as e:
                        logger.error("  ⚠️ Order failed: %s", e)

        time.sleep(300)  # 5 min interval

    # ── EOD Reconciliation: backfill outcomes for every trigger ──
    if trader and triggers:
        _reconcile_journal(trader, triggers, journal_file)

    # ── EOD Summary ──
    logger.info("═══ EOD Summary: %d scans, %d triggers ═══", scan_count, len(triggers))
    if trader:
        try:
            pnl = trader.get_today_pnl()
            logger.info("  Paper P&L: $%.2f (%.2f%%)", pnl["today_pnl"], pnl["today_pnl_pct"])
            positions = trader.get_positions()
            if positions:
                logger.info("  Open positions:")
                for p in positions:
                    logger.info("    %s: %s @ $%.2f (P&L: $%.2f)",
                                p["symbol"], p["qty"], p["entry_price"], p["unrealized_pnl"])
        except Exception as e:
            logger.error("  EOD summary failed: %s", e)


def _reconcile_journal(trader, triggers: list[dict], journal_file: Path) -> None:
    """Pull fill data from Alpaca and fill in actual_entry / exit_price / pnl on each trigger.

    Handles four exit cases:
      - target  (bracket take-profit leg filled)
      - stop    (bracket stop-loss leg filled)
      - gainz_exit (already stamped by the Gainz handler; we just look up the close fill)
      - eod     (still open at EOD — Alpaca will auto-close DAY orders; mark and let next-day reconcile)
    """
    for trig in triggers:
        order_id = trig.get("order_id")
        if not order_id:
            continue

        # ── Entry fill (always look up — even Gainz exits need the actual entry price) ──
        outcome = trader.get_order_outcome(order_id)
        if outcome:
            if outcome["actual_entry"] is not None:
                trig["actual_entry"] = outcome["actual_entry"]
                trig.setdefault("entry_filled_at", outcome["entry_filled_at"])

            # ── Exit fill: prefer Gainz close if already stamped, else use bracket leg ──
            if trig.get("exit_reason") == "gainz_exit" and trig.get("close_order_id"):
                fill = trader.get_fill_price(trig["close_order_id"])
                if fill:
                    trig["exit_price"] = fill["price"]
                    trig["exit_time"] = trig.get("exit_time") or fill["time"]
            elif outcome["exit_price"] is not None:
                trig["exit_price"] = outcome["exit_price"]
                trig["exit_time"] = outcome["exit_time"]
                trig["exit_reason"] = outcome["exit_reason"]
                trig["closed"] = True
            elif not trig.get("closed"):
                # Position still open — will reconcile on next run
                trig["exit_reason"] = "open"

        # ── Compute P&L per share if we have both fills ──
        entry = trig.get("actual_entry")
        exit_p = trig.get("exit_price")
        if entry is not None and exit_p is not None:
            if "call" in trig.get("direction", ""):
                pnl = exit_p - entry
            else:  # put = short the underlying
                pnl = entry - exit_p
            trig["pnl"] = round(pnl, 4)
            trig["is_winner"] = pnl > 0

    journal_file.write_text(json.dumps(triggers, indent=2, default=str))
    closed = sum(1 for t in triggers if t.get("closed"))
    open_n = len(triggers) - closed
    pnl_total = sum(t.get("pnl", 0.0) for t in triggers if t.get("pnl") is not None)
    logger.info("  Journal reconciled: %d closed, %d open, realized P&L: $%.2f",
                closed, open_n, pnl_total)


def main():
    parser = argparse.ArgumentParser(description="Autonomous Sweet Spot Paper Trading Agent")
    parser.add_argument("--symbol", "-s", default="SPY", help="Symbol to monitor (default: SPY)")
    parser.add_argument("--qty", type=int, default=1, help="Shares per trade (default: 1)")
    parser.add_argument("--max-chop", type=int, default=5, help="Max choppiness (default: 5)")
    parser.add_argument("--max-trades-per-day", type=int, default=3, help="Max trades per day (default: 3)")
    parser.add_argument("--max-stops-per-day", type=int, default=1, help="Halt after N stop-outs (default: 1)")
    parser.add_argument("--scan-start-min", type=int, default=60, help="Minutes after open to start scanning (default: 60 = 10:30)")
    parser.add_argument("--daemon", action="store_true", help="Run continuously (restarts daily)")
    parser.add_argument("--no-paper", action="store_true", help="Journal only, no paper orders")
    parser.add_argument("--no-gainz-exit", action="store_true",
                        help="Disable GainzAlgoV2 reversal early-exit (default: enabled)")
    parser.add_argument("--gainz-body-ratio", type=float, default=0.7,
                        help="Min candle body/range ratio for Gainz signal (default: 0.7)")
    parser.add_argument("--gainz-rsi-overbought", type=float, default=70.0,
                        help="RSI threshold for Gainz SELL signal (default: 70)")
    parser.add_argument("--gainz-rsi-oversold", type=float, default=30.0,
                        help="RSI threshold for Gainz BUY signal (default: 30)")
    parser.add_argument("--shares", action="store_true",
                        help="Trade shares instead of 0DTE options (default: options)")
    parser.add_argument("--contracts", type=int, default=1,
                        help="Number of option contracts per trade (default: 1)")
    parser.add_argument("--target-delta", type=float, default=0.50,
                        help="Target delta for 0DTE option selection (default: 0.50 = ATM)")
    parser.add_argument("--cascade-size-low", type=int, default=3,
                        help="Contracts for E 4-5 tier (default: 3)")
    parser.add_argument("--cascade-size-mid", type=int, default=3,
                        help="Contracts for E 6-7 tier (default: 3)")
    parser.add_argument("--cascade-size-high", type=int, default=3,
                        help="Contracts for E 8+ tier (default: 3)")
    parser.add_argument("--no-regime-guard", action="store_true",
                        help="Disable regime guard (default: ON — blocks counter-trend trades unless RSI extreme)")
    args = parser.parse_args()

    paper_trade = not args.no_paper

    if args.daemon:
        logger.info("Starting in daemon mode — will run every trading day")
        while True:
            now = get_et_now()
            # Only run on weekdays
            if now.weekday() < 5:
                if now.hour < 9 or (now.hour == 9 and now.minute < 25):
                    # Wait until 9:25 AM
                    wait_until = now.replace(hour=9, minute=25, second=0, microsecond=0)
                    sleep_sec = (wait_until - now).total_seconds()
                    logger.info("Sleeping %.0f min until pre-market...", sleep_sec / 60)
                    time.sleep(max(sleep_sec, 60))
                elif now.hour < 16:
                    run_day(args.symbol, args.qty, args.max_chop, paper_trade,
                            max_trades_per_day=args.max_trades_per_day,
                            max_stops_per_day=args.max_stops_per_day,
                            scan_start_min=args.scan_start_min,
                            gainz_exit=not args.no_gainz_exit,
                            gainz_body_ratio=args.gainz_body_ratio,
                            gainz_rsi_overbought=args.gainz_rsi_overbought,
                            gainz_rsi_oversold=args.gainz_rsi_oversold,
                            trade_shares=args.shares,
                            contracts=args.contracts,
                            target_delta=args.target_delta,
                            cascade_size_low=args.cascade_size_low,
                            cascade_size_mid=args.cascade_size_mid,
                            cascade_size_high=args.cascade_size_high,
                            regime_guard=not args.no_regime_guard)
                    # After market close, sleep until next day 9:25 AM
                    tomorrow_925 = (now + timedelta(days=1)).replace(hour=9, minute=25, second=0)
                    sleep_sec = (tomorrow_925 - get_et_now()).total_seconds()
                    logger.info("Market closed. Sleeping %.1f hours until tomorrow...", sleep_sec / 3600)
                    time.sleep(max(sleep_sec, 3600))
                else:
                    # After hours — sleep until tomorrow
                    time.sleep(3600)
            else:
                # Weekend — sleep until Monday 9:25
                days_until_monday = (7 - now.weekday()) % 7 or 7
                monday = (now + timedelta(days=days_until_monday)).replace(hour=9, minute=25, second=0)
                sleep_sec = (monday - now).total_seconds()
                logger.info("Weekend. Sleeping %.1f hours until Monday...", sleep_sec / 3600)
                time.sleep(max(sleep_sec, 3600))
    else:
        # Single day run
        run_day(args.symbol, args.qty, args.max_chop, paper_trade,
                max_trades_per_day=args.max_trades_per_day,
                max_stops_per_day=args.max_stops_per_day,
                scan_start_min=args.scan_start_min,
                gainz_exit=not args.no_gainz_exit,
                gainz_body_ratio=args.gainz_body_ratio,
                gainz_rsi_overbought=args.gainz_rsi_overbought,
                gainz_rsi_oversold=args.gainz_rsi_oversold,
                trade_shares=args.shares,
                contracts=args.contracts,
                target_delta=args.target_delta,
                cascade_size_low=args.cascade_size_low,
                cascade_size_mid=args.cascade_size_mid,
                cascade_size_high=args.cascade_size_high,
                regime_guard=not args.no_regime_guard)


if __name__ == "__main__":
    main()

