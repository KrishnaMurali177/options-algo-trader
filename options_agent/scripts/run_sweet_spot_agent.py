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

import numpy as np
import pandas as pd
import yfinance as yf

from src.utils.alpaca_data import fetch_bars as alpaca_fetch_bars
from src.opening_range import OpeningRangeAnalyzer
from src.recent_momentum import RecentMomentumAnalyzer
from src.momentum_cascade import MomentumCascadeDetector
from src.models.market_data import MarketIndicators
from src.utils.quality_scorer import compute_quality_score
from src.utils.choppiness import compute_choppiness
from src.utils.gainz import gainz_signal


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
    """Fetch latest VIX close. Returns 20.0 (neutral) on failure."""
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
        logger.warning("Gainz exit check failed for %s: %s", symbol, e)
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


def check_sweet_spot(symbol: str, max_chop: int = 5, regime_guard: bool = True,
                     pb_ema: bool = True, pb_ema_fast: int = 13, pb_ema_slow: int = 55) -> dict:
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
                       "chop": chop.chop_score, "price": indicators.current_price}
        if not (3 <= quality <= 7):
            return {"status": "reject", "stage": "quality",
                    "reason": f"quality {quality} outside sweet band [3,7]", **diag_common}
        if cascade.explosion_score < 2:
            return {"status": "reject", "stage": "cascade",
                    "reason": f"explosion {cascade.explosion_score} < 2", **diag_common}
        if chop.chop_score > max_chop:
            return {"status": "reject", "stage": "chop",
                    "reason": f"chop {chop.chop_score} > max {max_chop}", **diag_common}

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
        }

    except Exception as e:
        logger.error("Sweet spot check failed: %s", e)
        return {"status": "reject", "stage": "error", "reason": str(e)}


def run_day(symbol: str, qty: int, max_chop: int, paper_trade: bool,
            max_trades_per_day: int = 3, max_stops_per_day: int = 1,
            max_consecutive_losses: int = 2,
            scan_start_min: int = 60,
            gainz_exit: bool = True,
            gainz_body_ratio: float = 0.7,
            gainz_rsi_overbought: float = 70.0,
            gainz_rsi_oversold: float = 30.0,
            gainz_min_profit_r: float = 0.3,
            trade_shares: bool = False,
            contracts: int = 1,
            target_delta: float = 0.50,
            cascade_size_low: int = 3,
            cascade_size_mid: int = 3,
            cascade_size_high: int = 3,
            regime_guard: bool = False,
            vix_max: float = 30.0,
            vix_spike_pct: float = 20.0,
            pb_ema: bool = True,
            pb_ema_fast: int = 13,
            pb_ema_slow: int = 55,
            verbose_rejects: bool = False):
    """Run the agent for one trading day."""
    today = date.today()
    # Per-symbol journal so concurrent SPY/QQQ agents don't clobber each other's writes.
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

    logger.info("═══ Sweet Spot Agent: %s — %s ═══", symbol, today)
    logger.info("Settings: qty=%d, max_chop=%d, max_trades=%d, max_stops=%d, scan_start=%dmin, regime_guard=%s, pb_ema=%s, paper=%s, mode=%s",
                qty, max_chop, max_trades_per_day, max_stops_per_day, scan_start_min,
                "ON" if regime_guard else "OFF",
                f"{pb_ema_fast}/{pb_ema_slow}" if pb_ema else "OFF",
                bool(trader),
                "shares" if trade_shares else f"0DTE options (contracts={contracts}, delta={target_delta})")

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
    last_was_stagnation = False  # Track for extended post-stagnation cooldown
    scan_count = 0
    trades_today = 0
    flip_trades_today = 0
    stops_today = 0
    consecutive_losses = 0
    open_directions: dict[str, str] = {}  # symbol/occ_symbol → "buy_call" / "buy_put"
    open_options: dict[str, dict] = {}  # occ_symbol → {entry_premium, stop_price, target_price, direction}

    # ── Restart recovery: rehydrate open positions from Alpaca + journal ──
    # If the agent was restarted mid-day, in-memory state is empty. Pull live
    # Alpaca positions for this symbol's 0DTE options and rejoin with the
    # journal so exit monitoring resumes seamlessly.
    if trader and not trade_shares:
        try:
            live_positions = {p.symbol: p for p in trader.client.get_all_positions()}
            for trig in triggers:
                if trig.get("closed") or trig.get("discard") or trig.get("trade_mode") != "0dte_option":
                    continue
                occ = trig.get("occ_symbol")
                if occ and occ in live_positions and occ.startswith(symbol):
                    entry_time_str = trig.get("timestamp")
                    try:
                        entry_time = datetime.fromisoformat(entry_time_str)
                    except Exception:
                        entry_time = get_et_now()
                    open_options[occ] = {
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
                    open_directions[occ] = trig["direction"]
                    trades_today += 1  # count toward daily cap so we don't over-trade
                    # Set cooldown anchor so next scan doesn't immediately re-trigger.
                    last_trigger = max(last_trigger, entry_time) if last_trigger else entry_time
                    logger.info("♻ Recovered open position from journal: %s (stop=$%.2f target=$%.2f)",
                                occ, trig["stop"], trig["target"])
        except Exception as e:
            logger.warning("Restart-recovery scan failed: %s — proceeding with empty state", e)

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
        if gainz_exit and trader and open_directions:
            try:
                live_symbols = {p["symbol"] for p in trader.get_positions()}
            except Exception as e:
                logger.warning("Failed to fetch positions for Gainz check: %s", e)
                live_symbols = set()
            # Drop tracked symbols that are no longer open (stop/target/EOD already fired)
            open_directions = {s: d for s, d in open_directions.items() if s in live_symbols}
            for sym, dir_ in list(open_directions.items()):
                # Always evaluate Gainz on the underlying's bars, never the OCC symbol.
                if _check_gainz_exit(symbol, dir_, gainz_body_ratio,
                                     gainz_rsi_overbought, gainz_rsi_oversold):
                    # ── Min-profit gate: only Gainz-exit if P&L >= gainz_min_profit_r × risk ──
                    if gainz_min_profit_r > 0:
                        trig_match = next((t for t in triggers
                                           if (t.get("symbol") == sym or t.get("occ_symbol") == sym)
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
                                                         sym, ul_pnl,
                                                         gainz_min_profit_r, risk * gainz_min_profit_r)
                                            continue
                                except Exception as e:
                                    logger.warning("Min-profit gate check failed for %s: %s", sym, e)
                    is_option = sym in open_options
                    logger.info("⚡ GAINZ EXIT: closing %s %s on opposing reversal signal",
                                sym, "CALL" if "call" in dir_ else "PUT")
                    if is_option:
                        close_result = trader.close_options_position(sym)
                    else:
                        close_result = trader.close_position(sym)
                    # Stamp the matching trigger with the Gainz exit info
                    for trig in triggers:
                        if (trig.get("occ_symbol") == sym or trig.get("symbol") == sym) and not trig.get("closed"):
                            trig["exit_reason"] = "gainz_exit"
                            trig["exit_time"] = get_et_now().isoformat()
                            if close_result:
                                trig["close_order_id"] = close_result["order_id"]
                            trig["closed"] = True
                            break
                    journal_file.write_text(json.dumps(triggers, indent=2, default=str))
                    open_directions.pop(sym, None)
                    open_options.pop(sym, None)

        # ── Options stop/target/time-stop monitoring (underlying-based, since no bracket for options) ──
        if not trade_shares and trader and open_options:
            try:
                current_price_bars = alpaca_fetch_bars(symbol, days_back=1, interval="5min")
                if len(current_price_bars) > 0:
                    underlying_price = float(current_price_bars["Close"].iloc[-1])
                    for occ_sym, opt_info in list(open_options.items()):
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

                        if "call" in opt_info["direction"]:
                            if underlying_price <= opt_info["stop_price"]:
                                should_close = True
                                close_reason = "stop"
                            elif underlying_price >= effective_target:
                                should_close = True
                                close_reason = "decay_target"
                        else:  # put
                            if underlying_price >= opt_info["stop_price"]:
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
                        # Tiered stagnation (golden): at 40 min (8 bars), exit flat trades
                        # between -0.1R and +0.2R early. At 60 min, standard stagnation.
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

                                # Tiered stagnation: early exit at bar 8 (40 min) if flat
                                if 38 <= minutes_in_trade < 42:  # ~40 min window (bar 8)
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
                                consecutive_losses += 1
                            elif close_reason == "stagnation":
                                consecutive_losses += 1
                                last_was_stagnation = True
                            elif close_reason == "target":
                                consecutive_losses = 0
                                last_was_stagnation = False
            except Exception as e:
                logger.warning("Options monitoring failed: %s", e)

        # Stop opening NEW trades after 2:00 PM (still monitor existing for exits)
        if now.hour >= 14:
            # After 15:30, if no open positions, we're done for the day
            if now.hour == 15 and now.minute >= 30 and not open_directions:
                logger.info("Loop exit: time stop reached (15:30 ET) and no open positions.")
                break
            if not open_directions:
                logger.info("Loop exit: past 14:00 entry cutoff (%02d:%02d ET) and no open positions.", now.hour, now.minute)
                break
            _write_heartbeat(f"run_day_{symbol}_monitoring_positions")
            time.sleep(300)
            continue

        # Wait for OR to form + scan start delay
        if not is_past_or(min_after_open=scan_start_min):
            logger.info("Waiting for scan window (OR + %d min)...", scan_start_min)
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
            if not open_directions:
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
        verdict = check_sweet_spot(symbol, max_chop=max_chop, regime_guard=regime_guard,
                                   pb_ema=pb_ema, pb_ema_fast=pb_ema_fast, pb_ema_slow=pb_ema_slow)

        # Persist every verdict (rejects + triggers) to a daily JSONL for offline analysis.
        try:
            verdict_record = {"ts": now.isoformat(), "symbol": symbol, **verdict}
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
                # Deduplicate (15 min cooldown, 30 min after stagnation exit)
                cooldown_sec = 1800 if last_was_stagnation else 900
                # Block new entries when a same-direction option is already open
                # (avoids duplicate buys on the same OCC after restart-recovery).
                already_open_same_dir = any(
                    d == trigger["direction"] for d in open_directions.values()
                )
                if already_open_same_dir:
                    logger.info("✗ %s skip [open_position] %s already open in %s — not stacking",
                                symbol, trigger["direction"], list(open_directions.keys()))
                elif last_trigger and (now - last_trigger).total_seconds() < cooldown_sec:
                    logger.debug("Skipping duplicate trigger within cooldown (%d min)", cooldown_sec // 60)
                else:
                    last_trigger = now
                    trades_today += 1
                    if trigger.get("is_flip", False):
                        flip_trades_today += 1
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
                                contract = get_0dte_chain(
                                    symbol, option_type=opt_type,
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

                                    order = trader.place_options_trade(
                                        occ_symbol=contract["occ_symbol"],
                                        direction=trigger["direction"],
                                        qty=num_contracts,
                                        # No limit_price = market order (0DTE needs instant fill)
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
                                        "original_target_dist": abs(trigger["target"] - trigger["entry"]),
                                        "direction": trigger["direction"],
                                        "delta": contract["delta"],
                                        "entry_time": get_et_now(),
                                        "entry_underlying": trigger.get("price", trigger["entry"]),
                                        "max_favorable_excursion": 0.0,  # MFE tracking for stagnation skip
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

        _write_heartbeat(f"run_day_{symbol}_scanning")
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


HEARTBEAT_FILE = Path("/tmp/agent_heartbeat")

def _write_heartbeat(state: str) -> None:
    HEARTBEAT_FILE.write_text(json.dumps({
        "ts": datetime.now(ZoneInfo("UTC")).isoformat(),
        "state": state,
        "pid": os.getpid(),
    }))

def _safe_sleep(seconds: float, label: str = "waiting") -> None:
    """Sleep in 30-second chunks with periodic heartbeat.

    Long time.sleep() causes Docker healthcheck to mark container unhealthy
    (threshold: 600s). This keeps the heartbeat alive during overnight/weekend waits.
    """
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
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
    parser.add_argument("--max-trades-per-day", type=int, default=3, help="Max trades per day (default: 3)")
    parser.add_argument("--max-stops-per-day", type=int, default=1, help="Halt after N stop-outs (default: 1)")
    parser.add_argument("--max-consecutive-losses", type=int, default=2,
                        help="Stop trading after N consecutive losses (default: 2)")
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
    parser.add_argument("--gainz-min-profit-r", type=float, default=0.3,
                        help="Min profit as fraction of R to allow Gainz exit (golden: 0.3, 0=disabled)")
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
                    _safe_sleep(max(sleep_sec, 60), f"daemon_{args.symbol}_pre_market")
                elif now.hour < 16:
                    run_day(args.symbol, args.qty, args.max_chop, paper_trade,
                            max_trades_per_day=args.max_trades_per_day,
                            max_stops_per_day=args.max_stops_per_day,
                            max_consecutive_losses=args.max_consecutive_losses,
                            scan_start_min=args.scan_start_min,
                            gainz_exit=not args.no_gainz_exit,
                            gainz_body_ratio=args.gainz_body_ratio,
                            gainz_rsi_overbought=args.gainz_rsi_overbought,
                            gainz_rsi_oversold=args.gainz_rsi_oversold,
                            gainz_min_profit_r=args.gainz_min_profit_r,
                            trade_shares=args.shares,
                            contracts=args.contracts,
                            target_delta=args.target_delta,
                            cascade_size_low=args.cascade_size_low,
                            cascade_size_mid=args.cascade_size_mid,
                            cascade_size_high=args.cascade_size_high,
                            regime_guard=args.regime_guard,
                            vix_max=args.vix_max,
                            vix_spike_pct=args.vix_spike_pct,
                            pb_ema=not args.no_pb_ema,
                            pb_ema_fast=args.pb_ema_fast,
                            pb_ema_slow=args.pb_ema_slow,
                            verbose_rejects=args.verbose_rejects)
                    # After market close, sleep until next day 9:25 AM
                    tomorrow_925 = (now + timedelta(days=1)).replace(hour=9, minute=25, second=0)
                    sleep_sec = (tomorrow_925 - get_et_now()).total_seconds()
                    logger.info("Market closed. Sleeping %.1f hours until tomorrow...", sleep_sec / 3600)
                    _safe_sleep(max(sleep_sec, 3600), f"daemon_{args.symbol}_post_market")
                else:
                    # After hours — sleep until tomorrow
                    _safe_sleep(3600, f"daemon_{args.symbol}_after_hours")
            else:
                # Weekend — sleep until Monday 9:25
                days_until_monday = (7 - now.weekday()) % 7 or 7
                monday = (now + timedelta(days=days_until_monday)).replace(hour=9, minute=25, second=0)
                sleep_sec = (monday - now).total_seconds()
                logger.info("Weekend. Sleeping %.1f hours until Monday...", sleep_sec / 3600)
                _safe_sleep(max(sleep_sec, 3600), f"daemon_{args.symbol}_weekend")
    else:
        # Single day run
        run_day(args.symbol, args.qty, args.max_chop, paper_trade,
                max_trades_per_day=args.max_trades_per_day,
                max_stops_per_day=args.max_stops_per_day,
                max_consecutive_losses=args.max_consecutive_losses,
                scan_start_min=args.scan_start_min,
                gainz_exit=not args.no_gainz_exit,
                gainz_body_ratio=args.gainz_body_ratio,
                gainz_rsi_overbought=args.gainz_rsi_overbought,
                gainz_rsi_oversold=args.gainz_rsi_oversold,
                gainz_min_profit_r=args.gainz_min_profit_r,
                trade_shares=args.shares,
                contracts=args.contracts,
                target_delta=args.target_delta,
                cascade_size_low=args.cascade_size_low,
                cascade_size_mid=args.cascade_size_mid,
                cascade_size_high=args.cascade_size_high,
                regime_guard=args.regime_guard,
                vix_max=args.vix_max,
                vix_spike_pct=args.vix_spike_pct,
                pb_ema=not args.no_pb_ema,
                pb_ema_fast=args.pb_ema_fast,
                pb_ema_slow=args.pb_ema_slow,
                verbose_rejects=args.verbose_rejects)


if __name__ == "__main__":
    main()

