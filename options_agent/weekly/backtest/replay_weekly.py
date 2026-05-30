"""Weekly options replay engine — backtest weekly trades on historical daily bars.

Mirrors replay_sweet_spot.py architecture but handles multi-day position lifecycle.
Calls the exact same signal functions as the live weekly agent for signal fidelity.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.momentum_cascade import MomentumCascadeDetector
from src.utils.choppiness import compute_choppiness
from src.utils.gainz import gainz_signal
from src.utils.quality_scorer import compute_quality_score

from weekly.backtest.option_pricing import (
    estimate_delta,
    estimate_iv_from_atr,
    fetch_weekly_option_bars,
    nearest_friday,
    resolve_weekly_otm,
    sessions_between,
    synth_weekly_pnl,
    synth_weekly_premium,
    weekly_option_close_on_date,
)
from weekly.signals.daily_momentum import DailyMomentumAnalyzer
from weekly.signals.daily_range import DailyRangeAnalyzer
from weekly.signals.weekly_indicators import build_weekly_indicators

logger = logging.getLogger(__name__)


# ── Config ──

@dataclass
class WeeklyBacktestConfig:
    """All tunable parameters for the weekly backtester."""
    # Signal gates
    quality_min: int = 3
    quality_max: int = 7
    chop_min: int = 2
    chop_max: int = 5
    explosion_min: int = 2
    chop_lookback: int = 10

    # Entry rules
    entry_days: tuple[int, ...] = (0, 1, 2)  # Mon, Tue, Wed
    max_open_per_symbol: int = 2
    max_stops_per_week: int = 1

    # Option targeting
    target_delta: float = 0.35
    min_dte: int = 3
    max_dte: int = 7

    # Risk geometry
    stop_atr_mult: float = 1.5
    target_atr_mult: float = 2.0

    # Trailing stop tiers (R-multiples)
    trail_tier_1: float = 1.0    # at 1R → trail at breakeven
    trail_tier_2: float = 1.5    # at 1.5R → trail at +0.5R
    trail_tier_3: float = 2.0    # at 2R → trail at +1R
    trailing_enabled: bool = True

    # Decay target
    decay_halflife_sessions: float = 2.0
    decay_target_floor: float = 0.5

    # Exit rules
    stagnation_sessions: int = 2
    stagnation_mfe_threshold: float = 0.5
    stagnation_pnl_threshold: float = 0.3
    regime_degradation_chop: int = 7

    # Gainz
    gainz_exit: bool = True
    gainz_body_ratio: float = 0.6
    gainz_rsi_overbought: float = 75.0
    gainz_rsi_oversold: float = 25.0
    gainz_min_profit_r: float = 0.3

    # Option pricing
    real_options: bool = True
    synth_delta: float = 0.35
    synth_gamma: float = 0.02
    slippage: float = 0.0

    # VIX filter
    vix_max: float = 30.0
    vix_spike_pct: float = 20.0


@dataclass
class WeeklyPosition:
    """In-flight position tracked by the backtester."""
    entry_date: date
    symbol: str
    direction: str
    quality: int
    explosion: int
    chop: int
    or_momentum: int
    recent_momentum: int
    entry_underlying: float
    stop_underlying: float
    target_underlying: float
    original_risk: float
    trailing_stop: float | None = None
    max_favorable_excursion: float = 0.0

    # Option fields
    occ_symbol: str | None = None
    strike: float | None = None
    expiration: date | None = None
    dte_at_entry: int = 5
    entry_premium: float | None = None
    option_delta: float = 0.35
    pricing: str = "synth"

    # Tracking
    days_held: int = 0
    daily_evals: list[dict] = field(default_factory=list)
    overnight_gap_impact: float = 0.0
    prev_close: float | None = None

    # Exit
    exit_date: date | None = None
    exit_underlying: float | None = None
    exit_premium: float | None = None
    exit_reason: str | None = None

    def to_trigger_dict(self) -> dict:
        """Convert to trigger dict compatible with 0DTE reporting."""
        underlying_move = (self.exit_underlying or self.entry_underlying) - self.entry_underlying
        if self.direction == "buy_put":
            underlying_move = -underlying_move

        pnl = underlying_move
        is_winner = pnl > 0

        option_pnl = None
        if self.entry_premium is not None and self.exit_premium is not None:
            option_pnl = self.exit_premium - self.entry_premium
            is_winner = option_pnl > 0

        return {
            "date": self.entry_date.isoformat(),
            "time": "10:30",
            "direction": self.direction,
            "quality": self.quality,
            "explosion": self.explosion,
            "chop": self.chop,
            "momentum": self.or_momentum,
            "recent_momentum": self.recent_momentum,
            "entry": self.entry_underlying,
            "stop": self.stop_underlying,
            "target": self.target_underlying,
            "exit_price": self.exit_underlying,
            "outcome": self.exit_reason or "open",
            "pnl": round(pnl, 4),
            "underlying_move": round(underlying_move, 4),
            "is_winner": is_winner,
            "pricing": self.pricing,
            "occ_symbol": self.occ_symbol,
            "est_premium": self.entry_premium,
            "option_pnl_100x": round(option_pnl * 100, 2) if option_pnl is not None else None,
            # Weekly-specific
            "entry_date": self.entry_date.isoformat(),
            "exit_date": self.exit_date.isoformat() if self.exit_date else None,
            "days_held": self.days_held,
            "dte_at_entry": self.dte_at_entry,
            "dte_at_exit": (self.expiration - self.exit_date).days if self.expiration and self.exit_date else None,
            "expiration": self.expiration.isoformat() if self.expiration else None,
            "strike": self.strike,
            "option_delta": self.option_delta,
            "trailing_stop_at_exit": self.trailing_stop,
            "max_favorable_excursion_r": round(self.max_favorable_excursion, 3),
            "overnight_gap_impact": round(self.overnight_gap_impact, 4),
            "trade_mode": "weekly_option",
        }


# ── Core Replay ──

def replay_weekly(
    daily_bars: pd.DataFrame,
    symbol: str,
    config: WeeklyBacktestConfig,
    vix_map: dict[date, float] | None = None,
) -> list[dict]:
    """Run the weekly backtester over historical daily bars.

    Args:
        daily_bars: DataFrame with OHLCV, date-indexed.
        symbol: Ticker symbol.
        config: Backtester configuration.
        vix_map: Optional date → VIX mapping. Defaults to 20.0 if absent.

    Returns:
        List of trigger dicts (completed trades).
    """
    if daily_bars is None or len(daily_bars) < 30:
        logger.warning("Insufficient daily bars for %s (%d)", symbol, len(daily_bars) if daily_bars is not None else 0)
        return []

    # Normalize index to dates for iteration
    if daily_bars.index.tz is not None:
        _idx_dates = daily_bars.index.tz_convert("America/New_York")
    else:
        _idx_dates = daily_bars.index
    trading_days = sorted(set(
        d.date() if hasattr(d, 'date') else d
        for d in _idx_dates
    ))

    open_positions: list[WeeklyPosition] = []
    completed: list[dict] = []
    weekly_stops: dict[tuple[int, int], int] = {}  # (year, week) → count

    range_analyzer = DailyRangeAnalyzer()
    momentum_analyzer = DailyMomentumAnalyzer()
    cascade_detector = MomentumCascadeDetector()

    prev_vix = 20.0

    for day_idx, trade_date in enumerate(trading_days):
        if day_idx < 30:
            continue  # warmup

        vix = vix_map.get(trade_date, 20.0) if vix_map else 20.0

        # VIX filter
        if vix > config.vix_max:
            continue
        if config.vix_spike_pct > 0 and prev_vix > 0:
            spike = (vix - prev_vix) / prev_vix * 100
            if spike > config.vix_spike_pct:
                prev_vix = vix
                continue
        prev_vix = vix

        ts = pd.Timestamp(trade_date)
        if daily_bars.index.tz is not None:
            ts = ts.tz_localize(daily_bars.index.tz)
        bars_to_date = daily_bars[daily_bars.index <= ts]
        if len(bars_to_date) < 15:
            continue

        indicators = build_weekly_indicators(bars_to_date, symbol, vix)

        # PHASE 1: Evaluate exits
        still_open = []
        for pos in open_positions:
            pos.days_held = sessions_between(pos.entry_date, trade_date)

            # Overnight gap tracking
            today_open = float(bars_to_date.iloc[-1]["Open"])
            if pos.prev_close is not None:
                gap = today_open - pos.prev_close
                if pos.direction == "buy_call" and gap < 0:
                    pos.overnight_gap_impact += abs(gap)
                elif pos.direction == "buy_put" and gap > 0:
                    pos.overnight_gap_impact += abs(gap)
            pos.prev_close = float(bars_to_date.iloc[-1]["Close"])

            pnl_r = _compute_pnl_r(pos, indicators.current_price)
            pos.max_favorable_excursion = max(pos.max_favorable_excursion, max(0, pnl_r))

            if config.trailing_enabled:
                _update_trailing(pos, pnl_r, config)

            exit_reason = _evaluate_exits(pos, bars_to_date, indicators, trade_date, config, vix)

            if exit_reason:
                pos.exit_date = trade_date
                pos.exit_underlying = indicators.current_price
                pos.exit_reason = exit_reason
                _price_exit(pos, config)
                completed.append(pos.to_trigger_dict())

                if exit_reason == "stop_loss":
                    iso = trade_date.isocalendar()
                    wk = (iso[0], iso[1])
                    weekly_stops[wk] = weekly_stops.get(wk, 0) + 1

                logger.debug("[%s] Exit %s on %s — %s (%.2fR)", symbol, pos.occ_symbol, trade_date, exit_reason, pnl_r)
            else:
                still_open.append(pos)

        open_positions = still_open

        # PHASE 2: Scan for new entry
        if trade_date.weekday() not in config.entry_days:
            continue

        open_count = sum(1 for p in open_positions if p.symbol == symbol)
        if open_count >= config.max_open_per_symbol:
            continue

        iso = trade_date.isocalendar()
        wk = (iso[0], iso[1])
        if weekly_stops.get(wk, 0) >= config.max_stops_per_week:
            continue

        new_pos = _scan_entry(
            symbol, bars_to_date, indicators, trade_date,
            config, range_analyzer, momentum_analyzer, cascade_detector, vix,
        )
        if new_pos:
            new_pos.prev_close = float(bars_to_date.iloc[-1]["Close"])
            open_positions.append(new_pos)
            logger.debug(
                "[%s] Entry on %s — %s Q=%d E=%d C=%d",
                symbol, trade_date, new_pos.direction, new_pos.quality, new_pos.explosion, new_pos.chop,
            )

    # Force-close remaining
    for pos in open_positions:
        pos.exit_date = trading_days[-1]
        pos.exit_underlying = float(daily_bars.iloc[-1]["Close"])
        pos.exit_reason = "backtest_end"
        _price_exit(pos, config)
        completed.append(pos.to_trigger_dict())

    return completed


# ── Entry Scanning ──

def _scan_entry(
    symbol: str,
    bars_to_date: pd.DataFrame,
    indicators,
    trade_date: date,
    config: WeeklyBacktestConfig,
    range_analyzer: DailyRangeAnalyzer,
    momentum_analyzer: DailyMomentumAnalyzer,
    cascade_detector: MomentumCascadeDetector,
    vix: float,
) -> WeeklyPosition | None:
    """Scan for weekly entry — mirrors check_weekly_entry() from agent.py."""

    range_result = range_analyzer.analyze(bars_to_date, indicators)
    momentum_result = momentum_analyzer.analyze(bars_to_date, indicators)

    or_direction = range_result.breakout_direction
    or_momentum = range_result.momentum_score
    or_confirmed = range_result.breakout_confirmed
    recent_dir = momentum_result.direction
    recent_momentum = momentum_result.momentum_score

    # Direction from SMA crossover (same as live agent)
    if indicators.sma_20 > indicators.sma_50 and indicators.current_price > indicators.sma_20:
        direction = "buy_call"
    elif indicators.sma_20 < indicators.sma_50 and indicators.current_price < indicators.sma_20:
        direction = "buy_put"
    else:
        return None

    # Quality
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
    if quality < config.quality_min or quality > config.quality_max:
        return None

    # Choppiness
    chop_result = compute_choppiness(bars_to_date, lookback=config.chop_lookback, vix=vix)
    chop = chop_result.chop_score
    if chop < config.chop_min or chop > config.chop_max:
        return None

    # Explosion
    cascade_result = cascade_detector.analyze(indicators, quality, or_momentum, recent_momentum)
    explosion = cascade_result.explosion_score
    if explosion < config.explosion_min:
        return None

    # Stop/target from ATR
    atr = indicators.atr_14
    price = indicators.current_price
    if direction == "buy_call":
        stop = price - config.stop_atr_mult * atr
        target = price + config.target_atr_mult * atr
    else:
        stop = price + config.stop_atr_mult * atr
        target = price - config.target_atr_mult * atr
    risk = abs(price - stop)

    # Resolve option chain
    expiration = nearest_friday(trade_date, config.min_dte, config.max_dte)
    if expiration is None:
        return None
    dte = (expiration - trade_date).days

    option_type = "call" if direction == "buy_call" else "put"
    occ_symbol = None
    strike = None
    entry_premium = None
    option_delta = config.target_delta
    pricing = "synth"

    if config.real_options:
        iv = estimate_iv_from_atr(atr, price)
        chain = resolve_weekly_otm(
            symbol, trade_date, option_type, price,
            target_delta=config.target_delta,
            expiration=expiration, iv=iv,
        )
        if chain:
            occ_symbol = chain["occ_symbol"]
            strike = chain["strike"]
            option_delta = chain["estimated_delta"]
            option_bars = fetch_weekly_option_bars(occ_symbol, trade_date, expiration)
            entry_premium = weekly_option_close_on_date(option_bars, trade_date)
            if entry_premium is not None and entry_premium > 0:
                pricing = "real"

    if entry_premium is None or entry_premium <= 0:
        iv = estimate_iv_from_atr(atr, price)
        # Estimate OTM strike for synth pricing
        if strike is None:
            if option_type == "call":
                strike = round(price * (1 + 0.02), 0)  # ~2% OTM
            else:
                strike = round(price * (1 - 0.02), 0)
        entry_premium = synth_weekly_premium(price, strike, dte, atr, vix, option_type)
        pricing = "synth"

    return WeeklyPosition(
        entry_date=trade_date,
        symbol=symbol,
        direction=direction,
        quality=quality,
        explosion=explosion,
        chop=chop,
        or_momentum=or_momentum,
        recent_momentum=recent_momentum,
        entry_underlying=price,
        stop_underlying=round(stop, 2),
        target_underlying=round(target, 2),
        original_risk=round(risk, 2),
        occ_symbol=occ_symbol,
        strike=strike,
        expiration=expiration,
        dte_at_entry=dte,
        entry_premium=entry_premium,
        option_delta=option_delta,
        pricing=pricing,
    )


# ── Exit Evaluation ──

def _evaluate_exits(
    pos: WeeklyPosition,
    bars_to_date: pd.DataFrame,
    indicators,
    trade_date: date,
    config: WeeklyBacktestConfig,
    vix: float,
) -> str | None:
    """Check all exit conditions — mirrors _check_exit_conditions() from agent.py."""
    price = indicators.current_price
    entry = pos.entry_underlying
    risk = pos.original_risk if pos.original_risk > 0 else 1.0
    direction = pos.direction
    pnl_r = _compute_pnl_r(pos, price)

    # 1. Gap stop
    if len(bars_to_date) >= 2:
        prev_close = float(bars_to_date.iloc[-2]["Close"])
        today_open = float(bars_to_date.iloc[-1]["Open"])
        gap = abs(today_open - prev_close)
        if gap > 1.5 * risk:
            if (direction == "buy_call" and today_open < prev_close) or \
               (direction == "buy_put" and today_open > prev_close):
                return "gap_stop"

    # 2. Trailing stop
    if pos.trailing_stop is not None:
        if direction == "buy_call" and price <= pos.trailing_stop:
            return "trailing_stop"
        elif direction == "buy_put" and price >= pos.trailing_stop:
            return "trailing_stop"

    # 3. Hard stop
    if direction == "buy_call" and price <= pos.stop_underlying:
        return "stop_loss"
    elif direction == "buy_put" and price >= pos.stop_underlying:
        return "stop_loss"

    # 4. Decay target
    sessions = sessions_between(pos.entry_date, trade_date)
    hl = config.decay_halflife_sessions
    decay_factor = max(config.decay_target_floor, 0.5 ** (sessions / hl)) if hl > 0 else 1.0
    target_dist = abs(pos.target_underlying - entry)
    eff_target_dist = target_dist * decay_factor
    if direction == "buy_call":
        if price >= entry + eff_target_dist:
            return "decay_target"
    else:
        if price <= entry - eff_target_dist:
            return "decay_target"

    # 5. DTE check
    if pos.expiration:
        dte_remaining = (pos.expiration - trade_date).days
        if dte_remaining <= 1:
            return "dte_expiry"

    # 6. Regime degradation
    chop_result = compute_choppiness(bars_to_date, lookback=config.chop_lookback, vix=vix)
    if chop_result.chop_score > config.regime_degradation_chop:
        return "regime_degradation"

    # 7. Stagnation
    if sessions >= config.stagnation_sessions and \
       pos.max_favorable_excursion < config.stagnation_mfe_threshold and \
       abs(pnl_r) < config.stagnation_pnl_threshold:
        return "stagnation"

    # 8. Gainz reversal
    if config.gainz_exit and len(bars_to_date) >= 1:
        bar = bars_to_date.iloc[-1]
        g = gainz_signal(
            float(bar["Open"]), float(bar["High"]), float(bar["Low"]), float(bar["Close"]),
            indicators.rsi_14,
            body_ratio_min=config.gainz_body_ratio,
            rsi_overbought=config.gainz_rsi_overbought,
            rsi_oversold=config.gainz_rsi_oversold,
        )
        if g == "sell" and direction == "buy_call" and pnl_r > config.gainz_min_profit_r:
            return "gainz_exit"
        if g == "buy" and direction == "buy_put" and pnl_r > config.gainz_min_profit_r:
            return "gainz_exit"

    return None


def _compute_pnl_r(pos: WeeklyPosition, current_price: float) -> float:
    risk = pos.original_risk if pos.original_risk > 0 else 1.0
    if pos.direction == "buy_call":
        return (current_price - pos.entry_underlying) / risk
    else:
        return (pos.entry_underlying - current_price) / risk


def _update_trailing(pos: WeeklyPosition, pnl_r: float, config: WeeklyBacktestConfig) -> None:
    """Update trailing stop in-place."""
    entry = pos.entry_underlying
    risk = pos.original_risk
    direction = pos.direction

    candidate = None
    if pnl_r >= config.trail_tier_3:
        offset = 1.0 * risk
        candidate = (entry + offset) if direction == "buy_call" else (entry - offset)
    elif pnl_r >= config.trail_tier_2:
        offset = 0.5 * risk
        candidate = (entry + offset) if direction == "buy_call" else (entry - offset)
    elif pnl_r >= config.trail_tier_1:
        candidate = entry

    if candidate is not None:
        if pos.trailing_stop is None:
            pos.trailing_stop = candidate
        elif direction == "buy_call":
            pos.trailing_stop = max(pos.trailing_stop, candidate)
        else:
            pos.trailing_stop = min(pos.trailing_stop, candidate)


def _price_exit(pos: WeeklyPosition, config: WeeklyBacktestConfig) -> None:
    """Price the option exit (real or synth)."""
    if pos.pricing == "real" and pos.occ_symbol and pos.exit_date:
        option_bars = fetch_weekly_option_bars(pos.occ_symbol, pos.entry_date, pos.exit_date)
        exit_prem = weekly_option_close_on_date(option_bars, pos.exit_date)
        if exit_prem is not None:
            pos.exit_premium = exit_prem
            return

    # Synth fallback
    if pos.entry_premium and pos.exit_underlying:
        underlying_move = pos.exit_underlying - pos.entry_underlying
        if pos.direction == "buy_put":
            underlying_move = -underlying_move
        pnl = synth_weekly_pnl(
            pos.entry_premium, underlying_move,
            pos.dte_at_entry, pos.days_held,
            delta=pos.option_delta, gamma=config.synth_gamma,
            slippage=config.slippage,
        )
        pos.exit_premium = pos.entry_premium + pnl


# ── Metrics ──

def compute_metrics(triggers: list[dict], trading_days: list[date] | None = None) -> dict:
    """Compute performance metrics from completed triggers."""
    if not triggers:
        return {"trades": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0}

    winners = [t for t in triggers if t.get("is_winner")]
    losers = [t for t in triggers if not t.get("is_winner")]

    # Use option P&L if available, else underlying P&L
    def _pnl(t):
        return t.get("option_pnl_100x") or t.get("pnl", 0)

    pnls = [_pnl(t) for t in triggers]
    win_pnls = [_pnl(t) for t in winners]
    loss_pnls = [_pnl(t) for t in losers]

    total_pnl = sum(pnls)
    gross_win = sum(win_pnls) if win_pnls else 0
    gross_loss = abs(sum(loss_pnls)) if loss_pnls else 0
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Daily P&L for Sharpe/Sortino
    if trading_days:
        daily_pnl_map: dict[date, float] = {}
        for t in triggers:
            d = date.fromisoformat(t["exit_date"]) if t.get("exit_date") else date.fromisoformat(t["date"])
            daily_pnl_map[d] = daily_pnl_map.get(d, 0) + _pnl(t)
        daily_pnls = [daily_pnl_map.get(d, 0) for d in trading_days]
    else:
        daily_pnls = pnls

    mean_d = np.mean(daily_pnls) if daily_pnls else 0
    std_d = np.std(daily_pnls, ddof=1) if len(daily_pnls) > 1 else 1
    sharpe = (mean_d / std_d) * math.sqrt(252) if std_d > 0 else 0

    neg_pnls = [p for p in daily_pnls if p < 0]
    downside_std = np.std(neg_pnls, ddof=1) if len(neg_pnls) > 1 else 1
    sortino = (mean_d / downside_std) * math.sqrt(252) if downside_std > 0 else 0

    # Max drawdown
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    dd = cumulative - peak
    max_dd = float(dd.min()) if len(dd) > 0 else 0
    calmar = total_pnl / abs(max_dd) if max_dd != 0 else 0

    # Weekly-specific metrics
    hold_days = [t.get("days_held", 0) for t in triggers if t.get("days_held") is not None]
    dte_at_exit = [t.get("dte_at_exit", 0) for t in triggers if t.get("dte_at_exit") is not None]

    exit_reasons: dict[str, int] = {}
    for t in triggers:
        r = t.get("outcome", "unknown")
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    return {
        "trades": len(triggers),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / len(triggers) * 100, 1) if triggers else 0,
        "profit_factor": round(pf, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(triggers), 2) if triggers else 0,
        "avg_win": round(np.mean(win_pnls), 2) if win_pnls else 0,
        "avg_loss": round(np.mean(loss_pnls), 2) if loss_pnls else 0,
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "max_dd": round(max_dd, 2),
        "avg_hold_days": round(np.mean(hold_days), 1) if hold_days else 0,
        "avg_dte_at_exit": round(np.mean(dte_at_exit), 1) if dte_at_exit else 0,
        "exit_reasons": exit_reasons,
        "overnight_gap_total": round(sum(t.get("overnight_gap_impact", 0) for t in triggers), 2),
    }


def print_summary(metrics: dict, symbol: str = "") -> None:
    """Print a formatted summary table."""
    header = f"Weekly Backtest: {symbol}" if symbol else "Weekly Backtest"
    print(f"\n{'=' * 60}")
    print(f"  {header}")
    print(f"{'=' * 60}")
    print(f"  Trades:  {metrics['trades']}  ({metrics['winners']}W / {metrics['losers']}L)")
    print(f"  Win Rate:       {metrics['win_rate']}%")
    print(f"  Profit Factor:  {metrics['profit_factor']}")
    print(f"  Total P&L:      ${metrics['total_pnl']:,.2f}")
    print(f"  Avg P&L:        ${metrics['avg_pnl']:,.2f}")
    print(f"  Avg Win:        ${metrics['avg_win']:,.2f}")
    print(f"  Avg Loss:       ${metrics['avg_loss']:,.2f}")
    print(f"  Sharpe:         {metrics['sharpe']}")
    print(f"  Sortino:        {metrics['sortino']}")
    print(f"  Calmar:         {metrics['calmar']}")
    print(f"  Max Drawdown:   ${metrics['max_dd']:,.2f}")
    print(f"  Avg Hold Days:  {metrics['avg_hold_days']}")
    print(f"  Avg DTE@Exit:   {metrics['avg_dte_at_exit']}")
    print(f"  Overnight Gaps: ${metrics['overnight_gap_total']:,.2f}")
    print(f"\n  Exit Breakdown:")
    for reason, count in sorted(metrics.get("exit_reasons", {}).items(), key=lambda x: -x[1]):
        pct = count / max(1, metrics["trades"]) * 100
        print(f"    {reason:20s} {count:4d}  ({pct:.1f}%)")
    print(f"{'=' * 60}\n")
