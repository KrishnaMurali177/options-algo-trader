"""Reusable research helpers for post-hoc analysis of agent behaviour.

Deliberately decoupled from the live 0DTE pipeline: nothing in here is
imported by the agent, the scanner or the replay engine. These modules take
already-produced artifacts (trigger exports, journals, bars) and answer
"has this happened before, and what did the tape look like?" questions.

Modules:
  drawdown     — find and compare drawdown episodes in a daily P&L series.
  day_profile  — parameterize a session's underlying price action into a
                 scale-free feature vector, then rank historical analogs.
"""

from src.analysis.drawdown import (  # noqa: F401
    DrawdownEpisode,
    daily_pnl_from_triggers,
    equity_curve,
    find_drawdown_episodes,
    rank_similar_episodes,
    rank_similar_windows,
    rolling_window_stats,
    window_stats,
)
from src.analysis.day_profile import (  # noqa: F401
    FEATURE_SPECS,
    build_feature_table,
    compute_day_features,
    parse_weights,
    rank_analog_days,
    rank_analog_windows,
    reference_profile,
    zscore_table,
)
