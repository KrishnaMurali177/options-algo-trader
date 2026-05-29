"""Weekly options agent — core entry/exit/lifecycle logic.

Imports from shared core (src/) read-only. All weekly-specific logic
lives here and in the weekly/ subpackages.
"""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from src.models.market_data import MarketIndicators
from src.momentum_cascade import MomentumCascadeDetector
from src.utils.alpaca_data import fetch_daily_bars, is_trading_day
from src.utils.alpaca_paper import AlpacaPaperTrader
from src.utils.choppiness import compute_choppiness
from src.utils.discord_notifier import DiscordNotifier
from src.utils.gainz import gainz_signal
from src.utils.quality_scorer import compute_quality_score

from weekly.chain.weekly_chain import get_weekly_chain
from weekly.signals.daily_momentum import DailyMomentumAnalyzer
from weekly.signals.daily_range import DailyRangeAnalyzer
from weekly.signals.weekly_indicators import build_weekly_indicators
from weekly.state.position_manager import WeeklyPositionManager

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

QUALITY_MIN = 3
QUALITY_MAX = 7
CHOP_MIN = 2
CHOP_MAX = 5
EXPLOSION_MIN = 2
CHOP_LOOKBACK = 10


# ── VIX ──

def _fetch_vix() -> float:
    try:
        vix_df = yf.download("^VIX", period="5d", interval="1d", progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        return float(vix_df["Close"].iloc[-1])
    except Exception as e:
        logger.warning("VIX fetch failed (%s) — defaulting to 20.0", e)
        return 20.0


# ── Entry Logic ──

def check_weekly_entry(
    symbol: str,
    daily_bars: pd.DataFrame,
    indicators: MarketIndicators,
    *,
    min_dte: int = 3,
    max_dte: int = 7,
    target_delta: float = 0.35,
    max_premium: float = 500.0,
    verbose_rejects: bool = False,
) -> dict | None:
    """Check if conditions warrant a weekly options entry.

    Returns a trigger dict ready for WeeklyPositionManager.create_position(),
    or None if no entry.
    """
    now = datetime.now(ET)

    # Day-of-week gate: Mon-Wed only
    if now.weekday() > 2:
        if verbose_rejects:
            logger.info("[%s] Weekly reject [day_gate] %s — only Mon-Wed entries",
                        symbol, now.strftime("%A"))
        return None

    # Time window: 10:00-11:00 ET only
    if now.hour < 10 or now.hour >= 11:
        if verbose_rejects:
            logger.info("[%s] Weekly reject [time_gate] %s — entry window 10:00-11:00",
                        symbol, now.strftime("%H:%M"))
        return None

    if len(daily_bars) < 10:
        logger.warning("[%s] Weekly reject — insufficient daily bars (%d)", symbol, len(daily_bars))
        return None

    # Signal analysis
    range_analyzer = DailyRangeAnalyzer()
    momentum_analyzer = DailyMomentumAnalyzer()

    range_result = range_analyzer.analyze(daily_bars, indicators)
    momentum_result = momentum_analyzer.analyze(daily_bars, indicators)

    or_direction = range_result.breakout_direction
    or_momentum = range_result.momentum_score
    or_confirmed = range_result.breakout_confirmed
    recent_dir = momentum_result.direction
    recent_momentum = momentum_result.momentum_score

    # Determine direction from SMA crossover + daily close
    if indicators.sma_20 > indicators.sma_50 and indicators.current_price > indicators.sma_20:
        direction = "buy_call"
    elif indicators.sma_20 < indicators.sma_50 and indicators.current_price < indicators.sma_20:
        direction = "buy_put"
    else:
        if verbose_rejects:
            logger.info("[%s] Weekly reject [direction] SMA20/50 not aligned with price", symbol)
        return None

    # Quality score
    quality_result = compute_quality_score(
        direction=direction,
        current_price=indicators.current_price,
        sma_20=indicators.sma_20,
        sma_50=indicators.sma_50,
        vix=indicators.vix,
        volume=indicators.volume,
        volume_sma_20=indicators.volume_sma_20,
        or_direction=or_direction,
        or_momentum=or_momentum,
        or_confirmed=or_confirmed,
        recent_dir=recent_dir,
        recent_momentum=recent_momentum,
        zlema_trend=indicators.zlema_trend,
    )
    quality = quality_result.score

    if quality < QUALITY_MIN or quality > QUALITY_MAX:
        if verbose_rejects:
            logger.info("[%s] Weekly reject [quality] Q=%d outside [%d,%d]",
                        symbol, quality, QUALITY_MIN, QUALITY_MAX)
        return None

    # Choppiness
    chop_result = compute_choppiness(daily_bars, lookback=CHOP_LOOKBACK, vix=indicators.vix)
    chop = chop_result.chop_score

    if chop < CHOP_MIN or chop > CHOP_MAX:
        if verbose_rejects:
            logger.info("[%s] Weekly reject [chop] chop=%d outside [%d,%d]",
                        symbol, chop, CHOP_MIN, CHOP_MAX)
        return None

    # Explosion
    cascade = MomentumCascadeDetector()
    cascade_result = cascade.analyze(indicators, quality, or_momentum, recent_momentum)
    explosion = cascade_result.explosion_score

    if explosion < EXPLOSION_MIN:
        if verbose_rejects:
            logger.info("[%s] Weekly reject [explosion] E=%d < %d",
                        symbol, explosion, EXPLOSION_MIN)
        return None

    # All gates passed — build trigger
    option_type = "call" if direction == "buy_call" else "put"
    chain = get_weekly_chain(
        symbol, option_type,
        target_delta=target_delta,
        min_dte=min_dte, max_dte=max_dte,
        spot_price=indicators.current_price,
    )
    if chain is None:
        logger.warning("[%s] Weekly reject — no suitable chain found", symbol)
        return None

    # Contract sizing
    mid = chain["mid"]
    if mid <= 0:
        logger.warning("[%s] Weekly reject — zero premium", symbol)
        return None
    contracts = max(1, int(max_premium / (mid * 100)))

    # Stop/target from daily ATR
    atr = indicators.atr_14
    if direction == "buy_call":
        stop = indicators.current_price - 1.5 * atr
        target = indicators.current_price + 2.0 * atr
    else:
        stop = indicators.current_price + 1.5 * atr
        target = indicators.current_price - 2.0 * atr

    risk = abs(indicators.current_price - stop)

    logger.info(
        "[%s] WEEKLY ENTRY: %s Q=%d E=%d C=%d Mom=%+d Entry=$%.2f Stop=$%.2f Target=$%.2f "
        "Option=%s delta=%.2f mid=$%.2f x%d DTE=%d",
        symbol, direction.upper(), quality, explosion, chop, or_momentum,
        indicators.current_price, stop, target,
        chain["occ_symbol"], chain["delta"], mid, contracts, chain["dte"],
    )

    return {
        "entry_date": date.today().isoformat(),
        "symbol": symbol,
        "direction": direction,
        "occ_symbol": chain["occ_symbol"],
        "strike": chain["strike"],
        "expiration": chain["expiration"],
        "dte_at_entry": chain["dte"],
        "entry_underlying": indicators.current_price,
        "entry_premium": mid,
        "num_contracts": contracts,
        "quality_at_entry": quality,
        "explosion_at_entry": explosion,
        "chop_at_entry": chop,
        "stop_underlying": round(stop, 2),
        "target_underlying": round(target, 2),
        "original_risk": round(risk, 2),
        "trailing_stop": None,
        "max_favorable_excursion": 0.0,
        "trade_mode": "weekly_option",
        "option_delta": chain["delta"],
        "option_type": option_type,
        "or_momentum": or_momentum,
        "recent_momentum": recent_momentum,
    }


# ── Exit Logic ──

def evaluate_open_positions(
    symbol: str,
    daily_bars: pd.DataFrame,
    indicators: MarketIndicators,
    trader: AlpacaPaperTrader,
    state_mgr: WeeklyPositionManager,
    discord: DiscordNotifier | None = None,
) -> list[str]:
    """Evaluate all open weekly positions. Returns list of closed occ_symbols."""
    positions = state_mgr.load_open_positions()
    closed = []

    for occ_symbol, pos in positions.items():
        if pos.get("symbol") != symbol:
            continue

        exit_data = _check_exit_conditions(pos, daily_bars, indicators)

        # Build daily evaluation
        price = indicators.current_price
        entry = pos["entry_underlying"]
        risk = pos.get("original_risk", 1.0)
        pnl_r = (price - entry) / risk if pos["direction"] == "buy_call" else (entry - price) / risk

        # Update MFE
        mfe = max(pos.get("max_favorable_excursion", 0.0), max(0, pnl_r))

        # Update trailing stop
        trailing = _update_trailing_stop(pos, pnl_r)

        # Get option mid for tracking
        option_mid = None
        try:
            quote = trader.get_option_quote(occ_symbol)
            if quote:
                option_mid = quote.get("mid")
        except Exception:
            pass

        dte_remaining = _dte_remaining(pos)

        eval_record = {
            "date": date.today().isoformat(),
            "time": datetime.now(ET).strftime("%H:%M"),
            "underlying": price,
            "option_mid": option_mid,
            "quality": indicators.rsi_14,  # re-scored quality tracked in agent loop
            "chop": None,
            "trailing_stop": trailing,
            "pnl_r": round(pnl_r, 3),
            "dte_remaining": dte_remaining,
            "max_favorable_excursion": round(mfe, 3),
        }

        state_mgr.add_daily_evaluation(occ_symbol, eval_record)

        if exit_data:
            try:
                trader.close_options_position(occ_symbol)
                exit_data["exit_underlying"] = price
                exit_data["exit_premium"] = option_mid
                state_mgr.close_position(occ_symbol, exit_data)
                closed.append(occ_symbol)

                if discord:
                    discord._post({"content": (
                        f"Weekly EXIT {symbol} {pos['direction']} — {exit_data['exit_reason']}\n"
                        f"P&L: {pnl_r:+.2f}R | DTE remaining: {dte_remaining}"
                    )})

                logger.info("[%s] Weekly closed %s — %s (%.2fR)",
                            symbol, occ_symbol, exit_data["exit_reason"], pnl_r)
            except Exception as e:
                logger.error("[%s] Failed to close %s: %s", symbol, occ_symbol, e)

    return closed


def _check_exit_conditions(
    pos: dict,
    daily_bars: pd.DataFrame,
    indicators: MarketIndicators,
) -> dict | None:
    """Check all exit conditions for an open position. Returns exit_data or None."""
    price = indicators.current_price
    entry = pos["entry_underlying"]
    risk = pos.get("original_risk", 1.0)
    direction = pos["direction"]

    if direction == "buy_call":
        pnl_r = (price - entry) / risk if risk > 0 else 0
    else:
        pnl_r = (entry - price) / risk if risk > 0 else 0

    mfe = max(pos.get("max_favorable_excursion", 0.0), max(0, pnl_r))
    stop = pos.get("stop_underlying", 0)
    target = pos.get("target_underlying", 0)
    trailing = pos.get("trailing_stop")

    # 1. Gap stop: underlying gapped > 1.5x risk from yesterday's close
    if len(daily_bars) >= 2:
        prev_close = float(daily_bars.iloc[-2]["Close"])
        gap = abs(price - prev_close)
        if gap > 1.5 * risk:
            if (direction == "buy_call" and price < prev_close) or \
               (direction == "buy_put" and price > prev_close):
                return {"exit_reason": "gap_stop", "exit_date": date.today().isoformat()}

    # 2. Trailing stop
    if trailing is not None:
        if direction == "buy_call" and price <= trailing:
            return {"exit_reason": "trailing_stop", "exit_date": date.today().isoformat()}
        elif direction == "buy_put" and price >= trailing:
            return {"exit_reason": "trailing_stop", "exit_date": date.today().isoformat()}

    # 3. Hard stop
    if direction == "buy_call" and price <= stop:
        return {"exit_reason": "stop_loss", "exit_date": date.today().isoformat()}
    elif direction == "buy_put" and price >= stop:
        return {"exit_reason": "stop_loss", "exit_date": date.today().isoformat()}

    # 4. Decay target
    sessions = _sessions_since_entry(pos)
    decay_factor = max(0.5, 0.5 ** (sessions / 2.0))
    original_target_dist = abs(target - entry)
    effective_target_dist = original_target_dist * decay_factor
    if direction == "buy_call":
        effective_target = entry + effective_target_dist
        if price >= effective_target:
            return {"exit_reason": "decay_target", "exit_date": date.today().isoformat()}
    else:
        effective_target = entry - effective_target_dist
        if price <= effective_target:
            return {"exit_reason": "decay_target", "exit_date": date.today().isoformat()}

    # 5. DTE check: close if DTE <= 1
    dte = _dte_remaining(pos)
    if dte <= 1:
        return {"exit_reason": "dte_expiry", "exit_date": date.today().isoformat()}

    # 6. Regime degradation: quality < 2 or chop > 7
    chop_result = compute_choppiness(daily_bars, lookback=CHOP_LOOKBACK, vix=indicators.vix)
    if chop_result.chop_score > 7:
        return {"exit_reason": "regime_degradation", "exit_date": date.today().isoformat()}

    # 7. Stagnation: 2+ days with no progress, MFE < 0.5R
    if sessions >= 2 and mfe < 0.5 and abs(pnl_r) < 0.3:
        return {"exit_reason": "stagnation", "exit_date": date.today().isoformat()}

    # 8. Gainz reversal on daily bar
    if len(daily_bars) >= 1:
        bar = daily_bars.iloc[-1]
        g = gainz_signal(
            float(bar["Open"]), float(bar["High"]), float(bar["Low"]), float(bar["Close"]),
            indicators.rsi_14,
            body_ratio_min=0.6, rsi_overbought=75.0, rsi_oversold=25.0,
        )
        if g == "sell" and direction == "buy_call" and pnl_r > 0.3:
            return {"exit_reason": "gainz_exit", "exit_date": date.today().isoformat()}
        if g == "buy" and direction == "buy_put" and pnl_r > 0.3:
            return {"exit_reason": "gainz_exit", "exit_date": date.today().isoformat()}

    return None


def _update_trailing_stop(pos: dict, pnl_r: float) -> float | None:
    """Update trailing stop based on current P&L in R-multiples."""
    entry = pos["entry_underlying"]
    risk = pos.get("original_risk", 1.0)
    direction = pos["direction"]
    current_trail = pos.get("trailing_stop")

    new_trail = current_trail

    if pnl_r >= 2.0:
        # Trail at +1R
        offset = 1.0 * risk
        if direction == "buy_call":
            candidate = entry + offset
        else:
            candidate = entry - offset
        new_trail = _better_trail(current_trail, candidate, direction)
    elif pnl_r >= 1.5:
        # Trail at +0.5R
        offset = 0.5 * risk
        if direction == "buy_call":
            candidate = entry + offset
        else:
            candidate = entry - offset
        new_trail = _better_trail(current_trail, candidate, direction)
    elif pnl_r >= 1.0:
        # Trail at breakeven
        new_trail = _better_trail(current_trail, entry, direction)

    return new_trail


def _better_trail(current: float | None, candidate: float, direction: str) -> float:
    """Return the tighter (more protective) trailing stop."""
    if current is None:
        return candidate
    if direction == "buy_call":
        return max(current, candidate)
    else:
        return min(current, candidate)


def _sessions_since_entry(pos: dict) -> int:
    """Count business days since entry."""
    entry_str = pos.get("entry_date", date.today().isoformat())
    entry_date = date.fromisoformat(entry_str)
    today = date.today()
    count = 0
    d = entry_date
    while d < today:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


def _dte_remaining(pos: dict) -> int:
    """Days to expiration remaining."""
    exp_str = pos.get("expiration", date.today().isoformat())
    exp_date = date.fromisoformat(exp_str)
    return max(0, (exp_date - date.today()).days)


# ── Daily Lifecycle ──

def run_weekly_day(
    symbol: str,
    trader: AlpacaPaperTrader,
    state_mgr: WeeklyPositionManager,
    discord: DiscordNotifier | None = None,
    *,
    min_dte: int = 3,
    max_dte: int = 7,
    target_delta: float = 0.35,
    max_premium: float = 500.0,
    max_open: int = 2,
    verbose_rejects: bool = False,
):
    """Run one trading day for the weekly agent."""
    now = datetime.now(ET)
    today = now.date()

    logger.info("=" * 60)
    logger.info("Weekly Agent: %s — %s", symbol, today)
    logger.info("=" * 60)

    # Fetch daily bars
    daily_bars = fetch_daily_bars(symbol, days_back=90)
    if daily_bars is None or daily_bars.empty:
        logger.error("[%s] Failed to fetch daily bars", symbol)
        return

    vix = _fetch_vix()
    indicators = build_weekly_indicators(daily_bars, symbol, vix)

    scans = 0
    triggers = 0
    entry_placed = False

    # Main intraday loop
    while True:
        now = datetime.now(ET)

        if now.hour >= 16:
            break

        # Morning evaluation (9:30-10:00): check open positions
        if now.hour == 9 and now.minute >= 30 or now.hour == 10 and now.minute < 0:
            # Re-fetch for freshness
            daily_bars = fetch_daily_bars(symbol, days_back=90)
            if daily_bars is not None and not daily_bars.empty:
                indicators = build_weekly_indicators(daily_bars, symbol, _fetch_vix())
                closed = evaluate_open_positions(symbol, daily_bars, indicators, trader, state_mgr, discord)
                if closed:
                    logger.info("[%s] Closed %d positions in morning eval", symbol, len(closed))

        # Entry window (10:00-11:00): scan for new entry
        if 10 <= now.hour < 11 and not entry_placed:
            scans += 1

            open_count = state_mgr.get_open_count(symbol)
            weekly_stops = state_mgr.get_weekly_stop_count(symbol)

            if open_count >= max_open:
                if verbose_rejects:
                    logger.info("[%s] Weekly skip — %d/%d positions open", symbol, open_count, max_open)
            elif weekly_stops >= 1:
                if verbose_rejects:
                    logger.info("[%s] Weekly skip — %d stops this week, halting new entries", symbol, weekly_stops)
            else:
                daily_bars = fetch_daily_bars(symbol, days_back=90)
                if daily_bars is not None and not daily_bars.empty:
                    indicators = build_weekly_indicators(daily_bars, symbol, _fetch_vix())

                    trigger = check_weekly_entry(
                        symbol, daily_bars, indicators,
                        min_dte=min_dte, max_dte=max_dte,
                        target_delta=target_delta, max_premium=max_premium,
                        verbose_rejects=verbose_rejects,
                    )

                    if trigger:
                        try:
                            order = trader.place_options_trade(
                                trigger["occ_symbol"],
                                trigger["direction"],
                                qty=trigger["num_contracts"],
                                time_in_force="gtc",
                            )
                            trigger["order_id"] = order.get("order_id", "")
                            state_mgr.create_position(trigger)
                            triggers += 1
                            entry_placed = True

                            if discord:
                                discord._post({"content": (
                                    f"Weekly ENTRY {symbol} {trigger['direction'].upper()}\n"
                                    f"Q={trigger['quality_at_entry']} E={trigger['explosion_at_entry']} "
                                    f"C={trigger['chop_at_entry']}\n"
                                    f"Option: {trigger['occ_symbol']} delta={trigger['option_delta']:.2f} "
                                    f"mid=${trigger['entry_premium']:.2f} x{trigger['num_contracts']}\n"
                                    f"DTE={trigger['dte_at_entry']} Stop=${trigger['stop_underlying']:.2f} "
                                    f"Target=${trigger['target_underlying']:.2f}"
                                )})
                        except Exception as e:
                            logger.error("[%s] Failed to place weekly order: %s", symbol, e)

        # Midday monitoring (11:00-15:30): check stops every 30 min
        if 11 <= now.hour < 16:
            daily_bars = fetch_daily_bars(symbol, days_back=90)
            if daily_bars is not None and not daily_bars.empty:
                indicators = build_weekly_indicators(daily_bars, symbol, _fetch_vix())
                closed = evaluate_open_positions(symbol, daily_bars, indicators, trader, state_mgr, discord)
                if closed:
                    logger.info("[%s] Closed %d positions in midday check", symbol, len(closed))

        # Sleep until next check
        if now.hour < 11:
            sleep_sec = 300  # 5 min during entry window
        else:
            sleep_sec = 1800  # 30 min during monitoring
        _safe_sleep(sleep_sec)

    # EOD summary
    open_positions = state_mgr.load_open_positions()
    open_for_symbol = {k: v for k, v in open_positions.items() if v.get("symbol") == symbol}

    logger.info("=" * 60)
    logger.info("[%s] EOD: %d scans, %d triggers, %d open positions",
                symbol, scans, triggers, len(open_for_symbol))
    logger.info("=" * 60)

    if discord and open_for_symbol:
        lines = [f"Weekly EOD {symbol} — {len(open_for_symbol)} open:"]
        for occ, pos in open_for_symbol.items():
            dte = _dte_remaining(pos)
            entry = pos["entry_underlying"]
            price = indicators.current_price
            risk = pos.get("original_risk", 1.0)
            pnl_r = (price - entry) / risk if pos["direction"] == "buy_call" else (entry - price) / risk
            lines.append(f"  {occ} DTE={dte} P&L={pnl_r:+.2f}R trail={pos.get('trailing_stop', '-')}")
        discord._post({"content": "\n".join(lines)})


def _safe_sleep(seconds: float) -> None:
    """Wall-clock aware sleep (survives system suspend)."""
    deadline = time.time() + seconds
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(300, remaining))
