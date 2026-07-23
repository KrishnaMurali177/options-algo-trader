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
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import yfinance as yf

from src.utils.alpaca_data import fetch_bars as alpaca_fetch_bars, is_trading_day
from src.opening_range import OpeningRangeAnalyzer
from src.recent_momentum import RecentMomentumAnalyzer
from src.momentum_cascade import MomentumCascadeDetector
from src.models.market_data import MarketIndicators
from src.utils.quality_scorer import compute_quality_score
from src.utils.choppiness import compute_choppiness
from src.utils.gainz import gainz_signal
from src.utils.trade_notifier import TradeNotifier
from src.utils.alpaca_fills import enrich_trades_with_fills


def _build_indicators_replay_parity(
    extended_bars: pd.DataFrame,
    day_bars: pd.DataFrame,
    symbol: str,
    vix: float,
) -> MarketIndicators:
    """Build MarketIndicators using the exact construction the replay's scan loop uses.

    SMA-50 / SMA-200 come from `extended_bars` (multi-day) so they have warmup;
    RSI / MACD / ATR / BB / ZLEMA / volume come from `day_bars` (today only).
    Mirrors lines 344-405 of replay_sweet_spot.py.
    """
    n = len(day_bars)
    if n == 0:
        raise ValueError("day_bars is empty — cannot build indicators")

    day_close = day_bars["Close"].astype(float)
    day_high = day_bars["High"].astype(float)
    day_low = day_bars["Low"].astype(float)
    day_vol = day_bars["Volume"].astype(float)
    ext_close = extended_bars["Close"].astype(float)
    ext_n = len(ext_close)
    price = float(day_close.iloc[-1])

    # RSI (intraday, today only)
    if n >= 15:
        delta = day_close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))
        rsi_val = float(rsi_series.iloc[-1])
        if np.isnan(rsi_val):
            rsi_val = 50.0
    else:
        rsi_val = 50.0

    # SMAs: 20 from today, 50/200 from extended
    sma_20 = float(day_close.iloc[-20:].mean()) if n >= 20 else price
    sma_50 = float(ext_close.iloc[-50:].mean()) if ext_n >= 50 else float(ext_close.mean())
    sma_200 = float(ext_close.iloc[-200:].mean()) if ext_n >= 200 else float(ext_close.mean())

    # Bollinger Bands (today only, 20-period)
    if n >= 20:
        bb_mid = float(day_close.rolling(20).mean().iloc[-1])
        bb_std_val = float(day_close.rolling(20).std().iloc[-1])
        if np.isnan(bb_mid):
            bb_mid = price
            bb_std_val = 0.0
    else:
        bb_mid = price
        bb_std_val = 0.0
    bb_upper = bb_mid + 2 * bb_std_val
    bb_lower = bb_mid - 2 * bb_std_val

    # MACD (today only, 12/26/9)
    if n >= 26:
        ema12 = day_close.ewm(span=12, adjust=False).mean()
        ema26 = day_close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal_line
        macd_val = float(macd_line.iloc[-1])
        macd_sig = float(signal_line.iloc[-1])
        macd_hist = float(hist.iloc[-1])
    else:
        macd_val = macd_sig = macd_hist = 0.0

    # ATR (today only, 14-period)
    if n >= 15:
        tr = pd.concat(
            [day_high - day_low,
             (day_high - day_close.shift()).abs(),
             (day_low - day_close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr_val = float(tr.rolling(14).mean().iloc[-1])
        if np.isnan(atr_val):
            atr_val = 1.0
    else:
        atr_val = 1.0

    # Volume
    current_volume = int(day_vol.iloc[-1])
    vol_sma_val = float(day_vol.rolling(min(20, n)).mean().iloc[-1])
    if np.isnan(vol_sma_val):
        vol_sma_val = float(current_volume)

    # ZLEMA 8/21
    if n >= 21:
        lag_fast = (8 - 1) // 2
        lag_slow = (21 - 1) // 2
        comp_fast = 2 * day_close - day_close.shift(lag_fast)
        comp_slow = 2 * day_close - day_close.shift(lag_slow)
        zf = float(comp_fast.ewm(span=8, adjust=False).mean().iloc[-1])
        zs = float(comp_slow.ewm(span=21, adjust=False).mean().iloc[-1])
        if zf > zs * 1.0002:
            zlema_trend = "bullish"
        elif zf < zs * 0.9998:
            zlema_trend = "bearish"
        else:
            zlema_trend = "neutral"
    else:
        zf = zs = price
        zlema_trend = "neutral"

    return MarketIndicators(
        symbol=symbol,
        timestamp=datetime.now(ZoneInfo("UTC")),
        current_price=price,
        timeframe="15min",
        vix=vix,
        rsi_14=rsi_val,
        rsi_5min=rsi_val,
        sma_20=sma_20,
        sma_50=sma_50,
        sma_200=sma_200,
        bb_upper=bb_upper,
        bb_middle=bb_mid,
        bb_lower=bb_lower,
        macd=macd_val,
        macd_signal=macd_sig,
        macd_histogram=macd_hist,
        atr_14=atr_val,
        volume=current_volume,
        volume_sma_20=vol_sma_val,
        zlema_fast=zf,
        zlema_slow=zs,
        zlema_trend=zlema_trend,
    )


def _fetch_current_vix() -> float:
    """Fetch the daily VIX close for today (ET), matching replay's vix_map lookup.

    Replay (replay_sweet_spot.py:1251-1256) keys yfinance daily VIX by each
    trading day's date and uses iloc[-1] equivalent via vix_map[today]. During
    live trading, today's daily VIX row is a partial candle that updates
    intraday — but replay sees that same evolving value if scored at the same
    moment, so we mirror it. Returns 20.0 (neutral) on failure.
    """
    try:
        vix_df = yf.download("^VIX", period="5d", interval="1d", progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        return float(vix_df["Close"].iloc[-1])
    except Exception as e:
        logger.warning("VIX fetch failed (%s) — defaulting to 20.0", e)
        return 20.0


def _check_gainz_exit(underlying_symbol: str, direction: str, body_ratio: float,
                      rsi_overbought: float, rsi_oversold: float) -> bool:
    """Return True if the latest completed 5-min bar fires an opposing Gainz signal.

    Always evaluated on the underlying's bars — Alpaca's bars endpoint does not
    accept OCC option symbols.
    """
    try:
        bars = alpaca_fetch_bars(underlying_symbol, days_back=1, interval="5min")
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
        logger.warning("Gainz exit check failed for %s: %s", underlying_symbol, e)
        return False

# ── Setup ──
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
JOURNAL_DIR = Path(__file__).resolve().parent.parent / "sweet_spot_journal"
JOURNAL_DIR.mkdir(exist_ok=True)

_SYMBOL_TAG = os.environ.get("AGENT_SYMBOL", "")

def _setup_logging(symbol: str = "") -> logging.Logger:
    tag = symbol or _SYMBOL_TAG or "agent"
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"sweet_spot_agent_{tag.lower()}_{today}.log"
    latest_link = LOG_DIR / f"sweet_spot_agent_{tag.lower()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{tag}] [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, mode="a"),
        ],
    )
    try:
        latest_link.unlink(missing_ok=True)
        latest_link.symlink_to(log_file.name)
    except OSError:
        pass
    return logging.getLogger(f"sweet_spot_agent_{tag}")

logger = _setup_logging()


def get_et_now() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


# Buffer (seconds) added after each 5-min bar close before scanning.
# Alpaca's bar endpoint typically finalizes a 5-min bar within a few seconds of
# its close timestamp; 5s is conservative.
_BAR_CLOSE_BUFFER_SEC = 5


# FOMC meeting END dates (rate decision days, 2:00 PM ET presser). Public schedule.
# Golden 2026-06-04: skip these days — both symbols, both windows clean sweep.
# Mirror of FOMC_DATES in replay_sweet_spot.py — keep in sync.
FOMC_DATES: set[str] = {
    "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29",
    "2026-06-17", "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
}


def seconds_until_next_bar_close(now_et: datetime | None = None, bar_sec: int = 300) -> float:
    """Seconds from now until the next 5-min bar close (+ buffer).

    Replay parity: replay_day scans bar-by-bar, with each scan using the slice
    of bars whose timestamps are ≤ the bar's open time. To match exactly, the
    live agent should also wake at each 5-min boundary AFTER the bar has closed
    and been published by Alpaca, then scan with the just-closed bar included.

    Without this alignment, the live agent's wall-clock scan (e.g. at HH:MM:08)
    reads the in-progress bar as the latest available, drops it via
    `_drop_partial_trailing_bar`, and ends up one bar behind replay.
    """
    now_et = now_et or get_et_now()
    seconds_into_day = now_et.hour * 3600 + now_et.minute * 60 + now_et.second + now_et.microsecond / 1e6
    next_boundary = (int(seconds_into_day // bar_sec) + 1) * bar_sec
    return next_boundary - seconds_into_day + _BAR_CLOSE_BUFFER_SEC


def is_market_hours() -> bool:
    now = get_et_now()
    if now.weekday() >= 5:
        return False
    return now.hour * 60 + now.minute >= 9 * 60 + 30 and now.hour < 16


def is_past_or(min_after_open: int = 60) -> bool:
    """Wait until at least 60 min after open (10:30) for OR to form."""
    now = get_et_now()
    minutes_since_open = (now.hour * 60 + now.minute) - (9 * 60 + 30)
    return minutes_since_open >= min_after_open


def check_sweet_spot(symbol: str, max_chop: int = 5, min_chop: int = 2,
                     regime_guard: bool = True,
                     pb_ema: bool = True, pb_ema_fast: int = 13, pb_ema_slow: int = 55,
                     prior_triggers: list[dict] | None = None,
                     cluster_penalty: bool = False,
                     cluster_penalty_window_min: int = 30,
                     cluster_penalty_cap: int = 2,
                     rsi_extreme_penalty: float = 30.0,
                     vwap_slope_override_t: float = 0.0,
                     vwap_slope_override_k: int = 0,
                     vwap_slope_override_cmax: float = 0.0,
                     dynamic_or: bool = True,
                     dynamic_or_threshold: float = 0.6) -> dict:
    """Evaluate sweet spot conditions. Always returns a verdict dict.

    Returns a dict with either:
      - {"status": "trigger", **trigger_payload}  on accept
      - {"status": "reject", "stage": str, "reason": str, ...diagnostics}  on reject

    Replay parity: indicators, OR/recent/cascade analyzers, and choppiness all
    derive from a single multi-day Alpaca 5-min bar pull, exactly the way
    replay_sweet_spot.py constructs them. No yfinance intraday fetches.
    """
    try:
        # ── Single source of truth: 5d of Alpaca 5-min bars ──
        # 5 calendar days gives ~3-4 prior trading days for SMA-50/200 warmup.
        extended_bars = alpaca_fetch_bars(symbol, days_back=5, interval="5min")
        if len(extended_bars) == 0:
            logger.warning("No bars returned from Alpaca for %s", symbol)
            return {"status": "reject", "stage": "data", "reason": "no bars from Alpaca"}
        today_et = get_et_now().date()
        day_bars = extended_bars[extended_bars.index.date == today_et]
        if len(day_bars) == 0:
            return {"status": "reject", "stage": "data", "reason": "no bars for today"}

        vix_val = _fetch_current_vix()
        indicators = _build_indicators_replay_parity(extended_bars, day_bars, symbol, vix_val)

        # ── Analyzers (all routed through bars_5m= path — same code as replay) ──
        or_result = OpeningRangeAnalyzer().analyze(indicators, bars_5m=day_bars)
        if or_result:
            _bd = or_result.breakout_direction
            or_direction = _bd.value if hasattr(_bd, "value") else _bd
        else:
            or_direction = "neutral"
        or_momentum = or_result.momentum_score if or_result else 0

        rc_result = RecentMomentumAnalyzer().analyze(indicators, bars_5m=day_bars)
        recent_dir = rc_result.direction if rc_result else "neutral"
        recent_momentum = rc_result.momentum_score if rc_result else 0

        direction = "buy_call" if or_momentum >= 25 else "buy_put" if or_momentum <= -25 else None
        if direction is None:
            return {"status": "reject", "stage": "direction",
                    "reason": f"OR momentum {or_momentum:+d} inside ±25 band",
                    "or_momentum": or_momentum, "recent_momentum": recent_momentum,
                    "price": indicators.current_price}

        # Momentum flip: when recent 30-min momentum strongly disagrees with OR direction,
        # flip the trade direction to follow recent momentum (golden default: threshold=40)
        _flip_threshold = 40.0
        _is_flip = False
        if direction == "buy_put" and recent_momentum >= _flip_threshold:
            logger.info("MOMENTUM FLIP: OR says %s (or_mom=%d) but recent_mom=%.0f → flipped to buy_call",
                        direction, or_momentum, recent_momentum)
            direction = "buy_call"
            _is_flip = True
        elif direction == "buy_call" and recent_momentum <= -_flip_threshold:
            logger.info("MOMENTUM FLIP: OR says %s (or_mom=%d) but recent_mom=%.0f → flipped to buy_put",
                        direction, or_momentum, recent_momentum)
            direction = "buy_put"
            _is_flip = True

        # Regime guard (off by golden default, mirror of replay logic)
        if regime_guard:
            bullish_regime = indicators.sma_20 > indicators.sma_50
            bearish_regime = indicators.sma_20 < indicators.sma_50
            if direction == "buy_put" and bullish_regime and indicators.rsi_14 <= 70:
                return {"status": "reject", "stage": "regime_guard",
                        "reason": f"PUT vetoed by bullish regime (sma20>{indicators.sma_50:.2f}, rsi {indicators.rsi_14:.1f}≤70)",
                        "direction": direction, "or_momentum": or_momentum, "price": indicators.current_price}
            if direction == "buy_call" and bearish_regime and indicators.rsi_14 >= 30:
                return {"status": "reject", "stage": "regime_guard",
                        "reason": f"CALL vetoed by bearish regime (sma20<{indicators.sma_50:.2f}, rsi {indicators.rsi_14:.1f}≥30)",
                        "direction": direction, "or_momentum": or_momentum, "price": indicators.current_price}

        # Real intraday VWAP from today's bars (cum typical-price × volume).
        vwap_val: float | None = None
        _h = day_bars["High"].astype(float)
        _l = day_bars["Low"].astype(float)
        _c = day_bars["Close"].astype(float)
        _v = day_bars["Volume"].astype(float)
        _typical = (_h + _l + _c) / 3
        _cumvol = float(_v.cumsum().iloc[-1])
        if _cumvol > 0:
            vwap_val = float((_typical * _v).cumsum().iloc[-1] / _cumvol)

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
            vwap=vwap_val,
        )
        quality = quality_result.score

        # Within-day clustering penalty (golden default cap=2, window=30 min).
        # Subtract 1 per prior same-direction trigger within the window, capped.
        # Promoted 2026-05-21 after 12-cell sensitivity grid: every (window, cap)
        # combination beat baseline; cap=2 was uniformly best. Bit-exact with replay.
        if cluster_penalty and prior_triggers:
            now_et = get_et_now()
            window_start = now_et - timedelta(minutes=cluster_penalty_window_min)
            same_dir_recent = 0
            for prior in prior_triggers:
                prior_hhmm = prior.get("time", "")
                if not prior_hhmm or len(prior_hhmm) < 5:
                    continue
                try:
                    prior_dt = now_et.replace(hour=int(prior_hhmm[:2]),
                                              minute=int(prior_hhmm[3:5]),
                                              second=0, microsecond=0)
                except ValueError:
                    continue
                if (prior_dt >= window_start and prior_dt < now_et
                        and prior.get("direction") == direction):
                    same_dir_recent += 1
            penalty = min(same_dir_recent, cluster_penalty_cap)
            quality -= penalty

        # ── RSI-extreme reject (golden 2026-06-02, replay parity) ──
        # Reject triggers entering directional exhaustion (RSI>=80 CALL / <=20 PUT).
        # 730d + 365d A/B: SPY Sharpe 2.73→3.04 / Calmar 12.46→16.70; QQQ 365d
        # MDD% 68.2→38.0. See strategy_rsi_extreme_promoted_2026_06_02 memory.
        # rsi_14 == 50.0 is the fallback default (no data) — treat as missing.
        if rsi_extreme_penalty > 0 and indicators.rsi_14 != 50.0:
            _sign = 1.0 if direction == "buy_call" else -1.0
            _rsi_pressure = _sign * (indicators.rsi_14 - 50.0)
            if _rsi_pressure >= rsi_extreme_penalty:
                return {"status": "reject", "stage": "rsi_extreme",
                        "reason": f"{direction.replace('buy_', '').upper()} vetoed by RSI extreme "
                                  f"(rsi={indicators.rsi_14:.1f}, pressure={_rsi_pressure:.1f} "
                                  f">= {rsi_extreme_penalty:.1f})",
                        "direction": direction, "rsi_14": indicators.rsi_14,
                        "rsi_pressure": _rsi_pressure}

        cascade = MomentumCascadeDetector().analyze(
            indicators, quality_score=quality,
            or_momentum=or_momentum, recent_momentum=recent_momentum,
            bars_5m=day_bars,
        )

        # `bars` is used downstream for PB EMA + active-range; alias to today's bars.
        bars = day_bars
        chop = compute_choppiness(bars, vix=indicators.vix, atr=indicators.atr_14)

        # Sweet spot criteria
        diag_common = {"direction": direction, "or_momentum": or_momentum,
                       "quality": quality, "explosion": cascade.explosion_score,
                       "chop": chop.chop_score, "price": indicators.current_price,
                       "chop_breakdown": {
                           "ci_pts": chop.ci_pts, "rev_pts": chop.rev_pts,
                           "bar_ratio_pts": chop.bar_ratio_pts, "consec_pts": chop.consec_pts,
                           "ci": chop.choppiness_index, "reversal_pct": chop.direction_reversal_pct,
                           "bar_range_ratio": chop.bar_range_ratio, "max_consecutive": chop.max_consecutive,
                       }}
        if not (3 <= quality <= 7):
            return {"status": "reject", "stage": "quality",
                    "reason": f"quality {quality} outside sweet band [3,7]", **diag_common}
        if cascade.explosion_score < 2:
            return {"status": "reject", "stage": "cascade",
                    "reason": f"explosion {cascade.explosion_score} < 2", **diag_common}
        if chop.chop_score > max_chop:
            # ── VWAP-slope override (golden: T=0.7 / K=3 / Cmax=0.65) ──
            # Allow entry through chop gate IF directional drift vs session VWAP is
            # confirmed: (price-vwap)/atr >= T for buy_call (or <= -T for buy_put),
            # K consecutive bars on the correct side of VWAP, and chop.ci <= Cmax.
            # Mirror of replay logic in replay_sweet_spot.py:599-637; must stay bit-exact.
            chop_override_fired = False
            atr_val = float(indicators.atr_14) if indicators.atr_14 else 0.0
            if (vwap_slope_override_t > 0 and vwap_slope_override_k > 0
                    and vwap_slope_override_cmax > 0 and atr_val > 0
                    and len(day_bars) >= vwap_slope_override_k):
                _h2 = day_bars["High"].astype(float)
                _l2 = day_bars["Low"].astype(float)
                _c2 = day_bars["Close"].astype(float)
                _v2 = day_bars["Volume"].astype(float)
                tp2 = (_h2 + _l2 + _c2) / 3.0
                cv2 = _v2.cumsum()
                ctv2 = (tp2 * _v2).cumsum()
                cumvol_n = float(cv2.iloc[-1])
                if cumvol_n > 0:
                    vwap_now = float(ctv2.iloc[-1] / cumvol_n)
                    dist_atr = (indicators.current_price - vwap_now) / atr_val
                    closes_arr = _c2.iloc[-vwap_slope_override_k:].values
                    vwap_arr = (ctv2 / cv2).iloc[-vwap_slope_override_k:].values
                    if direction == "buy_call":
                        side_ok = bool((closes_arr > vwap_arr).all())
                        dist_ok = dist_atr >= vwap_slope_override_t
                    else:
                        side_ok = bool((closes_arr < vwap_arr).all())
                        dist_ok = -dist_atr >= vwap_slope_override_t
                    ci_ok = chop.choppiness_index <= vwap_slope_override_cmax
                    if side_ok and dist_ok and ci_ok:
                        chop_override_fired = True
                        logger.info("CHOP OVERRIDE (vwap-slope): dir=%s chop=%d ci=%.2f dist_atr=%.2f",
                                    direction, chop.chop_score, chop.choppiness_index, dist_atr)
            if not chop_override_fired:
                return {"status": "reject", "stage": "chop",
                        "reason": f"chop {chop.chop_score} > max {max_chop}", **diag_common}
        if chop.chop_score < min_chop:
            return {"status": "reject", "stage": "chop",
                    "reason": f"chop {chop.chop_score} < min {min_chop}", **diag_common}

        # ── PB EMA inside-band gate (golden: ON, 13/55) ──
        # Reject when price is *between* the fast/slow EMAs — PB EMA's
        # "no zone" state. Symmetric: does not block direction, only chop.
        if pb_ema and len(bars) >= pb_ema_slow:
            close = bars["Close"].astype(float)
            ema_f = float(close.ewm(span=pb_ema_fast, adjust=False).mean().iloc[-1])
            ema_s = float(close.ewm(span=pb_ema_slow, adjust=False).mean().iloc[-1])
            band_hi, band_lo = max(ema_f, ema_s), min(ema_f, ema_s)
            if band_lo < indicators.current_price < band_hi:
                return {"status": "reject", "stage": "pb_ema",
                        "reason": f"price ${indicators.current_price:.2f} inside EMA band [${band_lo:.2f},${band_hi:.2f}]",
                        **diag_common}

        now = get_et_now()
        range_high = or_result.range_high if or_result else indicators.current_price
        range_low = or_result.range_low if or_result else indicators.current_price
        range_width = range_high - range_low

        # ── Dynamic Opening Range override (golden 2026-05-23) ──
        # If the 30-min quick OR (09:30–09:59) was decisively broken by the 10:00 bar
        # (price moved >50% of quick range beyond either boundary), replace the
        # 60-min range with the 30-min range. If the break did NOT fire and we are
        # still before 10:30 ET, defer this scan — the 60-min OR isn't complete yet.
        # Mirror of replay_sweet_spot.py:248-275; must stay bit-exact for live-replay parity.
        if dynamic_or:
            decisive_fired = False
            quick_or_bars = day_bars.between_time("09:30", "09:59")
            if len(quick_or_bars) >= 6:
                quick_high = float(quick_or_bars["High"].max())
                quick_low = float(quick_or_bars["Low"].min())
                quick_width = quick_high - quick_low
                bars_at_10 = day_bars.between_time("10:00", "10:04")
                if len(bars_at_10) > 0 and quick_width > 0:
                    price_at_10 = float(bars_at_10["Close"].iloc[-1])
                    if (price_at_10 > quick_high + quick_width * dynamic_or_threshold
                            or price_at_10 < quick_low - quick_width * dynamic_or_threshold):
                        range_high = quick_high
                        range_low = quick_low
                        range_width = quick_width
                        decisive_fired = True
                        logger.info("DYNAMIC OR: decisive 10:00 break (price=%.2f, 30-min range %.2f-%.2f) — using 30-min OR",
                                    price_at_10, quick_low, quick_high)
            if not decisive_fired and now.hour * 60 + now.minute < 10 * 60 + 30:
                return {"status": "reject", "stage": "dynamic_or_defer",
                        "reason": "dynamic-OR: no decisive 10:00 break; deferring until 60-min OR closes at 10:30",
                        **diag_common}

        # ── Active range blend: 75% OR + 25% recent 30-min range (golden: 0.25) ──
        ACTIVE_RANGE_BLEND = 0.25
        ACTIVE_RANGE_BARS = 6
        if len(bars) >= ACTIVE_RANGE_BARS:
            recent_bars = bars.iloc[-ACTIVE_RANGE_BARS:]
            ar_high = float(recent_bars["High"].max())
            ar_low = float(recent_bars["Low"].min())
            ar_width = ar_high - ar_low
            if ar_width > 0:
                b = ACTIVE_RANGE_BLEND
                blend_high = range_high * (1 - b) + ar_high * b
                blend_low = range_low * (1 - b) + ar_low * b
            else:
                blend_high = range_high
                blend_low = range_low
        else:
            blend_high = range_high
            blend_low = range_low

        # ── Entry confirmation: price must be in upper/lower 25% of range or beyond ──
        # Skip for flip trades — reversal enters from the opposite zone by definition.
        if not _is_flip:
            breakout_threshold = range_width * 0.25
            if direction == "buy_call" and indicators.current_price < (range_high - breakout_threshold):
                return {"status": "reject", "stage": "entry_zone",
                        "reason": f"CALL price ${indicators.current_price:.2f} below upper-25% band (need ≥${range_high - breakout_threshold:.2f}, range ${range_low:.2f}-${range_high:.2f})",
                        **diag_common}
            if direction == "buy_put" and indicators.current_price > (range_low + breakout_threshold):
                return {"status": "reject", "stage": "entry_zone",
                        "reason": f"PUT price ${indicators.current_price:.2f} above lower-25% band (need ≤${range_low + breakout_threshold:.2f}, range ${range_low:.2f}-${range_high:.2f})",
                        **diag_common}

        # Target multiplier scales with cascade strength
        if cascade.explosion_score >= 8:
            target_mult = 1.5  # Explosive — let it run
        elif cascade.explosion_score >= 6:
            target_mult = 1.5  # Strong — extended target (was 1.25, tightened-stop golden)
        else:
            target_mult = 1.0  # Moderate — standard 1R

        # Replay parity: entry is the current price (the agent files a market order,
        # so this matches the real fill); the active-range blend governs only the
        # stop/target midpoint, never the entry level.
        entry = indicators.current_price
        if _is_flip:
            # Flip trades use pure active range (recent 30-min) for stop/target.
            # OR midpoint stop is nonsensical for reversals.
            recent_bars = bars.iloc[-ACTIVE_RANGE_BARS:] if len(bars) >= ACTIVE_RANGE_BARS else bars.iloc[-6:]
            flip_high = float(recent_bars["High"].max())
            flip_low = float(recent_bars["Low"].min())
            atr_val = indicators.atr_14 if indicators.atr_14 else 1.0
            if direction == "buy_call":
                stop = flip_low - 0.02 * atr_val
                risk = entry - stop
                target_1 = entry + risk * target_mult if risk > 0 else entry
            else:
                stop = flip_high + 0.02 * atr_val
                risk = stop - entry
                target_1 = entry - risk * target_mult if risk > 0 else entry
        elif direction == "buy_call":
            mid = (blend_high + blend_low) / 2
            stop = mid + 0.10 * (blend_high - blend_low)
            risk = entry - stop
            target_1 = entry + risk * target_mult
        else:
            mid = (blend_high + blend_low) / 2
            stop = mid - 0.10 * (blend_high - blend_low)
            risk = stop - entry
            target_1 = entry - risk * target_mult
        # Replay parity: reject if price sits on the wrong side of the stop midpoint.
        if risk <= 0:
            return {"status": "reject", "stage": "risk",
                    "reason": f"non-positive risk ${risk:.2f} (entry ${entry:.2f} on wrong side of stop ${stop:.2f})",
                    **diag_common}

        # Capture indicator snapshot for reproducibility verification
        _ema_f = _ema_s = None
        if pb_ema and len(bars) >= pb_ema_slow:
            _close = bars["Close"].astype(float)
            _ema_f = round(float(_close.ewm(span=pb_ema_fast, adjust=False).mean().iloc[-1]), 4)
            _ema_s = round(float(_close.ewm(span=pb_ema_slow, adjust=False).mean().iloc[-1]), 4)

        return {
            "status": "trigger",
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
            "is_flip": _is_flip,
            "indicators": {
                "sma_20": round(indicators.sma_20, 4),
                "sma_50": round(indicators.sma_50, 4),
                "ema_pb_fast": _ema_f,
                "ema_pb_slow": _ema_s,
                "rsi_14": round(indicators.rsi_14, 2),
                "atr_14": round(indicators.atr_14, 4),
                "vix": indicators.vix,
                "zlema_trend": indicators.zlema_trend,
                "or_direction": or_direction,
                "recent_dir": recent_dir,
                "num_bars_today": len(day_bars),
                "num_bars_extended": len(extended_bars),
            },
            "_bars": extended_bars,
        }

    except Exception as e:
        logger.error("Sweet spot check failed: %s", e)
        return {"status": "reject", "stage": "error", "reason": str(e)}


def run_day(symbol: str, qty: int, max_chop: int, paper_trade: bool,
            min_chop: int = 2,
            max_trades_per_day: int = 4, max_stops_per_day: int = 1,
            max_consecutive_losses: int = 2,
            scan_start_min: int = 60,
            scan_end: str = "13:59",
            gainz_exit: bool = True,
            gainz_body_ratio: float = 0.7,
            gainz_rsi_overbought: float = 70.0,
            gainz_rsi_oversold: float = 30.0,
            gainz_min_profit_r: float = 0.3,
            contracts: int = 1,
            target_delta: float = 0.50,
            cascade_size_low: int = 3,
            cascade_size_mid: int = 3,
            cascade_size_high: int = 3,
            regime_guard: bool = False,
            vix_max: float = 30.0,
            vix_spike_pct: float = 20.0,
            skip_fomc: bool = True,
            skip_failed_bounce: bool = False,
            failed_bounce_gap_max: float = 0.50,
            failed_bounce_close_pos_max: float = 0.20,
            pb_ema: bool = True,
            pb_ema_fast: int = 13,
            pb_ema_slow: int = 55,
            dynamic_or: bool = True,
            dynamic_or_threshold: float = 0.6,
            dte: int = 0,
            verbose_rejects: bool = False):
    """Run the agent for one trading day."""
    today = date.today()
    # Per-symbol journal so concurrent SPY/QQQ/VOO agents don't clobber each other's writes.
    # When dte>0, suffix with "_{dte}dte" to keep 1DTE/2DTE strategy state isolated from
    # the legacy 0DTE journal (which keeps its unprefixed filename for backward-compat
    # of restart-recovery on the existing live 0DTE agent).
    if dte > 0:
        journal_file = JOURNAL_DIR / f"{today.isoformat()}_{symbol}_{dte}dte.json"
    else:
        journal_file = JOURNAL_DIR / f"{today.isoformat()}_{symbol}.json"
    verdicts_file = JOURNAL_DIR / f"{today.isoformat()}_verdicts.jsonl"
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

    notifier = TradeNotifier(
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
    )

    logger.info("═══ Sweet Spot Agent: %s — %s ═══", symbol, today)
    logger.info("Settings: qty=%d, chop=%d-%d, max_trades=%d, max_stops=%d, scan_start=%dmin, regime_guard=%s, pb_ema=%s, paper=%s, mode=%s",
                qty, min_chop, max_chop, max_trades_per_day, max_stops_per_day, scan_start_min,
                "ON" if regime_guard else "OFF",
                f"{pb_ema_fast}/{pb_ema_slow}" if pb_ema else "OFF",
                bool(trader),
                f"{dte}DTE options (contracts={contracts}, delta={target_delta})")
    logger.info("Exit goldens: tiered_stag_early_bar=6 (30min, golden 2026-06-19), tiered_stag_pnl_band=[-0.1, +0.2]R, "
                "tiered_stag_mfe_skip=0.5R, fallback_stagnation=bar12 (60min, threshold=0.3R), "
                "stop=mid±0.10×OR_width, decay_target_halflife=8 (40min), decay_target_floor=0.4, time_stop=15:30 ET")

    # ── FOMC sit-out filter (golden 2026-06-04: skip rate-decision days) ──
    if skip_fomc and today.isoformat() in FOMC_DATES:
        logger.info("🚫 FOMC SIT-OUT: %s is an FOMC rate-decision day. Skipping today.", today.isoformat())
        return

    # ── Failed-bounce day-regime filter (golden 2026-06-06) ──
    # Skip days where today gaps up small (0% to 0.50%) AND yesterday closed in
    # the lower 20% of its range — bearish-capitulation close + weak overnight bid
    # = failed bounce archetype. Diagnostic-grounded cross-symbol cohort.
    # A/B: SPY 730d PF 3.09→3.38, Sharpe 5.83→6.04; QQQ 365d Calmar 17→26, MDD% 5.8→3.8.
    if skip_failed_bounce:
        try:
            symbol_for_bars = symbol
            # Pull the last 2 daily bars to compute prior_close, prior_high, prior_low, today_open
            from src.utils.alpaca_data import fetch_bars
            recent_5m = fetch_bars(symbol_for_bars, days_back=3, interval="5min")
            if recent_5m is not None and len(recent_5m) > 0:
                recent_5m["_d"] = recent_5m.index.date
                daily = recent_5m.groupby("_d").agg(
                    Open=("Open", "first"), High=("High", "max"),
                    Low=("Low", "min"), Close=("Close", "last"),
                )
                if len(daily) >= 2:
                    today_open = float(daily["Open"].iloc[-1])
                    p_close = float(daily["Close"].iloc[-2])
                    p_high = float(daily["High"].iloc[-2])
                    p_low = float(daily["Low"].iloc[-2])
                    p_range = p_high - p_low
                    if p_close > 0 and p_range > 0:
                        gap_pct = (today_open - p_close) / p_close * 100
                        close_pos = (p_close - p_low) / p_range
                        if (0 < gap_pct <= failed_bounce_gap_max
                                and close_pos <= failed_bounce_close_pos_max):
                            logger.info(
                                "🚫 FAILED-BOUNCE SIT-OUT: gap_pct=%.2f%% ∈ (0, %.2f] AND "
                                "prior_close_pos=%.2f ≤ %.2f — skipping today.",
                                gap_pct, failed_bounce_gap_max,
                                close_pos, failed_bounce_close_pos_max)
                            return
        except Exception as e:
            logger.warning("Failed-bounce filter check failed (%s) — proceeding without it.", e)

    # ── VIX sit-out filter (golden: skip if VIX > 30 or spiked > 20% day-over-day) ──
    if vix_max > 0 or vix_spike_pct > 0:
        try:
            vix_df = yf.download('^VIX', period='5d', interval='1d', progress=False)
            if isinstance(vix_df.columns, pd.MultiIndex):
                vix_df.columns = vix_df.columns.get_level_values(0)
            vix_closes = vix_df['Close'].astype(float)
            if len(vix_closes) >= 1:
                current_vix = float(vix_closes.iloc[-1])
                if vix_max > 0 and current_vix > vix_max:
                    logger.info("🚫 VIX SIT-OUT: VIX=%.1f > %.0f. Skipping today.", current_vix, vix_max)
                    return
                if vix_spike_pct > 0 and len(vix_closes) >= 2:
                    prev_vix = float(vix_closes.iloc[-2])
                    if prev_vix > 0:
                        spike = (current_vix - prev_vix) / prev_vix * 100
                        if spike > vix_spike_pct:
                            logger.info("🚫 VIX SPIKE SIT-OUT: VIX spiked %.1f%% (%.1f→%.1f). Skipping today.",
                                        spike, prev_vix, current_vix)
                            return
        except Exception as e:
            logger.warning("VIX sit-out check failed: %s — proceeding anyway", e)

    last_trigger = None
    scan_count = 0
    trades_today = 0
    flip_trades_today = 0
    stops_today = 0
    consecutive_losses = 0
    # Position tracking — supports MULTIPLE positions per (symbol, direction) to
    # match replay's stacking behavior. Keyed on synthetic position_id (occ#entry_iso)
    # rather than raw OCC, because cluster entries 5 min apart may resolve to the
    # same OCC strike, and we need each entry tracked independently for journaling.
    #
    # OCC-fate-cohort: Alpaca's close_position(occ) closes the entire OCC qty, so
    # when one position at OCC X exits, all positions sharing X exit together.
    # This is acceptable — same-OCC positions have nearly identical Greeks.
    open_directions: dict[str, str] = {}  # position_id → "buy_call" / "buy_put"
    open_options: dict[str, dict] = {}  # position_id → {entry_premium, stop_price, target_price, direction, occ_symbol, ...}

    # ── Restart recovery: rehydrate open positions from Alpaca + journal ──
    # If the agent was restarted mid-day, in-memory state is empty. Pull live
    # Alpaca positions for this symbol's 0DTE options and rejoin with the
    # journal so exit monitoring resumes seamlessly.
    #
    # Multi-position aware: iterate ALL unclosed triggers (not just unique OCCs),
    # so a cluster of N same-OCC entries is fully rehydrated as N position_ids.
    if trader:
        try:
            live_positions = {p.symbol: p for p in trader.client.get_all_positions()}
            expected_trade_mode = f"{dte}dte_option"
            for trig in triggers:
                if trig.get("closed") or trig.get("discard") or trig.get("trade_mode") != expected_trade_mode:
                    continue
                occ = trig.get("occ_symbol")
                if occ and occ in live_positions and occ.startswith(symbol):
                    entry_time_str = trig.get("timestamp")
                    try:
                        entry_time = datetime.fromisoformat(entry_time_str)
                    except Exception:
                        entry_time = get_et_now()
                    pos_id = f"{occ}#{entry_time.isoformat()}"
                    open_options[pos_id] = {
                        "occ_symbol": occ,
                        "entry_premium": trig.get("option_premium", 1.0),
                        "stop_price": trig["stop"],
                        "target_price": trig["target"],
                        "original_target_dist": abs(trig["target"] - trig["entry"]),
                        "direction": trig["direction"],
                        "delta": trig.get("option_delta", 0.5),
                        "entry_time": entry_time,
                        "entry_underlying": trig.get("price", trig["entry"]),
                        "max_favorable_excursion": 0.0,
                    }
                    open_directions[pos_id] = trig["direction"]
                    trades_today += 1  # count toward daily cap so we don't over-trade
                    # Set cooldown anchor so next scan doesn't immediately re-trigger.
                    last_trigger = max(last_trigger, entry_time) if last_trigger else entry_time
                    logger.info("♻ Recovered open position from journal: %s @ %s (stop=$%.2f target=$%.2f)",
                                occ, entry_time.strftime("%H:%M"), trig["stop"], trig["target"])
        except Exception as e:
            logger.warning("Restart-recovery scan failed: %s — proceeding with empty state", e)

    last_status_update = None

    while True:
        now = get_et_now()

        if not is_market_hours():
            if now.hour >= 16:
                logger.info("Loop exit: past market close (%02d:%02d ET)", now.hour, now.minute)
                break
            logger.info("Outside market hours (%02d:%02d ET, weekday=%d) — sleeping 60s", now.hour, now.minute, now.weekday())
            _write_heartbeat(f"run_day_{symbol}_outside_hours")
            time.sleep(60)
            continue

        # ── Gainz early-exit check (runs every loop, even after entry cutoff) ──
        # Iterates by position_id; an OCC-fate-cohort close drops ALL positions
        # sharing that OCC together (single Alpaca close_position call closes them all).
        if gainz_exit and trader and open_directions:
            # Fetch live positions to prune tracked state. CRITICAL: fail CLOSED —
            # if the fetch errors (network/rate-limit/timeout), do NOT purge tracked
            # state, or a single transient error strands an open position (agent thinks
            # it holds nothing, stops monitoring, exits the loop, position expires).
            # See bug_live_exit_not_firing_2026_07: 07-01 SPY C747 orphaned this way (-$519).
            _positions_fetched = False
            live_symbols: set[str] = set()
            try:
                live_symbols = {p["symbol"] for p in trader.get_positions()}
                _positions_fetched = True
            except Exception as e:
                logger.warning("Failed to fetch positions for Gainz check: %s — "
                               "keeping tracked state this iteration (fail-closed)", e)
            # Drop tracked positions whose OCC is no longer open at Alpaca
            # (stop/target/EOD already fired). For shares fallback, the "occ" is the underlying symbol.
            def _pos_alpaca_symbol(pid):
                info = open_options.get(pid, {})
                return info.get("occ_symbol") or pid  # shares fallback: pid IS the underlying symbol
            # Only prune on a SUCCESSFUL fetch — never purge based on an empty set that
            # came from a failed request.
            if _positions_fetched:
                open_directions = {pid: d for pid, d in open_directions.items()
                                   if _pos_alpaca_symbol(pid) in live_symbols}
                open_options = {pid: info for pid, info in open_options.items() if pid in open_directions}
            for pos_id, dir_ in list(open_directions.items()):
                if pos_id not in open_directions:
                    continue  # already closed earlier this iteration (fate cohort)
                occ_or_sym = _pos_alpaca_symbol(pos_id)
                # Always evaluate Gainz on the underlying's bars, never the OCC symbol.
                if _check_gainz_exit(symbol, dir_, gainz_body_ratio,
                                     gainz_rsi_overbought, gainz_rsi_oversold):
                    # ── Min-profit gate: only Gainz-exit if P&L >= gainz_min_profit_r × risk ──
                    if gainz_min_profit_r > 0:
                        trig_match = next((t for t in triggers
                                           if (t.get("symbol") == occ_or_sym or t.get("occ_symbol") == occ_or_sym)
                                           and not t.get("closed")), None)
                        if trig_match:
                            risk = abs(trig_match.get("entry", 0) - trig_match.get("stop", 0))
                            if risk > 0:
                                try:
                                    # Use underlying price P&L (not option P&L) to match replay logic
                                    cur_bars = alpaca_fetch_bars(symbol, days_back=1, interval="5min")
                                    if len(cur_bars) > 0:
                                        cur_price = float(cur_bars["Close"].iloc[-1])
                                        entry_ul = trig_match.get("price", trig_match.get("entry", cur_price))
                                        if "call" in dir_:
                                            ul_pnl = cur_price - entry_ul
                                        else:
                                            ul_pnl = entry_ul - cur_price
                                        if ul_pnl < risk * gainz_min_profit_r:
                                            logger.debug("Gainz exit skipped for %s — underlying P&L $%.2f < %.1fR ($%.2f)",
                                                         occ_or_sym, ul_pnl,
                                                         gainz_min_profit_r, risk * gainz_min_profit_r)
                                            continue
                                except Exception as e:
                                    logger.warning("Min-profit gate check failed for %s: %s", occ_or_sym, e)
                    is_option = open_options.get(pos_id, {}).get("occ_symbol") is not None
                    # Identify the OCC-fate-cohort: all position_ids sharing this OCC close together.
                    cohort_ids = [pid for pid, info in open_options.items()
                                  if (info.get("occ_symbol") or pid) == occ_or_sym]
                    if not cohort_ids:
                        cohort_ids = [pos_id]
                    logger.info("⚡ GAINZ EXIT: closing %s %s on opposing reversal signal (cohort=%d position(s))",
                                occ_or_sym, "CALL" if "call" in dir_ else "PUT", len(cohort_ids))
                    if is_option:
                        close_result = trader.close_options_position(occ_or_sym)
                    else:
                        close_result = trader.close_position(occ_or_sym)
                    # Stamp ALL triggers in the cohort with the Gainz exit info
                    cohort_entry_times = {open_options[pid].get("entry_time") for pid in cohort_ids if pid in open_options}
                    for trig in triggers:
                        if trig.get("closed"):
                            continue
                        trig_occ = trig.get("occ_symbol") or trig.get("symbol")
                        if trig_occ != occ_or_sym:
                            continue
                        trig["exit_reason"] = "gainz_exit"
                        trig["exit_time"] = get_et_now().isoformat()
                        if close_result:
                            trig["close_order_id"] = close_result["order_id"]
                        trig["closed"] = True
                    journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                    try:
                        g_bars = alpaca_fetch_bars(symbol, days_back=1, interval="5min")
                        g_price = float(g_bars["Close"].iloc[-1]) if len(g_bars) > 0 else 0
                    except Exception:
                        g_price = 0
                    for trig in triggers:
                        trig_occ = trig.get("occ_symbol") or trig.get("symbol")
                        if trig_occ == occ_or_sym and trig.get("closed") and trig.get("exit_reason") == "gainz_exit":
                            notifier.notify_trade_exit(trig, "gainz", g_price or trig.get("entry", 0))
                    for pid in cohort_ids:
                        open_directions.pop(pid, None)
                        open_options.pop(pid, None)

        # ── Options stop/target/time-stop monitoring (underlying-based, since no bracket for options) ──
        # Iterates per position_id. When one position triggers an exit, the entire
        # OCC-fate-cohort closes together via Alpaca's close_position(occ).
        if trader and open_options:
            try:
                current_price_bars = alpaca_fetch_bars(symbol, days_back=1, interval="5min")
                if len(current_price_bars) > 0:
                    underlying_price = float(current_price_bars["Close"].iloc[-1])
                    # Intrabar extremes of the last completed bar. Replay fires a
                    # stop the moment a bar's high/low TOUCHES the stop level
                    # (replay_sweet_spot.py:1160/1165 use fl<=stop / fh>=stop),
                    # not when the bar CLOSES beyond it. Checking Close-only made
                    # live detect stops up to a full bar late — which starved the
                    # max_stops_per_day halt and let same-dir entries stack into a
                    # reversal before the first stop registered (see 2026-05-29:
                    # replay halted at 1 stop / -$111, live took 3 / -$474).
                    # Use these for the stop comparison to match replay sensitivity.
                    bar_high = float(current_price_bars["High"].iloc[-1])
                    bar_low = float(current_price_bars["Low"].iloc[-1])
                    for pos_id, opt_info in list(open_options.items()):
                        if pos_id not in open_options:
                            continue  # already closed earlier this iteration via OCC cohort
                        occ_sym = opt_info.get("occ_symbol", pos_id)
                        should_close = False
                        close_reason = ""

                        # ── Update Maximum Favorable Excursion (MFE) ──
                        entry_ul = opt_info.get("entry_underlying", underlying_price)
                        if "call" in opt_info["direction"]:
                            current_excursion = underlying_price - entry_ul
                        else:
                            current_excursion = entry_ul - underlying_price
                        opt_info["max_favorable_excursion"] = max(
                            opt_info.get("max_favorable_excursion", 0.0), current_excursion
                        )

                        # ── Decay-aware target (golden: ON, halflife=6 bars, floor=0.4) ──
                        effective_target = opt_info["target_price"]
                        if "entry_time" in opt_info:
                            minutes_in_trade = (now - opt_info["entry_time"]).total_seconds() / 60
                            bars_elapsed = max(1, int(minutes_in_trade / 5))
                            decay_halflife = 8  # bars (40 min, was 6/30min)
                            decay_floor = 0.4
                            decay_factor = max(decay_floor, 0.5 ** (bars_elapsed / decay_halflife))
                            original_dist = opt_info.get("original_target_dist", 0)
                            if original_dist > 0:
                                entry_underlying = opt_info.get("entry_underlying", underlying_price)
                                decayed_dist = original_dist * decay_factor
                                if "call" in opt_info["direction"]:
                                    effective_target = entry_underlying + decayed_dist
                                else:
                                    effective_target = entry_underlying - decayed_dist

                        # Stop uses the bar's intrabar extreme (touch = stop, replay
                        # parity); target/decay stays on Close, matching replay's
                        # decay-aware-targets path (fh/fl target check is gated behind
                        # `not decay_aware_targets`, which is OFF in the golden config).
                        if "call" in opt_info["direction"]:
                            if bar_low <= opt_info["stop_price"]:
                                should_close = True
                                close_reason = "stop"
                            elif underlying_price >= effective_target:
                                should_close = True
                                close_reason = "decay_target"
                        else:  # put
                            if bar_high >= opt_info["stop_price"]:
                                should_close = True
                                close_reason = "stop"
                            elif underlying_price <= effective_target:
                                should_close = True
                                close_reason = "decay_target"

                        # ── Theta-breakeven exit: if profitable but theta will eat it ──
                        if not should_close and "entry_time" in opt_info:
                            minutes_in_trade = (now - opt_info["entry_time"]).total_seconds() / 60
                            bars_elapsed = max(1, int(minutes_in_trade / 5))
                            entry_underlying = opt_info.get("entry_underlying", underlying_price)
                            current_pnl_raw = (underlying_price - entry_underlying) \
                                if "call" in opt_info["direction"] \
                                else (entry_underlying - underlying_price)
                            if current_pnl_raw > 0 and bars_elapsed >= 2:
                                # Compute theta burn over next 2 bars using sqrt decay model
                                total_market_minutes = 390  # 9:30-16:00
                                entry_time_et = opt_info["entry_time"]
                                entry_min_since_open = (entry_time_et.hour * 60 + entry_time_et.minute) - (9 * 60 + 30)
                                bar_minutes = entry_min_since_open + bars_elapsed * 5
                                remaining_frac_now = max(0, (total_market_minutes - bar_minutes) / total_market_minutes)
                                remaining_frac_next = max(0, (total_market_minutes - bar_minutes - 10) / total_market_minutes)
                                premium = opt_info.get("entry_premium", 1.0)
                                theta_now = premium * 0.70 * (1.0 - remaining_frac_now ** 0.5)
                                theta_next = premium * 0.70 * (1.0 - remaining_frac_next ** 0.5)
                                theta_burn_2bars = theta_next - theta_now
                                option_delta = opt_info.get("delta", 0.50)
                                option_profit_approx = option_delta * current_pnl_raw
                                if theta_burn_2bars >= option_profit_approx * 0.8:
                                    should_close = True
                                    close_reason = "theta_exit"

                        # Stagnation exit: if 60 min (12 bars) have passed and trade
                        # hasn't moved 0.3R in its favor, cut it to avoid theta bleed.
                        # Tiered stagnation (golden 2026-06-19: 8→6 bars promoted): at 30 min
                        # (6 bars), exit flat trades between -0.1R and +0.2R early. At 60 min,
                        # standard stagnation. Phase 3b strict-real grid: SPY 0DTE PF 1.91→2.02,
                        # QQQ 0DTE PF 1.41→1.47, 4/4 quartiles passed both symbols/DTEs.
                        # MFE skip (golden): if trade already reached 0.5R, don't stagnation-exit —
                        # let decay_target or stop resolve it. Trades that showed real momentum
                        # but temporarily pulled back deserve more time to reach target.
                        if not should_close and "entry_time" in opt_info:
                            minutes_in_trade = (now - opt_info["entry_time"]).total_seconds() / 60
                            risk = abs(opt_info.get("entry_underlying", underlying_price) - opt_info["stop_price"])
                            if risk > 0:
                                current_pnl = (underlying_price - opt_info.get("entry_underlying", underlying_price)) \
                                    if "call" in opt_info["direction"] \
                                    else (opt_info.get("entry_underlying", underlying_price) - underlying_price)
                                mfe = opt_info.get("max_favorable_excursion", 0.0)
                                mfe_ratio = mfe / risk if risk > 0 else 0
                                pnl_r = current_pnl / risk

                                # Tiered stagnation: early exit at bar 6 (30 min) if flat
                                if 28 <= minutes_in_trade < 32:  # ~30 min window (bar 6)
                                    if -0.1 <= pnl_r <= 0.2 and mfe_ratio < 0.5:
                                        should_close = True
                                        close_reason = "stagnation"

                                # Standard stagnation at bar 12 (60 min)
                                if not should_close and minutes_in_trade >= 60:
                                    if current_pnl < risk * 0.3 and mfe_ratio < 0.5:
                                        should_close = True
                                        close_reason = "stagnation"

                        # Time stop: close at 15:30 ET (15 min before broker's 3:45 forced close)
                        if not should_close and now.hour == 15 and now.minute >= 30:
                            should_close = True
                            close_reason = "time_stop"

                        if should_close:
                            # OCC-fate-cohort: all positions sharing this OCC close together.
                            cohort_ids = [pid for pid, info in open_options.items()
                                          if info.get("occ_symbol", pid) == occ_sym]
                            logger.info("⚡ OPTIONS %s: closing %s (%s at $%.2f) — cohort=%d position(s)",
                                        close_reason.upper(), occ_sym, opt_info["direction"],
                                        underlying_price, len(cohort_ids))
                            close_result = trader.close_options_position(occ_sym)
                            closed_count = 0
                            for trig in triggers:
                                if trig.get("occ_symbol") == occ_sym and not trig.get("closed"):
                                    trig["exit_reason"] = close_reason
                                    trig["exit_time"] = get_et_now().isoformat()
                                    trig["underlying_exit_price"] = underlying_price
                                    if close_result:
                                        trig["close_order_id"] = close_result["order_id"]
                                    trig["closed"] = True
                                    closed_count += 1
                            journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                            for trig in triggers:
                                if trig.get("occ_symbol") == occ_sym and trig.get("closed") and \
                                   trig.get("exit_reason") == close_reason:
                                    notifier.notify_trade_exit(trig, close_reason, underlying_price)
                            for pid in cohort_ids:
                                open_options.pop(pid, None)
                                open_directions.pop(pid, None)
                            # Risk trackers count the COHORT as 1 outcome (one stop event = one stop_today)
                            # not N stops, since they all closed on the same underlying threshold.
                            if close_reason == "stop":
                                stops_today += 1
                            if close_reason in ("stop", "stagnation", "time_stop"):
                                consecutive_losses += 1
                            else:
                                consecutive_losses = 0
            except Exception as e:
                logger.warning("Options monitoring failed: %s", e)

        # ── Entry cutoff: replay parity with --scan-end ──
        # Replay scans bars whose open timestamp ≤ scan_end. The last scannable
        # 5-min bar opens at scan_end (rounded down). Its close = open + 5min,
        # and the live agent can only see it as closed at that wall-clock + buffer.
        # So the live entry cutoff is `scan_end + 5min + buffer`.
        # Belt-and-suspenders before breaking the loop: an empty in-memory
        # open_directions can be WRONG (see bug_live_exit_not_firing_2026_07 —
        # a transient fetch error could purge tracked state). Never break with a
        # real Alpaca position still open. Confirm truth against the broker; if the
        # confirmation fetch itself fails, treat as "positions may be open" (keep
        # monitoring) rather than exiting.
        def _safe_to_exit() -> bool:
            if open_directions:
                return False
            if not trader:
                return True
            try:
                live = trader.get_positions()
            except Exception as e:
                logger.warning("Loop-exit position check failed: %s — staying in loop", e)
                return False
            mine = [p for p in live if str(p.get("symbol", "")).startswith(symbol)]
            if mine:
                logger.warning("Loop-exit blocked: Alpaca still shows %d open %s position(s) "
                               "not in tracked state — re-hydrating and continuing to monitor.",
                               len(mine), symbol)
                for p in mine:
                    occ = p["symbol"]
                    trig = next((t for t in triggers
                                 if t.get("occ_symbol") == occ and not t.get("closed")), None)
                    if trig is None:
                        continue
                    try:
                        et = datetime.fromisoformat(trig.get("timestamp"))
                    except Exception:
                        et = get_et_now()
                    pid = f"{occ}#{et.isoformat()}"
                    if pid not in open_options:
                        open_options[pid] = {
                            "occ_symbol": occ,
                            "entry_premium": trig.get("option_premium", 1.0),
                            "stop_price": trig["stop"],
                            "target_price": trig["target"],
                            "original_target_dist": abs(trig["target"] - trig["entry"]),
                            "direction": trig["direction"],
                            "delta": trig.get("option_delta", 0.5),
                            "entry_time": et,
                            "entry_underlying": trig.get("price", trig["entry"]),
                            "max_favorable_excursion": 0.0,
                        }
                        open_directions[pid] = trig["direction"]
                return False
            return True

        try:
            _se_h, _se_m = (int(x) for x in scan_end.split(":"))
        except ValueError:
            _se_h, _se_m = 13, 59
        _cutoff_min = _se_h * 60 + _se_m + 5  # last bar's close
        _now_min = now.hour * 60 + now.minute + now.second / 60
        if _now_min >= _cutoff_min:
            # After 15:30, if no open positions, we're done for the day
            if now.hour == 15 and now.minute >= 30 and _safe_to_exit():
                logger.info("Loop exit: time stop reached (15:30 ET) and no open positions.")
                break
            if _safe_to_exit():
                logger.info("Loop exit: past %s entry cutoff (%02d:%02d ET) and no open positions.",
                            scan_end, now.hour, now.minute)
                break
            _write_heartbeat(f"run_day_{symbol}_monitoring_positions")
            time.sleep(300)
            continue

        # Wait for OR to form + scan start delay.
        # With dynamic_or=True (golden), allow scanning from 10:00 (30 min after open);
        # check_sweet_spot will verify the 10:00 decisive-breakout condition before
        # using the 30-min OR, and defer otherwise.
        effective_scan_start_min = 30 if dynamic_or else scan_start_min
        if not is_past_or(min_after_open=effective_scan_start_min):
            logger.info("Waiting for scan window (OR + %d min)...", effective_scan_start_min)
            _write_heartbeat(f"run_day_{symbol}_waiting_scan_window")
            time.sleep(60)
            continue

        # ── Daily limits ──
        # Trade cap: regular trades capped, but flip trades get 1 extra allowance.
        regular_trades = trades_today - flip_trades_today
        at_regular_cap = max_trades_per_day > 0 and regular_trades >= max_trades_per_day
        at_flip_cap = flip_trades_today >= 1  # max 1 flip trade per day
        if at_regular_cap and at_flip_cap:
            logger.info("Daily trade limit reached (%d regular + %d flip). Monitoring open positions only.", regular_trades, flip_trades_today)
            if _safe_to_exit():
                logger.info("Loop exit: daily trade cap reached and no open positions.")
                break
            _write_heartbeat(f"run_day_{symbol}_trade_cap_monitoring")
            time.sleep(300)
            continue
        if max_stops_per_day > 0 and stops_today >= max_stops_per_day:
            logger.info("Daily stop limit reached (%d/%d). Halting for protection.", stops_today, max_stops_per_day)
            break
        if max_consecutive_losses > 0 and consecutive_losses >= max_consecutive_losses:
            logger.info("Streak breaker: %d consecutive losses. Halting for the day.", consecutive_losses)
            break

        scan_count += 1
        verdict = check_sweet_spot(symbol, max_chop=max_chop, min_chop=min_chop,
                                   regime_guard=regime_guard,
                                   pb_ema=pb_ema, pb_ema_fast=pb_ema_fast, pb_ema_slow=pb_ema_slow,
                                   prior_triggers=triggers,
                                   dynamic_or=dynamic_or,
                                   dynamic_or_threshold=dynamic_or_threshold)

        # Persist every verdict (rejects + triggers) to a daily JSONL for offline analysis.
        # Attach a yfinance options-chain feature snapshot (Dimension 2 data collection).
        # Cached 60s inside the helper so this only hits yfinance ~once/min/symbol.
        try:
            from options_agent.src.utils.yf_chain_features import snapshot as _yf_snap
            chain_snap = _yf_snap(symbol, spot=verdict.get("close") or verdict.get("price"))
        except Exception as e:
            logger.debug("Chain snapshot failed: %s", e)
            chain_snap = None
        try:
            verdict_record = {"ts": now.isoformat(), "symbol": symbol, **verdict}
            if chain_snap is not None:
                verdict_record["chain"] = chain_snap
            with verdicts_file.open("a", encoding="utf-8") as vf:
                vf.write(json.dumps(verdict_record, default=str) + "\n")
        except Exception as e:
            logger.warning("Verdict journal write failed: %s", e)

        if verdict.get("status") == "reject":
            # Always log post-direction rejects (interesting); only log pre-direction
            # rejects when --verbose-rejects is on (noisy: fires every scan with no setup).
            stage = verdict.get("stage", "?")
            interesting = stage not in ("data", "direction")
            if interesting or verbose_rejects:
                logger.info("✗ %s reject [%s] %s", symbol, stage, verdict.get("reason", ""))

        if verdict.get("status") == "trigger":
            trigger = {k: v for k, v in verdict.items() if k != "status"}
            # If at regular cap, only allow flip triggers through
            if at_regular_cap and not trigger.get("is_flip", False):
                logger.info("✗ %s skip [trade_cap] regular cap reached, non-flip trigger ignored", symbol)
            else:
                # Deduplicate (5 min cooldown — matches replay golden cooldown-bars=1).
                # Post-stagnation cooldown is also 5 min: stag_cooldown_bars=1 in replay
                # since 2026-05-20, because live can't enforce a longer post-stag cooldown
                # in wall-clock (by the time stagnation is detected, the cooldown window
                # has already elapsed). See bug_replay_cooldown_timing_drift memory.
                cooldown_sec = 300
                # Same-direction stacking is ALLOWED (matches replay parity, 2026-05-25).
                # Replay opens multiple same-dir positions when triggers cluster on
                # trend days; live now mirrors this. Duplicate-order safety is enforced
                # at placement time by comparing in-memory position count vs Alpaca qty.
                # See [[bug_live_replay_position_stacking]].
                if last_trigger and (now - last_trigger).total_seconds() < cooldown_sec:
                    logger.debug("Skipping duplicate trigger within cooldown (%d min)", cooldown_sec // 60)
                else:
                    last_trigger = now
                    trades_today += 1
                    if trigger.get("is_flip", False):
                        flip_trades_today += 1

                    # Save bar snapshot for reproducibility (Layer 2)
                    bars_df = trigger.pop("_bars", None)
                    if bars_df is not None:
                        bars_dir = JOURNAL_DIR / "bars"
                        bars_dir.mkdir(exist_ok=True)
                        bars_file = bars_dir / f"{today.isoformat()}_{symbol}_{trigger['time']}_bars.parquet"
                        bars_df.to_parquet(bars_file)
                        trigger["bars_file"] = str(bars_file.name)

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
                            # Options-only (policy 2026-07-18): this agent trades {dte}DTE options
                            # exclusively. There is no shares path — a missing chain skips the trade
                            # rather than buying the underlying. Equity positions carry unbounded
                            # overnight risk and don't match replay/backtest semantics.
                            # {dte}DTE options: fetch chain, pick contract, buy-to-open
                            from src.utils.alpaca_data import get_dte_chain
                            opt_type = "call" if "call" in trigger["direction"] else "put"
                            contract = get_dte_chain(
                                symbol, dte=dte, option_type=opt_type,
                                target_delta=target_delta,
                                spot_price=trigger.get("price"),
                            )
                            if contract:
                                # Cascade-tier contract sizing
                                explosion = trigger.get("explosion", 4)
                                if explosion >= 8:
                                    num_contracts = contracts * cascade_size_high
                                elif explosion >= 6:
                                    num_contracts = contracts * cascade_size_mid
                                else:
                                    num_contracts = contracts * cascade_size_low

                                # ── Duplicate-order guard ──
                                # Before placing, verify Alpaca's reported qty at this OCC matches
                                # the SUM of qty across our open positions at this OCC. If Alpaca
                                # has more than expected, an out-of-band buy occurred (restart race
                                # or parallel agent). Abort to prevent double-buying.
                                new_occ = contract["occ_symbol"]
                                expected_qty = sum(
                                    info.get("num_contracts", 0)
                                    for pid, info in open_options.items()
                                    if info.get("occ_symbol") == new_occ
                                )
                                try:
                                    live_positions_now = {p.symbol: p for p in trader.client.get_all_positions()}
                                    existing = live_positions_now.get(new_occ)
                                    existing_qty = int(float(existing.qty)) if existing else 0
                                except Exception as e:
                                    logger.warning("Duplicate-order guard: Alpaca position fetch failed: %s — proceeding with placement", e)
                                    existing_qty = expected_qty  # assume in sync, allow placement
                                if existing_qty > expected_qty:
                                    logger.warning(
                                        "✗ %s skip [duplicate_order_guard] OCC %s has %d contracts at Alpaca but in-memory expected %d. "
                                        "Aborting buy to prevent double-purchase. Investigate stale state or parallel agent.",
                                        symbol, new_occ, existing_qty, expected_qty)
                                    # Mark the journaled trigger as discarded so restart-recovery skips it
                                    trigger["discard"] = True
                                    trigger["discard_reason"] = "duplicate_order_guard"
                                    journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                                    # Roll back the cooldown anchor and daily counters so a clean retry can fire next bar.
                                    trades_today -= 1
                                    if trigger.get("is_flip", False):
                                        flip_trades_today -= 1
                                    last_trigger = None  # next trigger isn't blocked by this skip
                                else:
                                    order = trader.place_options_trade(
                                        occ_symbol=new_occ,
                                        direction=trigger["direction"],
                                        qty=num_contracts,
                                        # No limit_price = market order (0DTE needs instant fill)
                                        time_in_force="day",
                                    )
                                    trigger["order_id"] = order["order_id"]
                                    trigger["occ_symbol"] = new_occ
                                    trigger["option_strike"] = contract["strike"]
                                    trigger["option_delta"] = contract["delta"]
                                    trigger["option_premium"] = contract["mid"]
                                    trigger["trade_mode"] = f"{dte}dte_option"
                                    trigger["num_contracts"] = num_contracts

                                    # Synthetic position_id: OCC + entry timestamp. Multiple cluster
                                    # entries at the same OCC each get their own id.
                                    entry_ts_now = get_et_now()
                                    pos_id = f"{new_occ}#{entry_ts_now.isoformat()}"

                                    # Track for stop/target monitoring (underlying-based)
                                    open_options[pos_id] = {
                                        "occ_symbol": new_occ,
                                        "num_contracts": num_contracts,
                                        "entry_premium": contract["mid"],
                                        "stop_price": trigger["stop"],
                                        "target_price": trigger["target"],
                                        "original_target_dist": abs(trigger["target"] - trigger["entry"]),
                                        "direction": trigger["direction"],
                                        "delta": contract["delta"],
                                        "entry_time": entry_ts_now,
                                        "entry_underlying": trigger.get("price", trigger["entry"]),
                                        "max_favorable_excursion": 0.0,  # MFE tracking for stagnation skip
                                    }
                                    open_directions[pos_id] = trigger["direction"]
                                    logger.info("  📝 %dDTE %s order: %s strike=$%.2f Δ=%.2f premium=$%.2f × %d contracts (pos_id=%s)",
                                                dte, opt_type.upper(), new_occ,
                                                contract["strike"], contract["delta"], contract["mid"],
                                                num_contracts, pos_id[-12:])
                            else:
                                # Chain unavailable for the requested DTE — skip the trade entirely
                                # for ALL DTEs (user policy 2026-07-18). Never fall back to buying
                                # shares: a missing options chain must not silently turn an options
                                # strategy into an equity position (shares carry unbounded overnight
                                # risk and don't match replay/backtest semantics).
                                logger.warning("  ⚠️ No %dDTE contract found — skipping trade (options-only policy, no shares fallback)",
                                               dte)
                                trigger["discard"] = True
                                trigger["discard_reason"] = f"no_{dte}dte_chain"
                                # Roll back daily counters so a clean retry can fire next bar.
                                trades_today -= 1
                                if trigger.get("is_flip", False):
                                    flip_trades_today -= 1
                                last_trigger = None

                            journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                            logger.info("  📝 Order placed: %s", trigger.get("order_id", "?")[:12])
                            notifier.notify_trade_entry(trigger)
                        except Exception as e:
                            # Order placement raised (e.g. 40310000 insufficient
                            # options buying power — the 07-07 blackout). This used
                            # to be a silent log line, so 12 failed orders went
                            # unnoticed for 9 days. Mark the trigger as a FAILED
                            # order (no order_id) and hard-alert so it's caught same day.
                            logger.error("  ⚠️ Order failed: %s", e)
                            trigger["order_failed"] = True
                            trigger["order_error"] = str(e)
                            try:
                                journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                            except Exception:
                                pass
                            try:
                                notifier.notify_alert(
                                    f"{symbol} ORDER PLACEMENT FAILED",
                                    f"A {trigger.get('direction', '?')} order failed at "
                                    f"{get_et_now().strftime('%H:%M:%S ET')}: `{e}`. "
                                    "No position was opened. If this is a buying-power "
                                    "error, check the account for an orphaned equity "
                                    "position poisoning options buying power.",
                                    level="critical",
                                )
                            except Exception:
                                pass

        # ── 30-min status update (SPY agent only to avoid duplicates) ──
        if symbol == "SPY":
            send_status = last_status_update is None or (now - last_status_update) >= timedelta(minutes=30)
            if send_status:
                try:
                    all_trades_status = []
                    for jf in JOURNAL_DIR.glob(f"{today.isoformat()}_*.json"):
                        if "verdicts" in jf.name:
                            continue
                        all_trades_status.extend(json.loads(jf.read_text()))
                    all_trades_status.sort(key=lambda t: t.get("timestamp", ""))
                    enrich_trades_with_fills(all_trades_status)

                    open_now = [t for t in all_trades_status if not t.get("closed")]
                    status_verdicts = verdicts_file.read_text().strip().split("\n") if verdicts_file.exists() else []
                    status_total = sum(1 for line in status_verdicts if line)
                    status_triggers = sum(1 for line in status_verdicts if line and '"trigger"' in line)
                    status_rejects = status_total - status_triggers

                    notifier.notify_status_update(
                        all_trades_status, open_now, status_total,
                        status_triggers, status_rejects,
                    )
                    last_status_update = now
                    logger.info("  📊 Status update sent")
                except Exception as e:
                    logger.error("  Status update failed: %s", e)

        _write_heartbeat(f"run_day_{symbol}_scanning")
        # Sleep until the next 5-min bar close (+ a few seconds for Alpaca to
        # publish the closed bar). Matches replay_day, which scans bar-by-bar
        # at each closed-bar boundary instead of on a drifting wall-clock cadence.
        time.sleep(max(1.0, seconds_until_next_bar_close()))

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

    # ── EOD Daily Report (SPY agent only to avoid duplicates) ──
    if symbol == "SPY":
        try:
            all_trades = []
            for jf in JOURNAL_DIR.glob(f"{today.isoformat()}_*.json"):
                if "verdicts" in jf.name:
                    continue
                all_trades.extend(json.loads(jf.read_text()))
            all_trades.sort(key=lambda t: t.get("timestamp", ""))
            enrich_trades_with_fills(all_trades)

            verdicts_file_eod = JOURNAL_DIR / f"{today.isoformat()}_verdicts.jsonl"
            total_scans = 0
            if verdicts_file_eod.exists():
                total_scans = sum(1 for line in verdicts_file_eod.read_text().strip().split("\n") if line)

            notifier.notify_daily_report(today.isoformat(), all_trades, total_scans)
            logger.info("  📧 Daily report sent: %d trades, %d scans", len(all_trades), total_scans)
        except Exception as e:
            logger.error("  Daily report failed: %s", e)


HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "agent_heartbeat"

def _write_heartbeat(state: str) -> None:
    HEARTBEAT_FILE.write_text(json.dumps({
        "ts": datetime.now(ZoneInfo("UTC")).isoformat(),
        "state": state,
        "pid": os.getpid(),
    }))

def _safe_sleep(seconds: float, label: str = "waiting") -> None:
    """Sleep in chunks with periodic heartbeat and wall-clock awareness.

    Uses time.time() (wall clock) instead of time.monotonic() so that
    system suspend (e.g. laptop lid close) counts toward elapsed time.
    When the lid reopens, the sleep exits immediately if the target has passed.
    """
    deadline = time.time() + seconds
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        _write_heartbeat(label)
        time.sleep(min(300, remaining))


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
    parser.add_argument("--min-chop", type=int, default=2,
                        help="Min choppiness floor (golden: 2). C=0-1 trades are ~50%% WR over 2yr.")
    parser.add_argument("--max-trades-per-day", type=int, default=3, help="Max trades per day (default: 3)")
    parser.add_argument("--max-stops-per-day", type=int, default=1, help="Halt after N stop-outs (default: 1)")
    parser.add_argument("--max-consecutive-losses", type=int, default=2,
                        help="Stop trading after N consecutive losses (default: 2)")
    parser.add_argument("--scan-start-min", type=int, default=60, help="Minutes after open to start scanning (default: 60 = 10:30)")
    parser.add_argument("--scan-end", type=str, default="13:59",
                        help="ET HH:MM cutoff (matches replay --scan-end, golden: 13:59). "
                             "Last scannable bar opens at scan_end; agent exits after that bar's close + buffer.")
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
    parser.add_argument("--gainz-min-profit-r", type=float, default=0.3,
                        help="Min profit as fraction of R to allow Gainz exit (golden: 0.3, 0=disabled)")
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
                        help="Disable regime guard (legacy flag, already OFF by default)")
    parser.add_argument("--regime-guard", action="store_true",
                        help="Enable regime guard (default: OFF — validated: removing it improves Sharpe 1.38→1.74, PF 1.30→1.37 over 2yr)")
    parser.add_argument("--no-pb-ema", action="store_true",
                        help="Disable PB EMA inside-band gate (default: ON, 13/55 — "
                             "validated 730d SPY: PF 1.41→1.48, Sharpe 1.89→2.26, MDD −12%%)")
    parser.add_argument("--pb-ema-fast", type=int, default=13,
                        help="Fast EMA length for PB EMA band (golden: 13)")
    parser.add_argument("--pb-ema-slow", type=int, default=55,
                        help="Slow EMA length for PB EMA band (golden: 55)")
    parser.add_argument("--vix-max", type=float, default=30.0,
                        help="Skip trading days where VIX > this level (0=disabled, default: 30)")
    parser.add_argument("--verbose-rejects", action="store_true", default=False,
                        help="Log every reject reason, including pre-direction (no setup) rejects. "
                             "Off by default — only post-direction rejects (gates that killed an actual setup) log.")
    parser.add_argument("--vix-spike-pct", type=float, default=20.0,
                        help="Skip days where VIX spiked >N%% day-over-day (0=disabled, default: 20)")
    parser.add_argument("--allow-fomc-days", dest="skip_fomc", action="store_false", default=True,
                        help="Disable FOMC sit-out (default: ON — skip rate-decision days). "
                             "Promoted 2026-06-04: SPY Sharpe +0.12 / QQQ Sharpe +0.10-0.18 / "
                             "clean sweep both symbols both windows.")
    parser.add_argument("--skip-failed-bounce", dest="skip_failed_bounce", action="store_true",
                        default=True,
                        help="Failed-bounce sit-out (default: ON as of 2026-06-20). "
                             "RE-PROMOTED after look-ahead-fix re-validation reversed the 2026-06-13 "
                             "demotion: turning the filter ON improves 7/8 cells (SPY+QQQ × 730d+365d) "
                             "on PF/Sharpe/Calmar/MDD%%. Clean Sharpe sweep +0.15-0.26, clean MDD%% "
                             "sweep -0.1pp to -7.1pp. The 2026-06-13 demotion was a look-ahead artifact.")
    parser.add_argument("--allow-failed-bounce", dest="skip_failed_bounce", action="store_false",
                        help="Disable failed-bounce sit-out (default is ON as of 2026-06-20).")
    parser.add_argument("--failed-bounce-gap-max", type=float, default=0.50)
    parser.add_argument("--failed-bounce-close-pos-max", type=float, default=0.20)
    parser.add_argument("--no-dynamic-or", action="store_true",
                        help="Disable dynamic OR (default: ON — 30-min OR + 10:00 scan-start "
                             "on decisive-breakout mornings; falls back to 60-min OR otherwise).")
    parser.add_argument("--dynamic-or-threshold", type=float, default=0.6,
                        help="Dynamic OR decisive-breakout fraction (golden: 0.6).")
    parser.add_argument("--dte", type=int, default=0,
                        help="Days-to-expiry of the option contract bought at entry. "
                             "0 (default) = same-day expiry, legacy behavior. 1 = next-business-day "
                             "expiry (validated by Phase 2 replay 2026-06-16: SPY PF 1.90→2.28, "
                             "CALL PF 1.34→1.73). When >0, journal filename is suffixed _{dte}dte. "
                             "A missing chain (any DTE) triggers skip-trade; the agent never falls back "
                             "to buying shares (options-only policy 2026-07-18).")
    args = parser.parse_args()

    paper_trade = not args.no_paper

    if args.daemon:
        logger.info("Starting in daemon mode — will run every trading day")
        while True:
            now = get_et_now()
            if now.weekday() >= 5 or not is_trading_day(now):
                if now.weekday() >= 5:
                    days_until_monday = (7 - now.weekday()) % 7 or 7
                    next_day = (now + timedelta(days=days_until_monday)).replace(hour=9, minute=25, second=0)
                    label = "Weekend"
                else:
                    next_day = (now + timedelta(days=1)).replace(hour=9, minute=25, second=0)
                    label = "Market holiday"
                sleep_sec = (next_day - now).total_seconds()
                logger.info("%s. Sleeping %.1f hours...", label, sleep_sec / 3600)
                _safe_sleep(max(sleep_sec, 3600), f"daemon_{args.symbol}_closed")
            elif now.hour < 9 or (now.hour == 9 and now.minute < 25):
                wait_until = now.replace(hour=9, minute=25, second=0, microsecond=0)
                sleep_sec = (wait_until - now).total_seconds()
                logger.info("Sleeping %.0f min until pre-market...", sleep_sec / 60)
                _safe_sleep(max(sleep_sec, 60), f"daemon_{args.symbol}_pre_market")
            elif now.hour < 16:
                run_day(args.symbol, args.qty, args.max_chop, paper_trade,
                        min_chop=args.min_chop,
                        max_trades_per_day=args.max_trades_per_day,
                        max_stops_per_day=args.max_stops_per_day,
                        max_consecutive_losses=args.max_consecutive_losses,
                        scan_start_min=args.scan_start_min,
                        scan_end=args.scan_end,
                        gainz_exit=not args.no_gainz_exit,
                        gainz_body_ratio=args.gainz_body_ratio,
                        gainz_rsi_overbought=args.gainz_rsi_overbought,
                        gainz_rsi_oversold=args.gainz_rsi_oversold,
                        gainz_min_profit_r=args.gainz_min_profit_r,
                        contracts=args.contracts,
                        target_delta=args.target_delta,
                        cascade_size_low=args.cascade_size_low,
                        cascade_size_mid=args.cascade_size_mid,
                        cascade_size_high=args.cascade_size_high,
                        regime_guard=args.regime_guard,
                        vix_max=args.vix_max,
                        vix_spike_pct=args.vix_spike_pct,
                        skip_fomc=args.skip_fomc,
                        skip_failed_bounce=args.skip_failed_bounce,
                        failed_bounce_gap_max=args.failed_bounce_gap_max,
                        failed_bounce_close_pos_max=args.failed_bounce_close_pos_max,
                        pb_ema=not args.no_pb_ema,
                        pb_ema_fast=args.pb_ema_fast,
                        pb_ema_slow=args.pb_ema_slow,
                        dynamic_or=not args.no_dynamic_or,
                        dynamic_or_threshold=args.dynamic_or_threshold,
                        dte=args.dte,
                        verbose_rejects=args.verbose_rejects)
                tomorrow_925 = (now + timedelta(days=1)).replace(hour=9, minute=25, second=0)
                sleep_sec = (tomorrow_925 - get_et_now()).total_seconds()
                logger.info("Market closed. Sleeping %.1f hours until tomorrow...", sleep_sec / 3600)
                _safe_sleep(max(sleep_sec, 3600), f"daemon_{args.symbol}_post_market")
            else:
                _safe_sleep(3600, f"daemon_{args.symbol}_after_hours")
    else:
        # Single day run
        run_day(args.symbol, args.qty, args.max_chop, paper_trade,
                min_chop=args.min_chop,
                max_trades_per_day=args.max_trades_per_day,
                max_stops_per_day=args.max_stops_per_day,
                max_consecutive_losses=args.max_consecutive_losses,
                scan_start_min=args.scan_start_min,
                scan_end=args.scan_end,
                gainz_exit=not args.no_gainz_exit,
                gainz_body_ratio=args.gainz_body_ratio,
                gainz_rsi_overbought=args.gainz_rsi_overbought,
                gainz_rsi_oversold=args.gainz_rsi_oversold,
                gainz_min_profit_r=args.gainz_min_profit_r,
                contracts=args.contracts,
                target_delta=args.target_delta,
                cascade_size_low=args.cascade_size_low,
                cascade_size_mid=args.cascade_size_mid,
                cascade_size_high=args.cascade_size_high,
                regime_guard=args.regime_guard,
                vix_max=args.vix_max,
                vix_spike_pct=args.vix_spike_pct,
                skip_fomc=args.skip_fomc,
                skip_failed_bounce=args.skip_failed_bounce,
                failed_bounce_gap_max=args.failed_bounce_gap_max,
                failed_bounce_close_pos_max=args.failed_bounce_close_pos_max,
                pb_ema=not args.no_pb_ema,
                pb_ema_fast=args.pb_ema_fast,
                pb_ema_slow=args.pb_ema_slow,
                dynamic_or=not args.no_dynamic_or,
                dynamic_or_threshold=args.dynamic_or_threshold,
                dte=args.dte,
                verbose_rejects=args.verbose_rejects)


if __name__ == "__main__":
    main()

