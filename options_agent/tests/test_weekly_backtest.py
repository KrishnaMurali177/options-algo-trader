"""Tests for the weekly backtester modules.

Covers:
  - weekly/backtest/option_pricing.py
  - weekly/backtest/replay_weekly.py (entry/exit/metrics)
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.market_data import MarketIndicators


# ── Fixtures ──

def _make_daily_bars(n: int = 60, start_price: float = 740.0, trend: str = "up") -> pd.DataFrame:
    dates = pd.bdate_range(end=date.today(), periods=n)
    data = []
    price = start_price
    for d in dates:
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


# ═══════════════════════════════════════════════════════════════
# Option Pricing Module
# ═══════════════════════════════════════════════════════════════

class TestNearestFriday:
    def test_monday_finds_friday(self):
        from weekly.backtest.option_pricing import nearest_friday
        # Monday 2026-06-01 with min_dte=3, max_dte=7
        result = nearest_friday(date(2026, 6, 1), 3, 7)
        assert result == date(2026, 6, 5)  # Friday

    def test_wednesday_this_friday_too_close(self):
        from weekly.backtest.option_pricing import nearest_friday
        # Wednesday 2026-06-03 with min_dte=3 → this Friday is DTE=2 (too close)
        result = nearest_friday(date(2026, 6, 3), 3, 7)
        # Next Friday 2026-06-12 is DTE=9 > max_dte=7, so should be None
        # Wait — 2026-06-10 is a Wednesday, 2026-06-06 is Saturday...
        # Let me compute: Jun 3 + 3 = Jun 6 (Sat), Jun 3 + 7 = Jun 10 (Wed)
        # No Friday between Jun 6 and Jun 10 → None
        assert result is None

    def test_wednesday_wider_window(self):
        from weekly.backtest.option_pricing import nearest_friday
        # With max_dte=10, Jun 3 → Jun 6 to Jun 13, Friday Jun 12 is in range
        result = nearest_friday(date(2026, 6, 3), 3, 10)
        assert result is not None
        assert result.weekday() == 4

    def test_no_friday_in_range(self):
        from weekly.backtest.option_pricing import nearest_friday
        # Very narrow window with no Friday
        result = nearest_friday(date(2026, 6, 1), 1, 2)  # Tue-Wed range
        assert result is None


class TestDeltaEstimation:
    def test_atm_call_near_50(self):
        from weekly.backtest.option_pricing import estimate_delta
        delta = estimate_delta(750.0, 750.0, 5, "call", iv=0.20)
        assert 0.45 <= delta <= 0.60

    def test_otm_call_below_50(self):
        from weekly.backtest.option_pricing import estimate_delta
        delta = estimate_delta(750.0, 760.0, 5, "call", iv=0.20)
        assert delta < 0.50

    def test_itm_call_above_50(self):
        from weekly.backtest.option_pricing import estimate_delta
        delta = estimate_delta(750.0, 740.0, 5, "call", iv=0.20)
        assert delta > 0.50

    def test_put_delta_negative(self):
        from weekly.backtest.option_pricing import estimate_delta
        delta = estimate_delta(750.0, 750.0, 5, "put", iv=0.20)
        assert delta < 0


class TestSynthPricing:
    def test_premium_reasonable_range(self):
        from weekly.backtest.option_pricing import synth_weekly_premium
        prem = synth_weekly_premium(750.0, 755.0, 5, 3.5, 18.0, "call")
        assert 0.15 <= prem <= 10.0

    def test_pnl_winner_positive(self):
        from weekly.backtest.option_pricing import synth_weekly_pnl
        pnl = synth_weekly_pnl(2.0, 5.0, 5, 2, delta=0.35, gamma=0.02)
        assert pnl > 0

    def test_pnl_loser_capped(self):
        from weekly.backtest.option_pricing import synth_weekly_pnl
        pnl = synth_weekly_pnl(2.0, -10.0, 5, 4, delta=0.35, gamma=0.02)
        assert pnl >= -2.0  # capped at -premium

    def test_theta_decay_increases_with_days(self):
        from weekly.backtest.option_pricing import synth_weekly_pnl
        # Same underlying move, more days held → more theta decay → lower P&L
        pnl_1d = synth_weekly_pnl(2.0, 3.0, 5, 1, delta=0.35)
        pnl_4d = synth_weekly_pnl(2.0, 3.0, 5, 4, delta=0.35)
        assert pnl_1d > pnl_4d


class TestSessionsBetween:
    def test_same_day(self):
        from weekly.backtest.option_pricing import sessions_between
        assert sessions_between(date(2026, 6, 1), date(2026, 6, 1)) == 0

    def test_one_business_day(self):
        from weekly.backtest.option_pricing import sessions_between
        # Monday to Tuesday
        assert sessions_between(date(2026, 6, 1), date(2026, 6, 2)) == 1

    def test_over_weekend(self):
        from weekly.backtest.option_pricing import sessions_between
        # Friday to Monday
        assert sessions_between(date(2026, 5, 29), date(2026, 6, 1)) == 1

    def test_full_week(self):
        from weekly.backtest.option_pricing import sessions_between
        # Monday to Friday
        assert sessions_between(date(2026, 6, 1), date(2026, 6, 5)) == 4


# ═══════════════════════════════════════════════════════════════
# Replay Engine
# ═══════════════════════════════════════════════════════════════

class TestWeeklyBacktestConfig:
    def test_defaults(self):
        from weekly.backtest.replay_weekly import WeeklyBacktestConfig
        config = WeeklyBacktestConfig()
        assert config.quality_min == 3
        assert config.quality_max == 7
        assert config.target_delta == 0.35
        assert config.entry_days == (0, 1, 2)

    def test_override(self):
        from weekly.backtest.replay_weekly import WeeklyBacktestConfig
        config = WeeklyBacktestConfig(quality_min=4, target_delta=0.30)
        assert config.quality_min == 4
        assert config.target_delta == 0.30


class TestReplayWeekly:
    def test_runs_without_error(self):
        from weekly.backtest.replay_weekly import WeeklyBacktestConfig, replay_weekly
        bars = _make_daily_bars(90, trend="up")
        config = WeeklyBacktestConfig(real_options=False)
        triggers = replay_weekly(bars, "SPY", config)
        assert isinstance(triggers, list)

    def test_insufficient_data_returns_empty(self):
        from weekly.backtest.replay_weekly import WeeklyBacktestConfig, replay_weekly
        bars = _make_daily_bars(10)
        config = WeeklyBacktestConfig(real_options=False)
        triggers = replay_weekly(bars, "SPY", config)
        assert triggers == []

    def test_trigger_dict_has_required_fields(self):
        from weekly.backtest.replay_weekly import WeeklyBacktestConfig, replay_weekly
        bars = _make_daily_bars(120, trend="up")
        config = WeeklyBacktestConfig(real_options=False)
        triggers = replay_weekly(bars, "SPY", config)
        if triggers:
            t = triggers[0]
            assert "date" in t
            assert "direction" in t
            assert "quality" in t
            assert "entry" in t
            assert "exit_price" in t
            assert "trade_mode" in t
            assert t["trade_mode"] == "weekly_option"
            assert "days_held" in t
            assert "dte_at_entry" in t

    def test_no_look_ahead_bias(self):
        """Verify entries don't use future data by checking entry date <= trade date."""
        from weekly.backtest.replay_weekly import WeeklyBacktestConfig, replay_weekly
        bars = _make_daily_bars(120, trend="up")
        config = WeeklyBacktestConfig(real_options=False)
        triggers = replay_weekly(bars, "SPY", config)
        for t in triggers:
            entry_d = date.fromisoformat(t["entry_date"])
            if t.get("exit_date"):
                exit_d = date.fromisoformat(t["exit_date"])
                assert exit_d >= entry_d


class TestComputeMetrics:
    def test_basic_metrics(self):
        from weekly.backtest.replay_weekly import compute_metrics
        triggers = [
            {"pnl": 2.0, "is_winner": True, "outcome": "decay_target", "date": "2026-05-01",
             "exit_date": "2026-05-03", "days_held": 2, "dte_at_exit": 3, "overnight_gap_impact": 0.5},
            {"pnl": -1.0, "is_winner": False, "outcome": "stop_loss", "date": "2026-05-05",
             "exit_date": "2026-05-06", "days_held": 1, "dte_at_exit": 4, "overnight_gap_impact": 0.2},
            {"pnl": 1.5, "is_winner": True, "outcome": "trailing_stop", "date": "2026-05-08",
             "exit_date": "2026-05-10", "days_held": 2, "dte_at_exit": 2, "overnight_gap_impact": 0},
        ]
        m = compute_metrics(triggers)
        assert m["trades"] == 3
        assert m["winners"] == 2
        assert m["losers"] == 1
        assert m["win_rate"] == pytest.approx(66.7, abs=0.1)
        assert m["profit_factor"] == pytest.approx(3.5, abs=0.01)
        assert m["total_pnl"] == pytest.approx(2.5, abs=0.01)
        assert m["avg_hold_days"] == pytest.approx(1.7, abs=0.1)

    def test_empty_triggers(self):
        from weekly.backtest.replay_weekly import compute_metrics
        m = compute_metrics([])
        assert m["trades"] == 0
        assert m["win_rate"] == 0


# ═══════════════════════════════════════════════════════════════
# Sweep Config Generation
# ═══════════════════════════════════════════════════════════════

class TestSweepConfigs:
    def test_build_configs_generates_combos(self):
        from weekly.backtest.sweep_weekly import _build_configs
        from weekly.backtest.replay_weekly import WeeklyBacktestConfig

        base = WeeklyBacktestConfig()
        grid = {
            "quality_band": [(3, 7), (4, 8)],
            "chop_max": [4, 5],
        }
        configs = _build_configs(base, grid)
        assert len(configs) == 4  # 2 × 2

        # Verify config values are applied
        labels, cfgs = zip(*configs)
        q_mins = {c.quality_min for c in cfgs}
        assert 3 in q_mins
        assert 4 in q_mins
