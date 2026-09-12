"""Regression tests for replay's VIX look-ahead fix (resolve_day_vix).

^VIX settles at the close, so the settled close for session N is not knowable
at session N's 10:30 scan. Replay used to hand the strategy exactly that.
These pin the lagged behaviour, and pin lag=0 as the documented way to
reproduce pre-fix runs.
"""

import importlib.util
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_replay():
    """Import replay_sweet_spot by path — scripts/ is not a package."""
    path = ROOT / "scripts" / "replay_sweet_spot.py"
    spec = importlib.util.spec_from_file_location("replay_sweet_spot", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["replay_sweet_spot"] = mod
    spec.loader.exec_module(mod)
    return mod


replay = _load_replay()

DAYS = [date(2026, 3, d) for d in (2, 3, 4, 5, 6)]     # Mon–Fri
VIX = {DAYS[0]: 14.0, DAYS[1]: 15.0, DAYS[2]: 16.0, DAYS[3]: 24.0, DAYS[4]: 20.0}


def test_lag1_uses_the_prior_sessions_close():
    day_vix, prev_vix = replay.resolve_day_vix(VIX, DAYS, 3, vix_lag=1)
    assert day_vix == 16.0      # Wednesday's close, knowable Thursday morning
    assert prev_vix == 15.0     # Tuesday's, for the spike comparison


def test_lag0_reproduces_the_look_ahead():
    day_vix, prev_vix = replay.resolve_day_vix(VIX, DAYS, 3, vix_lag=0)
    assert day_vix == 24.0      # Thursday's own settled close — not knowable at 10:30
    assert prev_vix == 16.0


def test_the_lag_changes_the_sit_out_decision():
    """The case the fix exists for: a VIX spike that only shows up at the close.

    With look-ahead, Thursday is skipped on a 20-max gate because the replay
    can see Thursday closed at 24. Lagged, the strategy sees Wednesday's 16 and
    takes the day — which is what the live agent would actually have done.
    """
    ahead, _ = replay.resolve_day_vix(VIX, DAYS, 3, vix_lag=0)
    lagged, _ = replay.resolve_day_vix(VIX, DAYS, 3, vix_lag=1)
    assert ahead > 20.0 >= lagged


def test_missing_sessions_fall_back_to_the_most_recent_earlier_close():
    sparse = {DAYS[0]: 14.0, DAYS[3]: 24.0}     # Tue/Wed/Fri absent (holiday-ish)
    day_vix, prev_vix = replay.resolve_day_vix(sparse, DAYS, 3, vix_lag=1)
    assert day_vix == 14.0      # walks back to Monday rather than defaulting
    assert prev_vix == 14.0


def test_first_session_has_no_history():
    day_vix, prev_vix = replay.resolve_day_vix(VIX, DAYS, 0, vix_lag=1)
    assert day_vix == 20.0      # neutral default, not a look-ahead at 14.0
    assert prev_vix is None


def test_empty_map_is_neutral():
    assert replay.resolve_day_vix({}, DAYS, 3, vix_lag=1) == (20.0, None)


def test_vix_lag_defaults_to_one_on_the_cli():
    """The no-look-ahead behaviour must be the default, not opt-in."""
    argv, sys.argv = sys.argv, ["replay_sweet_spot.py"]
    try:
        parser = (replay.build_parser() if hasattr(replay, "build_parser")
                  else pytest.skip("parser is inline in main() on this lineage"))
        assert parser.parse_args([]).vix_lag == 1
        assert parser.parse_args(["--vix-lag", "0"]).vix_lag == 0
    finally:
        sys.argv = argv
