"""Regression check: compare live `check_sweet_spot` against the replay path.

For each --time on --date, runs the live agent's `check_sweet_spot` against
that day's bars and prints what it emits. Then runs `replay_day` for the
full date and prints any triggers it produces. Used to confirm the live
and replay paths agree on golden defaults.

Default --times come from the live agent's journal for --date if it exists;
otherwise from --times-fallback (10:30, 10:45, 11:30).

Usage:
    Set-Location options_agent
    ..\\venv\\Scripts\\python.exe scripts\\verify_live_vs_replay.py
    ..\\venv\\Scripts\\python.exe scripts\\verify_live_vs_replay.py --date 2026-05-08
    ..\\venv\\Scripts\\python.exe scripts\\verify_live_vs_replay.py --symbol QQQ --times 10:30,11:00,12:15
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.run_sweet_spot_agent as agent
from scripts.replay_sweet_spot import _build_indicators_from_bars, replay_day

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _REPO_ROOT / "data_cache"
_JOURNAL_DIR = _REPO_ROOT / "sweet_spot_journal"


def load_bars(symbol: str) -> pd.DataFrame:
    """Load the symbol's 7d 5-min parquet (preferred) or fall back to any *_5min_*.parquet."""
    preferred = _CACHE_DIR / f"{symbol}_5min_7d.parquet"
    candidates = [preferred] if preferred.exists() else sorted(
        _CACHE_DIR.glob(f"{symbol}_5min_*d.parquet"),
        key=lambda p: int(p.stem.rsplit("_", 1)[-1].rstrip("d")),
    )
    if not candidates:
        raise FileNotFoundError(
            f"No cached 5-min bars for {symbol} in {_CACHE_DIR}. "
            f"Run the live agent or replay once to populate the cache."
        )
    src = preferred if preferred.exists() else candidates[0]
    df = pd.read_parquet(src)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("America/New_York")
    print(f"Loaded bars from {src.relative_to(_REPO_ROOT)}")
    return df


def journal_times(symbol: str, day: date) -> list[str]:
    """Return list of HH:MM strings the live agent fired at on `day` for `symbol`."""
    f = _JOURNAL_DIR / f"{day.isoformat()}.json"
    if not f.exists():
        return []
    try:
        rows = json.loads(f.read_text())
    except Exception:
        return []
    return [r["time"] for r in rows if r.get("symbol") == symbol and r.get("time")]


class _SliceState:
    """Holds the current 'as-of' time so the patched fetch can slice bars."""
    slice_at: datetime | None = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", "-s", default="SPY", help="Ticker (default: SPY)")
    p.add_argument("--date", "-d", default=None,
                   help="Trading date YYYY-MM-DD (default: today in America/New_York)")
    p.add_argument("--times", "-t", default=None,
                   help="Comma-separated HH:MM list (default: live agent's journal entries "
                        "for --date if any, else --times-fallback)")
    p.add_argument("--times-fallback", default="10:30,10:45,11:30",
                   help="HH:MM list to use when no journal entries exist (default: 10:30,10:45,11:30)")
    p.add_argument("--max-chop", type=int, default=5)
    p.add_argument("--regime-guard", action="store_true", default=False,
                   help="Enable regime guard (golden default: OFF)")
    p.add_argument("--no-pb-ema", action="store_true",
                   help="Disable PB EMA gate (golden default: ON 13/55)")
    return p.parse_args()


def parse_times(times_csv: str, day: date, tz) -> list[datetime]:
    out: list[datetime] = []
    for raw in times_csv.split(","):
        raw = raw.strip()
        if not raw:
            continue
        hh, mm = raw.split(":")
        out.append(datetime(day.year, day.month, day.day, int(hh), int(mm), tzinfo=tz))
    return out


def main() -> None:
    args = parse_args()

    et = ZoneInfo("America/New_York")
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = datetime.now(et).date()

    bars_full = load_bars(args.symbol)
    today_bars = bars_full[bars_full.index.date == target_date]
    print(f"{len(bars_full)} total bars; {len(today_bars)} for {target_date}.")
    if today_bars.empty:
        print(f"No bars for {target_date} in cache — re-run the live agent or replay for that date.")
        return

    if args.times is not None:
        time_strs = args.times
    else:
        jt = journal_times(args.symbol, target_date)
        time_strs = ",".join(jt) if jt else args.times_fallback
        src = "journal" if jt else "fallback"
        print(f"Times source: {src} → {time_strs}")

    # Use the actual bar-index tz to keep timezone math consistent.
    bar_tz = today_bars.index.tz
    times = parse_times(time_strs, target_date, bar_tz)

    # Snap each requested time to the most recent bar ≤ that time.
    snapped = []
    for t in times:
        avail = today_bars[today_bars.index <= t]
        snapped.append((t, avail.index[-1] if len(avail) else None))

    # Pull today's VIX (yfinance) so the live path's VIX matches what the replay used.
    try:
        import yfinance as yf
        vix_df = yf.download("^VIX", period="5d", interval="1d", progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        day_vix = float(vix_df["Close"].iloc[-1])
    except Exception as e:
        print(f"VIX fetch failed ({e}); defaulting to 20.0")
        day_vix = 20.0
    print(f"Using VIX = {day_vix:.2f}")

    print("\n" + "═" * 80)
    print("  LIVE PATH (updated check_sweet_spot, golden defaults)")
    print("═" * 80)

    def fake_fetch(symbol, days_back=1, interval="5min"):
        cutoff = _SliceState.slice_at
        return bars_full[bars_full.index <= cutoff] if cutoff is not None else bars_full

    def fake_vix() -> float:
        return day_vix

    def fake_get_et_now():
        return _SliceState.slice_at if _SliceState.slice_at is not None else datetime.now(et)

    pb_ema_on = not args.no_pb_ema
    with patch.object(agent, "alpaca_fetch_bars", fake_fetch), \
         patch.object(agent, "_fetch_current_vix", fake_vix), \
         patch.object(agent, "get_et_now", fake_get_et_now):
        for original_t, snapped_t in snapped:
            label = original_t.strftime("%H:%M")
            if snapped_t is None:
                print(f"\n[{label}] no bars available before this time — skipped")
                continue
            _SliceState.slice_at = snapped_t
            trig = agent.check_sweet_spot(
                args.symbol, max_chop=args.max_chop, regime_guard=args.regime_guard,
                pb_ema=pb_ema_on, pb_ema_fast=13, pb_ema_slow=55,
            )
            print(f"\n[{label} → snapped to {snapped_t.strftime('%H:%M')}]")
            if trig is None:
                # Walk the same gates manually to print which one failed.
                _why_rejected(snapped_t, bars_full, day_vix, args.symbol,
                              max_chop=args.max_chop, pb_ema=pb_ema_on)
            else:
                print(f"  ✅ TRIGGER: {trig['direction']} Q={trig['quality']} "
                      f"E={trig['explosion']} C={trig['chop']} Mom={trig['or_momentum']:+d} "
                      f"entry=${trig['entry']:.2f} stop=${trig['stop']:.2f} "
                      f"target=${trig['target']:.2f}")

    print("\n" + "═" * 80)
    print(f"  REPLAY PATH (replay_day on {target_date} bars)")
    print("═" * 80)
    prior_days = sorted({d for d in bars_full.index.date if d < target_date})
    prior_bars = bars_full[pd.Series(bars_full.index.date).isin(set(prior_days)).values] if prior_days else None
    triggers = replay_day(
        today_bars, target_date,
        max_chop=args.max_chop, min_cascade=2, min_quality=3, max_quality=7,
        breakout_pct=0.25, cooldown_bars=3,
        scan_end="13:59", scan_start="10:30",
        target_mult_low=1.0, target_mult_mid=1.5, target_mult_high=1.5,
        regime_guard=args.regime_guard, or_threshold=25, symbol=args.symbol,
        max_trades_per_day=3, max_stops_per_day=1, max_consecutive_losses=2,
        confirmation_bar=False, stagnation_bars=12, stagnation_threshold=0.3,
        gainz_exit=True, gainz_min_profit_r=0.3,
        cascade_sizing=True, cascade_size_low=3, cascade_size_mid=3, cascade_size_high=3,
        simulate_options=True, real_options=False,
        decay_aware_targets=True, decay_target_floor=0.4, decay_halflife_bars=8,
        active_range=True, active_range_bars=6, active_range_blend=0.25,
        pb_ema=pb_ema_on, pb_ema_fast=13, pb_ema_slow=55,
        vix=day_vix, prior_bars=prior_bars,
    )
    if not triggers:
        print(f"  No triggers from replay_day for {target_date}.")
    else:
        for t in triggers:
            print(f"  {t['time']}  {t['direction']:9s} Q={t['quality']} E={t['explosion']} "
                  f"C={t['chop']} Mom={t['momentum']:+d}  entry=${t['entry']:.2f} "
                  f"stop=${t['stop']:.2f} target=${t['target']:.2f}  → {t['outcome']}")


def _why_rejected(snapped_t, bars_full, day_vix, symbol: str = "SPY",
                  max_chop: int = 5, pb_ema: bool = True,
                  pb_ema_fast: int = 13, pb_ema_slow: int = 55):
    """Replicate check_sweet_spot's gates step-by-step and print which one rejects."""
    from src.opening_range import OpeningRangeAnalyzer
    from src.recent_momentum import RecentMomentumAnalyzer
    from src.momentum_cascade import MomentumCascadeDetector
    from src.utils.choppiness import compute_choppiness
    from src.utils.quality_scorer import compute_quality_score

    sliced = bars_full[bars_full.index <= snapped_t]
    ind = _build_indicators_from_bars(sliced, symbol=symbol)
    ind.vix = day_vix

    or_r = OpeningRangeAnalyzer().analyze(ind)
    if or_r is None:
        print("  ❌ rejected: OpeningRangeAnalyzer returned None")
        return
    bd = or_r.breakout_direction
    or_dir = bd.value if hasattr(bd, "value") else bd
    or_mom = or_r.momentum_score
    direction = "buy_call" if or_mom >= 25 else "buy_put" if or_mom <= -25 else None
    if direction is None:
        print(f"  ❌ rejected at direction: or_momentum={or_mom:+d} below ±25 threshold")
        return

    rc = RecentMomentumAnalyzer().analyze(ind)
    rec_dir, rec_mom = (rc.direction, rc.momentum_score) if rc else ("neutral", 0)

    h, l, c, v = (sliced["High"].astype(float), sliced["Low"].astype(float),
                  sliced["Close"].astype(float), sliced["Volume"].astype(float))
    typical = (h + l + c) / 3
    cumv = float(v.cumsum().iloc[-1])
    vwap_val = float((typical * v).cumsum().iloc[-1] / cumv) if cumv > 0 else None

    qr = compute_quality_score(
        direction=direction, current_price=ind.current_price,
        sma_20=ind.sma_20, sma_50=ind.sma_50, vix=ind.vix,
        volume=1.0, volume_sma_20=1.0,
        or_direction=or_dir, or_momentum=or_mom,
        or_confirmed=abs(or_mom) >= 40,
        recent_dir=rec_dir, recent_momentum=rec_mom,
        zlema_trend=ind.zlema_trend, vwap=vwap_val,
    )
    cascade = MomentumCascadeDetector().analyze(
        ind, quality_score=qr.score, or_momentum=or_mom, recent_momentum=rec_mom,
    )
    chop = compute_choppiness(sliced, vix=ind.vix, atr=ind.atr_14)

    print(f"  → dir={direction}  Q={qr.score}  E={cascade.explosion_score}  "
          f"C={chop.chop_score}  Mom={or_mom:+d}  Recent={rec_mom:+d}  "
          f"price=${ind.current_price:.2f}  range=[{or_r.range_low:.2f},{or_r.range_high:.2f}]")

    if not (3 <= qr.score <= 7):
        print(f"  ❌ rejected at quality: {qr.score} not in [3,7]")
        return
    if cascade.explosion_score < 2:
        print(f"  ❌ rejected at cascade: explosion={cascade.explosion_score} < 2")
        return
    if chop.chop_score > max_chop:
        print(f"  ❌ rejected at chop: {chop.chop_score} > {max_chop}")
        return

    # PB EMA
    close = sliced["Close"].astype(float)
    if pb_ema and len(sliced) >= pb_ema_slow:
        ema_f = float(close.ewm(span=pb_ema_fast, adjust=False).mean().iloc[-1])
        ema_s = float(close.ewm(span=pb_ema_slow, adjust=False).mean().iloc[-1])
        bhi, blo = max(ema_f, ema_s), min(ema_f, ema_s)
        if blo < ind.current_price < bhi:
            print(f"  ❌ rejected at PB EMA inside-band: price ${ind.current_price:.2f} in band [{blo:.2f}, {bhi:.2f}]")
            return

    rh, rl = or_r.range_high, or_r.range_low
    rw = rh - rl
    bt = rw * 0.25
    if direction == "buy_call" and ind.current_price < (rh - bt):
        print(f"  ❌ rejected at entry confirmation: price ${ind.current_price:.2f} < {rh - bt:.2f} (upper 25% of OR)")
        return
    if direction == "buy_put" and ind.current_price > (rl + bt):
        print(f"  ❌ rejected at entry confirmation: price ${ind.current_price:.2f} > {rl + bt:.2f} (lower 25% of OR)")
        return

    # Range / risk reject
    AR_BARS, AR_BLEND = 6, 0.25
    if len(sliced) >= AR_BARS:
        rb = sliced.iloc[-AR_BARS:]
        ah, al = float(rb["High"].max()), float(rb["Low"].min())
        if ah - al > 0:
            bhi2 = rh * (1 - AR_BLEND) + ah * AR_BLEND
            blo2 = rl * (1 - AR_BLEND) + al * AR_BLEND
        else:
            bhi2, blo2 = rh, rl
    else:
        bhi2, blo2 = rh, rl
    mid = (bhi2 + blo2) / 2
    width = bhi2 - blo2
    if direction == "buy_call":
        stop = mid + 0.10 * width
        risk = ind.current_price - stop
    else:
        stop = mid - 0.10 * width
        risk = stop - ind.current_price
    if risk <= 0:
        print(f"  ❌ rejected at risk≤0: entry={ind.current_price:.2f} stop={stop:.2f} risk={risk:.4f}")
        return
    print(f"  ⚠️ all gates passed — investigate why no trigger emitted "
          f"(entry={ind.current_price:.2f} stop={stop:.2f} risk={risk:.4f})")


if __name__ == "__main__":
    main()
