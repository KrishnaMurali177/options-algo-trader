"""Tests for the weekly options agent package.

Covers:
  - weekly/signals/daily_range.py
  - weekly/signals/daily_momentum.py
  - weekly/signals/weekly_indicators.py
  - weekly/state/position_manager.py
  - weekly/chain/weekly_chain.py (unit logic only, no API calls)
  - weekly/agent.py (entry/exit logic)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.market_data import MarketIndicators


# ── Fixtures ──

def _make_daily_bars(n: int = 30, start_price: float = 740.0, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic daily OHLCV bars."""
    dates = pd.bdate_range(end=date.today(), periods=n)
    data = []
    price = start_price
    for i, d in enumerate(dates):
        if trend == "up":
            drift = 0.3
        elif trend == "down":
            drift = -0.3
        else:
            drift = 0.0
        price += drift + np.random.uniform(-0.5, 0.5)
        high = price + np.random.uniform(0.5, 2.0)
        low = price - np.random.uniform(0.5, 2.0)
        open_p = price + np.random.uniform(-0.5, 0.5)
        vol = int(50_000_000 + np.random.uniform(-5_000_000, 5_000_000))
        data.append({"Open": open_p, "High": high, "Low": low, "Close": price, "Volume": vol})
    return pd.DataFrame(data, index=dates)


def _make_indicators(price: float = 750.0, trend: str = "up") -> MarketIndicators:
    sma_20 = price - 2 if trend == "up" else price + 2
    sma_50 = price - 5 if trend == "up" else price + 5
    return MarketIndicators(
        symbol="SPY",
        timestamp=datetime.utcnow(),
        current_price=price,
        timeframe="daily",
        vix=18.0,
        rsi_14=62.0 if trend == "up" else 38.0,
        sma_20=sma_20,
        sma_50=sma_50,
        sma_200=price - 20,
        bb_upper=price + 10,
        bb_middle=sma_20,
        bb_lower=price - 10,
        macd=0.5 if trend == "up" else -0.5,
        macd_signal=0.3 if trend == "up" else -0.3,
        macd_histogram=0.2 if trend == "up" else -0.2,
        atr_14=3.5,
        volume=55_000_000,
        volume_sma_20=50_000_000,
        zlema_trend="bullish" if trend == "up" else "bearish",
    )


# ═══════════════════════════════════════════════════════════════
# DailyRangeAnalyzer
# ═══════════════════════════════════════════════════════════════

class TestDailyRangeAnalyzer:
    def test_bullish_breakout(self):
        from weekly.signals.daily_range import DailyRangeAnalyzer

        bars = _make_daily_bars(10, start_price=740.0, trend="up")
        # Force a clear breakout: current close above prior day's high
        bars.iloc[-2, bars.columns.get_loc("High")] = 750.0
        bars.iloc[-2, bars.columns.get_loc("Low")] = 745.0
        bars.iloc[-1, bars.columns.get_loc("Close")] = 753.0

        indicators = _make_indicators(753.0, "up")
        result = DailyRangeAnalyzer().analyze(bars, indicators)

        assert result.breakout_direction == "bullish"
        assert result.momentum_score > 0
        assert result.range_high == 750.0
        assert result.range_low == 745.0

    def test_bearish_breakout(self):
        from weekly.signals.daily_range import DailyRangeAnalyzer

        bars = _make_daily_bars(10, start_price=750.0, trend="down")
        bars.iloc[-2, bars.columns.get_loc("High")] = 748.0
        bars.iloc[-2, bars.columns.get_loc("Low")] = 742.0
        bars.iloc[-1, bars.columns.get_loc("Close")] = 740.0

        indicators = _make_indicators(740.0, "down")
        result = DailyRangeAnalyzer().analyze(bars, indicators)

        assert result.breakout_direction == "bearish"
        assert result.momentum_score < 0

    def test_neutral_inside_range(self):
        from weekly.signals.daily_range import DailyRangeAnalyzer

        bars = _make_daily_bars(10, start_price=745.0, trend="flat")
        bars.iloc[-2, bars.columns.get_loc("High")] = 750.0
        bars.iloc[-2, bars.columns.get_loc("Low")] = 740.0
        bars.iloc[-1, bars.columns.get_loc("Close")] = 745.0

        indicators = _make_indicators(745.0, "flat")
        indicators.rsi_14 = 50.0
        indicators.macd_histogram = 0.0
        indicators.sma_20 = 745.0
        result = DailyRangeAnalyzer().analyze(bars, indicators)

        assert result.breakout_direction == "neutral"

    def test_insufficient_data(self):
        from weekly.signals.daily_range import DailyRangeAnalyzer

        bars = _make_daily_bars(2)
        indicators = _make_indicators()
        result = DailyRangeAnalyzer().analyze(bars, indicators)

        assert result.breakout_direction == "neutral"
        assert result.momentum_score == 0

    def test_output_fields_match_quality_scorer_interface(self):
        from weekly.signals.daily_range import DailyRangeAnalyzer

        bars = _make_daily_bars(10)
        indicators = _make_indicators()
        result = DailyRangeAnalyzer().analyze(bars, indicators)

        assert hasattr(result, "breakout_direction")
        assert hasattr(result, "momentum_score")
        assert hasattr(result, "breakout_confirmed")
        assert hasattr(result, "range_high")
        assert hasattr(result, "range_low")
        assert result.breakout_direction in ("bullish", "bearish", "neutral")
        assert -100 <= result.momentum_score <= 100
        assert isinstance(result.breakout_confirmed, bool)


# ═══════════════════════════════════════════════════════════════
# DailyMomentumAnalyzer
# ═══════════════════════════════════════════════════════════════

class TestDailyMomentumAnalyzer:
    def test_bullish_momentum(self):
        from weekly.signals.daily_momentum import DailyMomentumAnalyzer

        bars = _make_daily_bars(10, start_price=730.0, trend="up")
        # Force strong uptrend: 5 green candles, >1% move
        for i in range(-5, 0):
            bars.iloc[i, bars.columns.get_loc("Open")] = 730 + (i + 5) * 2
            bars.iloc[i, bars.columns.get_loc("Close")] = 732 + (i + 5) * 2
        indicators = _make_indicators(742.0, "up")
        result = DailyMomentumAnalyzer().analyze(bars, indicators)

        assert result.direction == "bullish"
        assert result.momentum_score > 0

    def test_bearish_momentum(self):
        from weekly.signals.daily_momentum import DailyMomentumAnalyzer

        bars = _make_daily_bars(10, start_price=750.0, trend="down")
        for i in range(-5, 0):
            bars.iloc[i, bars.columns.get_loc("Open")] = 750 - (i + 5) * 2
            bars.iloc[i, bars.columns.get_loc("Close")] = 748 - (i + 5) * 2
        indicators = _make_indicators(738.0, "down")
        result = DailyMomentumAnalyzer().analyze(bars, indicators)

        assert result.direction == "bearish"
        assert result.momentum_score < 0

    def test_insufficient_data(self):
        from weekly.signals.daily_momentum import DailyMomentumAnalyzer

        bars = _make_daily_bars(3)
        indicators = _make_indicators()
        result = DailyMomentumAnalyzer().analyze(bars, indicators)

        assert result.direction == "neutral"
        assert result.momentum_score == 0

    def test_output_fields_match_quality_scorer_interface(self):
        from weekly.signals.daily_momentum import DailyMomentumAnalyzer

        bars = _make_daily_bars(10)
        indicators = _make_indicators()
        result = DailyMomentumAnalyzer().analyze(bars, indicators)

        assert hasattr(result, "direction")
        assert hasattr(result, "momentum_score")
        assert result.direction in ("bullish", "bearish", "neutral")
        assert -100 <= result.momentum_score <= 100


# ═══════════════════════════════════════════════════════════════
# build_weekly_indicators
# ═══════════════════════════════════════════════════════════════

class TestWeeklyIndicators:
    def test_builds_from_daily_bars(self):
        from weekly.signals.weekly_indicators import build_weekly_indicators

        bars = _make_daily_bars(60, start_price=740.0, trend="up")
        result = build_weekly_indicators(bars, "SPY", vix=18.0)

        assert isinstance(result, MarketIndicators)
        assert result.symbol == "SPY"
        assert result.timeframe == "daily"
        assert result.vix == 18.0
        assert result.current_price > 0
        assert result.sma_20 > 0
        assert result.sma_50 > 0
        assert result.atr_14 > 0
        assert 0 <= result.rsi_14 <= 100

    def test_handles_minimal_data(self):
        from weekly.signals.weekly_indicators import build_weekly_indicators

        bars = _make_daily_bars(5, start_price=740.0)
        result = build_weekly_indicators(bars, "SPY", vix=20.0)

        assert result.current_price > 0
        assert result.rsi_14 == 50.0  # insufficient for RSI

    def test_raises_on_empty(self):
        from weekly.signals.weekly_indicators import build_weekly_indicators

        with pytest.raises(ValueError, match="empty"):
            build_weekly_indicators(pd.DataFrame(), "SPY", vix=20.0)

    def test_zlema_trend(self):
        from weekly.signals.weekly_indicators import build_weekly_indicators

        bars = _make_daily_bars(30, start_price=700.0, trend="up")
        result = build_weekly_indicators(bars, "SPY", vix=18.0)

        assert result.zlema_trend in ("bullish", "bearish", "neutral")


# ═══════════════════════════════════════════════════════════════
# WeeklyPositionManager
# ═══════════════════════════════════════════════════════════════

class TestPositionManager:
    def _make_trigger(self, symbol="SPY", occ="SPY260606C00750000"):
        return {
            "entry_date": date.today().isoformat(),
            "symbol": symbol,
            "direction": "buy_call",
            "occ_symbol": occ,
            "strike": 750.0,
            "expiration": (date.today() + timedelta(days=5)).isoformat(),
            "dte_at_entry": 5,
            "entry_underlying": 748.0,
            "entry_premium": 3.25,
            "num_contracts": 1,
            "quality_at_entry": 5,
            "explosion_at_entry": 3,
            "chop_at_entry": 3,
            "stop_underlying": 744.0,
            "target_underlying": 754.0,
            "original_risk": 4.0,
            "trailing_stop": None,
            "max_favorable_excursion": 0.0,
            "trade_mode": "weekly_option",
        }

    def test_create_and_load(self):
        from weekly.state.position_manager import WeeklyPositionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = WeeklyPositionManager(Path(tmpdir))
            trigger = self._make_trigger()
            filepath = mgr.create_position(trigger)

            assert filepath.exists()
            assert "_OPEN.json" in filepath.name

            positions = mgr.load_open_positions()
            assert len(positions) == 1
            assert "SPY260606C00750000" in positions

    def test_add_evaluation(self):
        from weekly.state.position_manager import WeeklyPositionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = WeeklyPositionManager(Path(tmpdir))
            mgr.create_position(self._make_trigger())

            ok = mgr.add_daily_evaluation("SPY260606C00750000", {
                "date": date.today().isoformat(),
                "underlying": 750.0,
                "pnl_r": 0.5,
                "trailing_stop": 748.0,
                "max_favorable_excursion": 0.5,
            })
            assert ok

            positions = mgr.load_open_positions()
            pos = positions["SPY260606C00750000"]
            assert len(pos["evaluations"]) == 1
            assert pos["trailing_stop"] == 748.0

    def test_close_position(self):
        from weekly.state.position_manager import WeeklyPositionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = WeeklyPositionManager(Path(tmpdir))
            mgr.create_position(self._make_trigger())

            ok = mgr.close_position("SPY260606C00750000", {
                "exit_reason": "decay_target",
                "exit_date": date.today().isoformat(),
                "exit_underlying": 753.0,
                "exit_premium": 4.50,
            })
            assert ok

            # No more open positions
            positions = mgr.load_open_positions()
            assert len(positions) == 0

            # Closed file exists
            closed_files = list(Path(tmpdir).glob("*_CLOSED.json"))
            assert len(closed_files) == 1

            data = json.loads(closed_files[0].read_text())
            assert data["closed"] is True
            assert data["exit_reason"] == "decay_target"
            assert data["is_winner"] is True
            assert data["pnl_per_contract"] == 125.0  # (4.50 - 3.25) * 100

    def test_open_count(self):
        from weekly.state.position_manager import WeeklyPositionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = WeeklyPositionManager(Path(tmpdir))
            mgr.create_position(self._make_trigger("SPY", "SPY260606C00750000"))
            mgr.create_position(self._make_trigger("SPY", "SPY260606C00755000"))
            mgr.create_position(self._make_trigger("QQQ", "QQQ260606C00735000"))

            assert mgr.get_open_count("SPY") == 2
            assert mgr.get_open_count("QQQ") == 1

    def test_close_nonexistent(self):
        from weekly.state.position_manager import WeeklyPositionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = WeeklyPositionManager(Path(tmpdir))
            ok = mgr.close_position("FAKE000", {"exit_reason": "test"})
            assert not ok


# ═══════════════════════════════════════════════════════════════
# weekly_chain helpers
# ═══════════════════════════════════════════════════════════════

class TestWeeklyChainHelpers:
    def test_nearest_friday(self):
        from weekly.chain.weekly_chain import _nearest_friday

        # Monday 2026-06-01 → nearest Friday in range should be 2026-06-05
        monday = date(2026, 6, 1)
        result = _nearest_friday(monday, monday + timedelta(days=7))
        assert result is not None
        assert result.weekday() == 4  # Friday
        assert result == date(2026, 6, 5)

    def test_nearest_friday_none_in_range(self):
        from weekly.chain.weekly_chain import _nearest_friday

        # Mon to Wed — no Friday
        result = _nearest_friday(date(2026, 6, 1), date(2026, 6, 3))
        assert result is None

    def test_target_delta_for_type(self):
        from weekly.chain.weekly_chain import target_delta_for_type

        # Deep ITM call (>2% moneyness) → higher delta
        assert target_delta_for_type("call", 730.0, 750.0) > 0.5

        # Deep OTM call (>2% moneyness) → lower delta
        assert target_delta_for_type("call", 770.0, 750.0) < 0.5

        # ATM (within 2%) → 0.50
        assert target_delta_for_type("call", 750.0, 750.0) == 0.50
        assert target_delta_for_type("call", 745.0, 750.0) == 0.50


# ═══════════════════════════════════════════════════════════════
# Agent exit logic
# ═══════════════════════════════════════════════════════════════

class TestExitLogic:
    def _make_position(self, **overrides):
        pos = {
            "entry_date": (date.today() - timedelta(days=2)).isoformat(),
            "symbol": "SPY",
            "direction": "buy_call",
            "occ_symbol": "SPY260606C00750000",
            "entry_underlying": 748.0,
            "original_risk": 4.0,
            "stop_underlying": 744.0,
            "target_underlying": 756.0,
            "trailing_stop": None,
            "max_favorable_excursion": 0.0,
            "expiration": (date.today() + timedelta(days=3)).isoformat(),
        }
        pos.update(overrides)
        return pos

    def test_stop_loss_triggers(self):
        from weekly.agent import _check_exit_conditions

        bars = _make_daily_bars(10, start_price=750.0, trend="down")
        # Ensure no gap between last two bars so gap_stop doesn't fire first
        bars.iloc[-2, bars.columns.get_loc("Close")] = 743.0
        bars.iloc[-1, bars.columns.get_loc("Open")] = 743.0
        indicators = _make_indicators(742.0, "down")
        pos = self._make_position()

        result = _check_exit_conditions(pos, bars, indicators)
        assert result is not None
        assert result["exit_reason"] in ("stop_loss", "gap_stop")

    def test_trailing_stop_triggers(self):
        from weekly.agent import _check_exit_conditions

        bars = _make_daily_bars(10, start_price=750.0, trend="down")
        indicators = _make_indicators(749.0, "down")
        pos = self._make_position(trailing_stop=750.0)

        result = _check_exit_conditions(pos, bars, indicators)
        assert result is not None
        assert result["exit_reason"] == "trailing_stop"

    def test_dte_expiry_triggers(self):
        from weekly.agent import _check_exit_conditions

        bars = _make_daily_bars(10, start_price=750.0)
        indicators = _make_indicators(750.0)
        pos = self._make_position(expiration=date.today().isoformat())

        result = _check_exit_conditions(pos, bars, indicators)
        assert result is not None
        assert result["exit_reason"] == "dte_expiry"

    def test_no_exit_when_healthy(self):
        from weekly.agent import _check_exit_conditions

        bars = _make_daily_bars(10, start_price=750.0, trend="up")
        # Price above entry, not at target, DTE > 1, no trailing stop
        indicators = _make_indicators(750.0, "up")
        pos = self._make_position(
            expiration=(date.today() + timedelta(days=4)).isoformat(),
            max_favorable_excursion=1.0,
        )

        result = _check_exit_conditions(pos, bars, indicators)
        assert result is None

    def test_decay_target_triggers(self):
        from weekly.agent import _check_exit_conditions

        bars = _make_daily_bars(10, start_price=750.0, trend="up")
        # Entry 3 days ago, target at 756, decay should pull target closer
        indicators = _make_indicators(754.0, "up")
        pos = self._make_position(
            entry_date=(date.today() - timedelta(days=3)).isoformat(),
            entry_underlying=748.0,
            target_underlying=756.0,
            original_risk=4.0,
            expiration=(date.today() + timedelta(days=2)).isoformat(),
            max_favorable_excursion=1.5,
        )

        result = _check_exit_conditions(pos, bars, indicators)
        # After 3 sessions with halflife=2: decay_factor = max(0.5, 0.5^(3/2)) ≈ 0.354 → clamped to 0.5
        # Effective target = 748 + 8 * 0.5 = 752. Price 754 > 752 → should trigger
        assert result is not None
        assert result["exit_reason"] == "decay_target"


# ═══════════════════════════════════════════════════════════════
# Trailing stop logic
# ═══════════════════════════════════════════════════════════════

class TestTrailingStop:
    def test_no_trail_below_1r(self):
        from weekly.agent import _update_trailing_stop

        pos = {"entry_underlying": 748.0, "original_risk": 4.0,
               "direction": "buy_call", "trailing_stop": None}
        result = _update_trailing_stop(pos, pnl_r=0.5)
        assert result is None

    def test_trail_at_breakeven_at_1r(self):
        from weekly.agent import _update_trailing_stop

        pos = {"entry_underlying": 748.0, "original_risk": 4.0,
               "direction": "buy_call", "trailing_stop": None}
        result = _update_trailing_stop(pos, pnl_r=1.0)
        assert result == 748.0

    def test_trail_at_half_r_at_1_5r(self):
        from weekly.agent import _update_trailing_stop

        pos = {"entry_underlying": 748.0, "original_risk": 4.0,
               "direction": "buy_call", "trailing_stop": 748.0}
        result = _update_trailing_stop(pos, pnl_r=1.5)
        assert result == 750.0  # entry + 0.5 * risk

    def test_trail_at_1r_at_2r(self):
        from weekly.agent import _update_trailing_stop

        pos = {"entry_underlying": 748.0, "original_risk": 4.0,
               "direction": "buy_call", "trailing_stop": 750.0}
        result = _update_trailing_stop(pos, pnl_r=2.0)
        assert result == 752.0  # entry + 1.0 * risk

    def test_trail_never_moves_backward(self):
        from weekly.agent import _update_trailing_stop

        pos = {"entry_underlying": 748.0, "original_risk": 4.0,
               "direction": "buy_call", "trailing_stop": 752.0}
        # Even at 1.0R (trail=breakeven=748), existing 752 should stay
        result = _update_trailing_stop(pos, pnl_r=1.0)
        assert result == 752.0

    def test_put_direction_trailing(self):
        from weekly.agent import _update_trailing_stop

        pos = {"entry_underlying": 748.0, "original_risk": 4.0,
               "direction": "buy_put", "trailing_stop": None}
        result = _update_trailing_stop(pos, pnl_r=1.0)
        assert result == 748.0  # breakeven for put


# ═══════════════════════════════════════════════════════════════
# Integration: signals feed into quality_scorer unchanged
# ═══════════════════════════════════════════════════════════════

class TestSignalIntegration:
    def test_daily_signals_feed_quality_scorer(self):
        """Verify daily range + momentum outputs plug into compute_quality_score."""
        from weekly.signals.daily_range import DailyRangeAnalyzer
        from weekly.signals.daily_momentum import DailyMomentumAnalyzer
        from src.utils.quality_scorer import compute_quality_score

        bars = _make_daily_bars(30, start_price=730.0, trend="up")
        indicators = _make_indicators(750.0, "up")

        range_result = DailyRangeAnalyzer().analyze(bars, indicators)
        mom_result = DailyMomentumAnalyzer().analyze(bars, indicators)

        # This should not raise — proves the interfaces are compatible
        quality = compute_quality_score(
            direction="buy_call",
            current_price=indicators.current_price,
            sma_20=indicators.sma_20,
            sma_50=indicators.sma_50,
            vix=indicators.vix,
            volume=indicators.volume,
            volume_sma_20=indicators.volume_sma_20,
            or_direction=range_result.breakout_direction,
            or_momentum=range_result.momentum_score,
            or_confirmed=range_result.breakout_confirmed,
            recent_dir=mom_result.direction,
            recent_momentum=mom_result.momentum_score,
            zlema_trend=indicators.zlema_trend,
        )

        assert 0 <= quality.score <= 11
        assert quality.label in ("🟢 HIGH", "🔵 MEDIUM", "🟡 LOW")

    def test_daily_bars_feed_choppiness(self):
        """Verify daily bars work with compute_choppiness."""
        from src.utils.choppiness import compute_choppiness

        bars = _make_daily_bars(30, start_price=740.0, trend="up")
        result = compute_choppiness(bars, lookback=10, vix=18.0)

        assert 0 <= result.chop_score <= 10
        assert isinstance(result.is_choppy, bool)
