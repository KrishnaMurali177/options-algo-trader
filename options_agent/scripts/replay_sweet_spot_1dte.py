"""1DTE replay wrapper — runs the exact same sweet-spot strategy as replay_sweet_spot.py
but buys 1DTE option contracts (expiry = entry_date + 1 business day) instead of 0DTE.

Same-day exit regardless of expiry — the signal logic, quality lattice, cascade detection,
chop filter, regime guard, cluster penalty, PB-EMA, dynamic-OR, VIX bonus, momentum-flip,
stagnation/stop/decay-target exits, sizing, and every other parameter are inherited
unchanged. ONLY the option contract that gets bought changes.

Phase 2.3 build (2026-06-16) per [[strategy_1dte_phase0_promising]]. Goldens inherited
from 0DTE to test "is the instrument switch alone profitable?" before any re-tuning.

Implementation: thin delegation to replay_sweet_spot.main() with --dte=1 injected.
That way the signal/trigger code can't drift between 0DTE and 1DTE.
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    if "--dte" not in sys.argv:
        sys.argv.extend(["--dte", "1"])
    from replay_sweet_spot import main as _main  # noqa: E402
    _main()
