#!/usr/bin/env python
"""Sweet Spot Live Tracker — logs sweet spot triggers in real-time for evaluation.

Runs alongside your dashboard, checks SPY every 5 minutes during market hours,
and logs every sweet spot trigger with entry/exit levels. At EOD, it checks
what would have happened if you'd entered.

Usage:
    cd options_agent
    python scripts/track_sweet_spots.py              # Run during market hours
    python scripts/track_sweet_spots.py --review     # Review today's results at EOD
    python scripts/track_sweet_spots.py --history    # Show all logged triggers

Results are saved to: sweet_spot_journal/YYYY-MM-DD.json
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

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.opening_range import OpeningRangeAnalyzer
from src.recent_momentum import RecentMomentumAnalyzer
from src.momentum_cascade import MomentumCascadeDetector
from src.utils.quality_scorer import compute_quality_score
from src.utils.choppiness import compute_choppiness
from src.market_analyzer import MarketAnalyzer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

JOURNAL_DIR = Path(__file__).resolve().parent.parent / "sweet_spot_journal"
JOURNAL_DIR.mkdir(exist_ok=True)


def get_current_et_time() -> datetime:
    """Get current time in US/Eastern."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("US/Eastern"))


def is_market_open() -> bool:
    """Check if market is currently open (9:30-16:00 ET, weekdays)."""
    now = get_current_et_time()
    if now.weekday() >= 5:  # Weekend
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def check_sweet_spot(symbol: str = "SPY", max_chop: int = 5) -> dict | None:
    """Check if current conditions trigger a sweet spot.
    
    Returns trigger data dict if sweet spot is active, None otherwise.
    """
    try:
        analyzer = MarketAnalyzer()
        indicators = analyzer.analyze(symbol, timeframe="15min")
        
        # Opening Range
        or_analyzer = OpeningRangeAnalyzer()
        or_result = or_analyzer.analyze(symbol)
        or_direction = or_result.direction if or_result else "neutral"
        or_momentum = or_result.momentum_score if or_result else 0
        
        # Recent Momentum
        rc_analyzer = RecentMomentumAnalyzer()
        rc_result = rc_analyzer.analyze(symbol)
        recent_dir = rc_result.direction if rc_result else "neutral"
        recent_momentum = rc_result.momentum_score if rc_result else 0
        
        # Quality Score
        direction = "buy_call" if or_momentum >= 25 else "buy_put" if or_momentum <= -25 else None
        if direction is None:
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
        )
        quality = quality_result.score
        
        # Cascade
        cascade_detector = MomentumCascadeDetector()
        cascade = cascade_detector.analyze(
            indicators, quality_score=quality,
            or_momentum=or_momentum, recent_momentum=recent_momentum,
        )
        
        # Choppiness
        bars = yf.download(symbol, period="1d", interval="5m", progress=False)
        if isinstance(bars.columns, pd.MultiIndex):
            bars.columns = bars.columns.get_level_values(0)
        chop = compute_choppiness(bars)
        
        # Sweet spot check
        sweet_spot_quality = 4 <= quality <= 8
        sweet_spot_cascade = cascade.explosion_score >= 4
        chop_ok = chop.chop_score <= max_chop
        
        if sweet_spot_quality and sweet_spot_cascade and chop_ok:
            now = get_current_et_time()
            
            # Compute entry levels (from OR result)
            range_high = or_result.range_high if or_result else indicators.current_price
            range_low = or_result.range_low if or_result else indicators.current_price
            range_width = range_high - range_low
            
            if direction == "buy_call":
                entry = range_high + range_width * 0.10
                mid = (range_high + range_low) / 2
                stop = mid + 0.10 * (range_high - range_low)  # Tighter: 60% of range
                risk = entry - stop
                target_1 = entry + risk * 0.75
                target_2 = entry + risk * 1.5
            else:
                entry = range_low - range_width * 0.10
                mid = (range_high + range_low) / 2
                stop = mid - 0.10 * (range_high - range_low)  # Tighter: 60% of range
                risk = stop - entry
                target_1 = entry - risk * 0.75
                target_2 = entry - risk * 1.5
            
            return {
                "timestamp": now.isoformat(),
                "time": now.strftime("%H:%M"),
                "symbol": symbol,
                "direction": direction,
                "price_at_trigger": indicators.current_price,
                "quality": quality,
                "explosion": cascade.explosion_score,
                "chop": chop.chop_score,
                "or_momentum": or_momentum,
                "recent_momentum": recent_momentum,
                "entry_level": round(entry, 2),
                "stop_level": round(stop, 2),
                "target_1": round(target_1, 2),
                "target_2": round(target_2, 2),
                "range_high": round(range_high, 2),
                "range_low": round(range_low, 2),
                # Filled at EOD review
                "outcome": None,
                "exit_price": None,
                "pnl": None,
            }
        
        return None
        
    except Exception as e:
        logger.error("Error checking sweet spot: %s", e)
        return None


def review_day(day: date, symbol: str = "SPY"):
    """Review a day's triggers and compute what would have happened."""
    journal_file = JOURNAL_DIR / f"{day.isoformat()}.json"
    if not journal_file.exists():
        print(f"  No journal for {day}")
        return
    
    triggers = json.loads(journal_file.read_text())
    if not triggers:
        print(f"  No triggers on {day}")
        return
    
    # Get the day's 5-min data
    start = day.isoformat()
    end = (day + timedelta(days=1)).isoformat()
    df = yf.download(symbol, start=start, end=end, interval="5m", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty:
        print(f"  No price data for {day}")
        return
    
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("US/Eastern")
    
    print(f"\n  {'Time':<6} {'Dir':<9} {'Entry':>8} {'Stop':>8} {'T1':>8} {'T2':>8} {'Outcome':<10} {'P&L':>8}")
    print(f"  {'─'*6} {'─'*9} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*8}")
    
    results = []
    for t in triggers:
        entry = t["entry_level"]
        stop = t["stop_level"]
        t1 = t["target_1"]
        t2 = t["target_2"]
        direction = t["direction"]
        trigger_time = t["time"]
        
        # Walk through bars after trigger time
        after_trigger = df[df.index.strftime("%H:%M") >= trigger_time]
        
        outcome = "no_fill"
        exit_price = None
        entered = False
        
        for _, bar in after_trigger.iterrows():
            h, l, c = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
            
            if not entered:
                if direction == "buy_call" and h >= entry:
                    entered = True
                elif direction == "buy_put" and l <= entry:
                    entered = True
                continue
            
            if direction == "buy_call":
                if l <= stop:
                    outcome = "stop"; exit_price = stop; break
                if h >= t2:
                    outcome = "target_2"; exit_price = t2; break
                if h >= t1:
                    outcome = "target_1"; exit_price = t1; break
            else:
                if h >= stop:
                    outcome = "stop"; exit_price = stop; break
                if l <= t2:
                    outcome = "target_2"; exit_price = t2; break
                if l <= t1:
                    outcome = "target_1"; exit_price = t1; break
        
        if entered and exit_price is None:
            # EOD exit
            exit_price = float(after_trigger["Close"].iloc[-1])
            outcome = "eod"
        
        if exit_price:
            pnl = (exit_price - entry) if direction == "buy_call" else (entry - exit_price)
        else:
            pnl = 0.0
        
        t["outcome"] = outcome
        t["exit_price"] = exit_price
        t["pnl"] = round(pnl, 4)
        
        dir_label = "CALL" if direction == "buy_call" else "PUT"
        pnl_str = f"${pnl:+.2f}" if exit_price else "—"
        print(f"  {trigger_time:<6} {dir_label:<9} ${entry:>7.2f} ${stop:>7.2f} ${t1:>7.2f} ${t2:>7.2f} {outcome:<10} {pnl_str:>8}")
        results.append(t)
    
    # Save updated journal with outcomes
    journal_file.write_text(json.dumps(results, indent=2, default=str))
    
    # Summary
    filled = [r for r in results if r["outcome"] not in ("no_fill", None)]
    if filled:
        wins = sum(1 for r in filled if (r["pnl"] or 0) > 0)
        total_pnl = sum(r["pnl"] or 0 for r in filled)
        print(f"\n  Summary: {len(filled)} fills, {wins}/{len(filled)} wins ({wins/len(filled)*100:.0f}%), P&L: ${total_pnl:+.2f}")


def show_history():
    """Show all historical sweet spot journal entries."""
    files = sorted(JOURNAL_DIR.glob("*.json"))
    if not files:
        print("  No journal entries yet. Run the tracker during market hours first.")
        return
    
    all_triggers = []
    for f in files:
        triggers = json.loads(f.read_text())
        all_triggers.extend(triggers)
    
    filled = [t for t in all_triggers if t.get("outcome") and t["outcome"] != "no_fill"]
    
    print(f"\n  Total journal days: {len(files)}")
    print(f"  Total triggers: {len(all_triggers)}")
    print(f"  Filled trades: {len(filled)}")
    
    if filled:
        wins = sum(1 for t in filled if (t.get("pnl") or 0) > 0)
        total_pnl = sum(t.get("pnl") or 0 for t in filled)
        print(f"  Win rate: {wins}/{len(filled)} ({wins/len(filled)*100:.0f}%)")
        print(f"  Total P&L: ${total_pnl:+.2f}")
        print(f"\n  Last 10 trades:")
        print(f"  {'Date':<12} {'Time':<6} {'Dir':<5} {'Q':>2} {'E':>2} {'C':>2} {'Outcome':<10} {'P&L':>8}")
        print(f"  {'─'*12} {'─'*6} {'─'*5} {'─'*2} {'─'*2} {'─'*2} {'─'*10} {'─'*8}")
        for t in filled[-10:]:
            d = t.get("timestamp", "")[:10]
            dir_l = "CALL" if "call" in t["direction"] else "PUT"
            print(f"  {d:<12} {t['time']:<6} {dir_l:<5} {t['quality']:>2} {t['explosion']:>2} {t['chop']:>2} {t['outcome']:<10} ${t['pnl']:>+7.2f}")


def run_tracker(symbol: str = "SPY", interval_min: int = 5, max_chop: int = 5,
                paper_trade: bool = False, qty: int = 1):
    """Run the live tracker during market hours."""
    today = date.today()
    journal_file = JOURNAL_DIR / f"{today.isoformat()}.json"
    
    # Load existing triggers for today
    if journal_file.exists():
        triggers = json.loads(journal_file.read_text())
    else:
        triggers = []
    
    # Initialize paper trader if requested
    trader = None
    if paper_trade:
        try:
            from src.utils.alpaca_paper import AlpacaPaperTrader
            trader = AlpacaPaperTrader()
            print(f"  ✅ Alpaca paper trading ENABLED (qty={qty})")
            pnl = trader.get_today_pnl()
            print(f"     Account: ${pnl['equity']:,.0f} equity, ${pnl['buying_power']:,.0f} buying power")
        except Exception as e:
            print(f"  ⚠️ Paper trading failed to init: {e}")
            print(f"     Continuing in journal-only mode")
            trader = None
    
    print(f"\n  🔍 Sweet Spot Tracker — {symbol} — {today}")
    print(f"  Checking every {interval_min} minutes during market hours")
    print(f"  Filter: Quality 4-7, Cascade ≥ 4, Chop ≤ {max_chop}")
    print(f"  Paper Trading: {'ON' if trader else 'OFF'}")
    print(f"  Journal: {journal_file}")
    print(f"  Press Ctrl+C to stop\n")
    
    last_trigger_time = None
    
    while True:
        if not is_market_open():
            now = get_current_et_time()
            if now.hour >= 16:
                print(f"\n  Market closed. Triggers today: {len(triggers)}")
                break
            print(f"  Waiting for market open... ({now.strftime('%H:%M')} ET)")
            time.sleep(60)
            continue
        
        trigger = check_sweet_spot(symbol, max_chop=max_chop)
        now = get_current_et_time()
        
        if trigger:
            # Avoid duplicate triggers within 15 min
            if last_trigger_time and (now - last_trigger_time).total_seconds() < 900:
                pass  # Skip duplicate
            else:
                triggers.append(trigger)
                journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                last_trigger_time = now
                
                dir_label = "🟢 CALL" if "call" in trigger["direction"] else "🔴 PUT"
                print(f"  ⚡ SWEET SPOT @ {trigger['time']} — {dir_label}")
                print(f"     Q={trigger['quality']} E={trigger['explosion']} C={trigger['chop']} "
                      f"Mom={trigger['or_momentum']:+d}")
                print(f"     Entry: ${trigger['entry_level']:.2f}  Stop: ${trigger['stop_level']:.2f}  "
                      f"T1: ${trigger['target_1']:.2f}  T2: ${trigger['target_2']:.2f}")
                
                # Execute paper trade if enabled
                if trader:
                    try:
                        order = trader.place_sweet_spot_trade(
                            symbol=symbol,
                            direction=trigger["direction"],
                            qty=qty,
                            entry=trigger["entry_level"],
                            stop=trigger["stop_level"],
                            target=trigger["target_1"],  # Use T1 as take profit
                            time_in_force="day",
                        )
                        trigger["paper_order_id"] = order["order_id"]
                        print(f"     📝 Paper order placed: {order['order_id'][:8]}... ({order['status']})")
                    except Exception as e:
                        print(f"     ⚠️ Paper order failed: {e}")
                
                print()
        else:
            print(f"  {now.strftime('%H:%M')} — no sweet spot", end="\r")
        
        time.sleep(interval_min * 60)


def main():
    parser = argparse.ArgumentParser(description="Sweet Spot Live Tracker")
    parser.add_argument("--symbol", "-s", default="SPY")
    parser.add_argument("--review", action="store_true", help="Review today's triggers at EOD")
    parser.add_argument("--review-date", default=None, help="Review a specific date (YYYY-MM-DD)")
    parser.add_argument("--history", action="store_true", help="Show all historical results")
    parser.add_argument("--interval", type=int, default=5, help="Check interval in minutes (default: 5)")
    parser.add_argument("--max-chop", type=int, default=5, help="Max chop score (default: 5)")
    parser.add_argument("--paper-trade", action="store_true",
                        help="Execute trades on Alpaca paper account when sweet spots trigger")
    parser.add_argument("--qty", type=int, default=1, help="Shares per paper trade (default: 1)")
    args = parser.parse_args()
    
    if args.history:
        show_history()
    elif args.review or args.review_date:
        review_date = date.fromisoformat(args.review_date) if args.review_date else date.today()
        print(f"\n  Reviewing {review_date}...")
        review_day(review_date, args.symbol)
    else:
        run_tracker(args.symbol, args.interval, args.max_chop, args.paper_trade, args.qty)


if __name__ == "__main__":
    main()

