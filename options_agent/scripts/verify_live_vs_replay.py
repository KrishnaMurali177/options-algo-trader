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
from datetime import date, datetime, timedelta, timezone
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


def load_bars(symbol: str, target_date: date | None = None) -> pd.DataFrame:
    """Load bars for verification.

    Priority:
    1. Bar snapshot from journal (exact bars the agent used at trade time)
    2. Cached *_5min_*d.parquet files (largest first)
    """
    # Try bar snapshots from journal if target_date is provided
    if target_date:
        bars_dir = _JOURNAL_DIR / "bars"
        if bars_dir.exists():
            snapshots = sorted(bars_dir.glob(f"{target_date.isoformat()}_{symbol}_*_bars.parquet"))
            if snapshots:
                src = snapshots[0]
                df = pd.read_parquet(src)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                df.index = df.index.tz_convert("America/New_York")
                print(f"Loaded bars from snapshot: {src.name}")
                return df

    # Fallback to cached parquet. Prefer the 7d cache when it covers the target
    # date (fast, most-recent), otherwise fall through to larger caches.
    candidates = sorted(
        _CACHE_DIR.glob(f"{symbol}_5min_*d.parquet"),
        key=lambda p: int(p.stem.rsplit("_", 1)[-1].rstrip("d")),
    )
    if not candidates:
        raise FileNotFoundError(
            f"No cached 5-min bars for {symbol} in {_CACHE_DIR}. "
            f"Run the live agent or replay once to populate the cache."
        )

    def _covers(src: Path) -> bool:
        if target_date is None:
            return True
        df = pd.read_parquet(src)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df_local = df.index.tz_convert("America/New_York")
        # Need bars from target_date past ~14:00 ET — otherwise the cache was
        # written mid-day and the full scan window (10:30-13:59) isn't covered.
        target_bars = df_local[df_local.date == target_date]
        if len(target_bars) == 0:
            return False
        last_bar_min = target_bars.hour.max() * 60 + target_bars.minute.max()
        return last_bar_min >= 14 * 60  # 14:00 ET

    src = next((c for c in candidates if _covers(c)), candidates[-1])
    df = pd.read_parquet(src)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("America/New_York")
    print(f"Loaded bars from {src.relative_to(_REPO_ROOT)}")
    return df


def journal_times(symbol: str, day: date) -> list[str]:
    """Return list of HH:MM strings the live agent fired at on `day` for `symbol`."""
    # Read both legacy `<date>.json` and per-symbol `<date>_<SYM>.json`.
    candidates = [_JOURNAL_DIR / f"{day.isoformat()}.json",
                  _JOURNAL_DIR / f"{day.isoformat()}_{symbol}.json"]
    times: list[str] = []
    for f in candidates:
        if not f.exists():
            continue
        try:
            rows = json.loads(f.read_text())
        except Exception:
            continue
        for r in rows:
            if r.get("discard"):
                continue
            if r.get("symbol") == symbol and r.get("time"):
                times.append(r["time"])
    return times


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
    p.add_argument("--min-chop", type=int, default=2,
                   help="Min choppiness floor (golden: 2)")
    p.add_argument("--regime-guard", action="store_true", default=False,
                   help="Enable regime guard (golden default: OFF)")
    p.add_argument("--no-pb-ema", action="store_true",
                   help="Disable PB EMA gate (golden default: ON 13/55)")
    p.add_argument("--all-bars", action="store_true",
                   help="Compare LIVE and REPLAY per-bar verdicts across every 5-min "
                        "bar in [scan-start, scan-end], not just --times. Surfaces drift "
                        "that the trigger-only mode would miss.")
    p.add_argument("--scan-start", type=str, default="10:30",
                   help="--all-bars start (HH:MM ET, golden 10:30)")
    p.add_argument("--scan-end-time", type=str, default="13:59",
                   help="--all-bars end (HH:MM ET, golden 13:59)")
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

    bars_full = load_bars(args.symbol, target_date=target_date)
    # Limit to 5 calendar days back (matching live agent's days_back=5)
    window_start = target_date - timedelta(days=5)
    bars_full = bars_full[bars_full.index.date >= window_start]
    today_bars = bars_full[bars_full.index.date == target_date]
    print(f"{len(bars_full)} total bars; {len(today_bars)} for {target_date}.")
    if today_bars.empty:
        print(f"No bars for {target_date} in cache — re-run the live agent or replay for that date.")
        return

    if args.all_bars:
        # All-bars timeline: every 5-min bar between scan_start and scan_end.
        # Lets us see per-bar drift (today's discovery: live vs replay differ
        # on dozens of intermediate bars, not just the trigger times).
        bar_tz = today_bars.index.tz
        ss_h, ss_m = (int(x) for x in args.scan_start.split(":"))
        se_h, se_m = (int(x) for x in args.scan_end_time.split(":"))
        ss = datetime(target_date.year, target_date.month, target_date.day, ss_h, ss_m, tzinfo=bar_tz)
        se = datetime(target_date.year, target_date.month, target_date.day, se_h, se_m, tzinfo=bar_tz)
        time_strs = ",".join(
            ts.strftime("%H:%M") for ts in today_bars.index
            if ss <= ts.to_pydatetime() <= se
        )
        print(f"Times source: all-bars [{args.scan_start} → {args.scan_end_time}] → {time_strs.count(',') + 1} bars")
    elif args.times is not None:
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
        # Mirror src/utils/alpaca_data.py:_drop_partial_trailing_bar — the live
        # agent never sees the in-progress bar. A "5min" bar with open ts=13:55
        # is in-progress for any wall-clock t in [13:55, 14:00). Without this,
        # the verify-side "live" path reads one bar AHEAD of the real live agent
        # and Q-scores diverge by ~1 (see SPY 2026-05-15 13:55 incident).
        cutoff = _SliceState.slice_at
        if cutoff is None:
            return bars_full
        sliced = bars_full[bars_full.index <= cutoff]
        if len(sliced) == 0:
            return sliced
        bar_sec = {"5min": 300, "15min": 900, "1hour": 3600}.get(interval, 300)
        last_ts = sliced.index[-1]
        if last_ts.tz is None:
            last_ts = last_ts.tz_localize("UTC")
        age = (cutoff - last_ts.to_pydatetime()).total_seconds()
        if age < bar_sec:
            sliced = sliced.iloc[:-1]
        return sliced

    def fake_vix() -> float:
        return day_vix

    def fake_get_et_now():
        return _SliceState.slice_at if _SliceState.slice_at is not None else datetime.now(et)

    pb_ema_on = not args.no_pb_ema
    # Per-bar verdict capture for --all-bars mode (used by the diff section below).
    live_verdicts: dict[str, dict] = {}
    with patch.object(agent, "alpaca_fetch_bars", fake_fetch), \
         patch.object(agent, "_fetch_current_vix", fake_vix), \
         patch.object(agent, "get_et_now", fake_get_et_now):
        for original_t, snapped_t in snapped:
            label = original_t.strftime("%H:%M")
            if snapped_t is None:
                print(f"\n[{label}] no bars available before this time — skipped")
                continue
            # Simulate the live agent's POST-FIX wake time: bar T + bar_sec + buffer.
            # At that wake, fake_fetch sees age = bar_sec + buffer ≥ bar_sec, so the
            # bar with open=T is KEPT (not dropped as partial). This is exactly what
            # the live agent now does after the bar-close alignment fix.
            _SliceState.slice_at = snapped_t + timedelta(seconds=305)
            trig = agent.check_sweet_spot(
                args.symbol, max_chop=args.max_chop, min_chop=args.min_chop,
                regime_guard=args.regime_guard,
                pb_ema=pb_ema_on, pb_ema_fast=13, pb_ema_slow=55,
            )
            print(f"\n[{label} → snapped to {snapped_t.strftime('%H:%M')}]")
            if trig is None or trig.get("status") == "reject":
                reason = trig.get("reason", "unknown") if trig else "returned None"
                stage = trig.get("stage", "?") if trig else "?"
                print(f"  ❌ rejected at {stage}: {reason}")
                _why_rejected(snapped_t, bars_full, day_vix, args.symbol,
                              max_chop=args.max_chop, min_chop=args.min_chop,
                              pb_ema=pb_ema_on)
            else:
                print(f"  ✅ TRIGGER: {trig['direction']} Q={trig['quality']} "
                      f"E={trig['explosion']} C={trig['chop']} Mom={trig['or_momentum']:+d} "
                      f"entry=${trig['entry']:.2f} stop=${trig['stop']:.2f} "
                      f"target=${trig['target']:.2f}")
            if args.all_bars and trig is not None:
                # Normalize: triggers have no `stage` field; use "trigger" so the
                # drift-diff sees the same value the replay-side helper emits.
                _stage = trig.get("stage") if trig.get("status") == "reject" else "trigger"
                live_verdicts[label] = {
                    "status": trig.get("status"),
                    "stage": _stage,
                    "q": trig.get("quality"),
                    "e": trig.get("explosion"),
                    "c": trig.get("chop"),
                    "or_mom": trig.get("or_momentum"),
                }

    print("\n" + "═" * 80)
    print(f"  REPLAY PATH (replay_day on {target_date} bars)")
    print("═" * 80)
    # Prior bars: only dates within the 5-calendar-day window before target
    prior_bars = bars_full[bars_full.index.date < target_date]
    prior_bars = prior_bars if len(prior_bars) > 0 else None
    # Kwargs MUST mirror the golden defaults in replay_sweet_spot.py main().
    # When a default flips (e.g. cooldown_bars 2→1, golden 2026-05-18), update both.
    triggers = replay_day(
        today_bars, target_date,
        max_chop=args.max_chop, min_chop=args.min_chop,
        min_cascade=2, min_quality=3, max_quality=7,
        breakout_pct=0.25, cooldown_bars=1,
        scan_end="13:59", scan_start="10:30",
        target_mult_low=1.0, target_mult_mid=1.5, target_mult_high=1.5,
        regime_guard=args.regime_guard, or_threshold=25, symbol=args.symbol,
        max_trades_per_day=4, max_stops_per_day=1, max_consecutive_losses=2,
        confirmation_bar=False, stagnation_bars=12, stagnation_threshold=0.3,
        gainz_exit=True, gainz_min_profit_r=0.3,
        cascade_sizing=True, cascade_size_low=3, cascade_size_mid=3, cascade_size_high=3,
        simulate_options=True, real_options=True,
        decay_aware_targets=True, decay_target_floor=0.4, decay_halflife_bars=8,
        active_range=True, active_range_bars=6, active_range_blend=0.25,
        pb_ema=pb_ema_on, pb_ema_fast=13, pb_ema_slow=55,
        tiered_stagnation=True, tiered_stag_early_bar=8,
        tiered_stag_pnl_lo=-0.1, tiered_stag_pnl_hi=0.2, stag_cooldown_bars=1,
        momentum_flip=True, momentum_flip_threshold=40.0, max_flip_trades=1,
        vix=day_vix, prior_bars=prior_bars,
    )
    if not triggers:
        print(f"  No triggers from replay_day for {target_date}.")
    else:
        for t in triggers:
            print(f"  {t['time']}  {t['direction']:9s} Q={t['quality']} E={t['explosion']} "
                  f"C={t['chop']} Mom={t['momentum']:+d}  entry=${t['entry']:.2f} "
                  f"stop=${t['stop']:.2f} target=${t['target']:.2f}  → {t['outcome']}")

    # ── ALL-BARS DRIFT TABLE ───────────────────────────────────────────────
    if args.all_bars:
        print("\n" + "═" * 80)
        print("  PER-BAR DRIFT (LIVE vs REPLAY at each 5-min bar)")
        print("═" * 80)
        # Build replay-side per-bar verdicts using the SAME slice the live path used.
        # If they agree on Q/E/chop/or_mom at every bar, parity is confirmed.
        replay_verdicts: dict[str, dict] = {}
        for original_t, snapped_t in snapped:
            if snapped_t is None:
                continue
            label = original_t.strftime("%H:%M")
            replay_verdicts[label] = _replay_verdict_for_bar(
                snapped_t, bars_full, day_vix, args.symbol,
                max_chop=args.max_chop, min_chop=args.min_chop,
                pb_ema=pb_ema_on,
            )
        print(f"{'Time':<7}{'LIVE Q/E/C om/stage':<35}{'REPLAY Q/E/C om/stage':<35}{'drift':<20}")
        drifts = 0
        for label in sorted(set(live_verdicts) | set(replay_verdicts)):
            lv = live_verdicts.get(label)
            rv = replay_verdicts.get(label)
            def _fmt(d):
                if not d:
                    return "—"
                q, e, c = d.get("q"), d.get("e"), d.get("c")
                om = d.get("or_mom")
                stage = d.get("stage") or d.get("status") or "?"
                qs = q if q is not None else "?"
                es = e if e is not None else "?"
                cs = c if c is not None else "?"
                oms = f"{om:+}" if om is not None else "?"
                return f"{qs}/{es}/{cs} {oms} [{stage}]"
            drift = ""
            if lv and rv:
                diff_keys = []
                for k in ("q", "e", "c", "or_mom", "stage"):
                    if lv.get(k) != rv.get(k):
                        diff_keys.append(k)
                if diff_keys:
                    drift = "Δ" + ",".join(diff_keys)
                    drifts += 1
            print(f"{label:<7}{_fmt(lv):<35}{_fmt(rv):<35}{drift:<20}")
        total = len([t for t in (set(live_verdicts) | set(replay_verdicts))
                     if t in live_verdicts and t in replay_verdicts])
        print(f"\n  Drifted bars: {drifts}/{total}")
        if drifts == 0:
            print("  ✅ Bit-exact parity at every bar.")
        else:
            print(f"  ⚠ {drifts} bar(s) diverge — investigate indicator builders or input slices.")


def _replay_verdict_for_bar(snapped_t, bars_full, day_vix, symbol: str = "SPY",
                            max_chop: int = 5, min_chop: int = 2, pb_ema: bool = True,
                            pb_ema_fast: int = 13, pb_ema_slow: int = 55) -> dict:
    """Run the replay-equivalent gate chain on bars sliced ≤ snapped_t.

    Returns the same shape as the live verdict dict: status, stage, q, e, c, or_mom.
    Uses `_build_indicators_from_bars` (the replay's standalone builder) so any
    drift vs `check_sweet_spot` localizes to the indicator builders.
    """
    from src.opening_range import OpeningRangeAnalyzer
    from src.recent_momentum import RecentMomentumAnalyzer
    from src.momentum_cascade import MomentumCascadeDetector
    from src.utils.choppiness import compute_choppiness
    from src.utils.quality_scorer import compute_quality_score

    sliced = bars_full[bars_full.index <= snapped_t]
    if len(sliced) == 0:
        return {"status": "reject", "stage": "data", "q": None, "e": None, "c": None, "or_mom": None}
    # Use the SAME builder the live agent uses (which matches the replay's inline
    # per-bar computation). Building with `_build_indicators_from_bars` would
    # introduce drift on RSI/MACD/ATR/BB/ZLEMA — those use today-only intraday
    # bars in the live + replay scan loops, but use multi-day in the standalone
    # builder. Standalone is for one-shot reports, not parity testing.
    sliced_today = sliced[sliced.index.date == snapped_t.date()]
    if len(sliced_today) == 0:
        return {"status": "reject", "stage": "data", "q": None, "e": None, "c": None, "or_mom": None}
    ind = agent._build_indicators_replay_parity(sliced, sliced_today, symbol, day_vix)
    or_r = OpeningRangeAnalyzer().analyze(ind, bars_5m=sliced_today)
    if or_r is None:
        return {"status": "reject", "stage": "or", "q": None, "e": None, "c": None, "or_mom": None}
    or_mom = or_r.momentum_score
    direction = "buy_call" if or_mom >= 25 else "buy_put" if or_mom <= -25 else None
    if direction is None:
        return {"status": "reject", "stage": "direction", "q": None, "e": None, "c": None, "or_mom": or_mom}
    rc = RecentMomentumAnalyzer().analyze(ind, bars_5m=sliced_today)
    rec_dir, rec_mom = (rc.direction, rc.momentum_score) if rc else ("neutral", 0)
    # Momentum flip — mirror check_sweet_spot and replay_day (both apply flip
    # BEFORE scoring quality, so the new direction's score is what counts).
    # Threshold mirrors golden default `momentum_flip_threshold=40`.
    _is_flip = False
    if direction == "buy_call" and rec_mom <= -40:
        direction = "buy_put"
        _is_flip = True
    elif direction == "buy_put" and rec_mom >= 40:
        direction = "buy_call"
        _is_flip = True
    h, l, c, v = (sliced["High"].astype(float), sliced["Low"].astype(float),
                  sliced["Close"].astype(float), sliced["Volume"].astype(float))
    typical = (h + l + c) / 3
    cumv = float(v.cumsum().iloc[-1])
    vwap_val = float((typical * v).cumsum().iloc[-1] / cumv) if cumv > 0 else None
    bd = or_r.breakout_direction
    or_dir = bd.value if hasattr(bd, "value") else bd
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
        bars_5m=sliced_today,
    )
    chop = compute_choppiness(sliced_today, vix=ind.vix, atr=ind.atr_14)
    # Determine the first failing gate (matches check_sweet_spot ordering at run_sweet_spot_agent.py:414-475).
    stage = "trigger"
    if not (3 <= qr.score <= 7):
        stage = "quality"
    elif cascade.explosion_score < 2:
        stage = "cascade"
    elif chop.chop_score > max_chop:
        stage = "chop"
    elif chop.chop_score < min_chop:
        stage = "chop"
    else:
        # PB EMA inside-band reject
        if pb_ema and len(sliced_today) >= pb_ema_slow:
            close = sliced_today["Close"].astype(float)
            ema_f = float(close.ewm(span=pb_ema_fast, adjust=False).mean().iloc[-1])
            ema_s = float(close.ewm(span=pb_ema_slow, adjust=False).mean().iloc[-1])
            bhi, blo = max(ema_f, ema_s), min(ema_f, ema_s)
            if blo < ind.current_price < bhi:
                stage = "pb_ema"
        if stage == "trigger" and not _is_flip:
            # Entry-zone (25%) reject. Live/replay skip this for flip trades
            # because a flip means reversal from the opposite zone, so price is
            # naturally in the "wrong" zone for the new direction.
            rw = or_r.range_high - or_r.range_low
            bt = rw * 0.25
            if direction == "buy_call" and ind.current_price < (or_r.range_high - bt):
                stage = "entry_zone"
            elif direction == "buy_put" and ind.current_price > (or_r.range_low + bt):
                stage = "entry_zone"
    return {
        "status": "trigger" if stage == "trigger" else "reject",
        "stage": stage,
        "q": qr.score, "e": cascade.explosion_score, "c": chop.chop_score,
        "or_mom": or_mom,
    }


def _why_rejected(snapped_t, bars_full, day_vix, symbol: str = "SPY",
                  max_chop: int = 5, min_chop: int = 2, pb_ema: bool = True,
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

    sliced_today = sliced[sliced.index.date == snapped_t.date()]
    or_r = OpeningRangeAnalyzer().analyze(ind, bars_5m=sliced_today)
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

    rc = RecentMomentumAnalyzer().analyze(ind, bars_5m=sliced_today)
    rec_dir, rec_mom = (rc.direction, rc.momentum_score) if rc else ("neutral", 0)

    # Momentum flip — mirror check_sweet_spot and replay_day before scoring Q.
    if direction == "buy_call" and rec_mom <= -40:
        direction = "buy_put"
    elif direction == "buy_put" and rec_mom >= 40:
        direction = "buy_call"

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
    if chop.chop_score < min_chop:
        print(f"  ❌ rejected at min chop: {chop.chop_score} < {min_chop}")
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
