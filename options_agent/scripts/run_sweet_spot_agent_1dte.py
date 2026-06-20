"""1DTE live agent wrapper — runs the exact same sweet-spot strategy as
run_sweet_spot_agent.py but buys 1DTE option contracts (expiry = next listed
business day, holiday-aware) instead of 0DTE.

Same-day exit regardless of expiry. Signal/quality/cascade/chop/regime/cluster/
PB-EMA/dynamic-OR/VIX-bonus/momentum-flip/stagnation/stop/decay-target/sizing/
journal/restart-recovery/duplicate-order-guard logic is inherited unchanged.
Only the option contract bought and the journal filename change.

Phase 5 build (2026-06-18) per [[strategy_1dte_phase2_replay_validated]].
Goldens inherited from 0DTE (Phase 3 confirmed no retune needed).

Implementation: thin delegation to run_sweet_spot_agent.main() with --dte=1
injected. The signal/order/journal code can't drift between 0DTE and 1DTE.

Run alongside the 0DTE agent — both will use separate journals
(YYYY-MM-DD_<SYM>.json for 0DTE, YYYY-MM-DD_<SYM>_1dte.json for 1DTE) and
OCC symbols naturally segment by expiry. When the 1DTE chain is unavailable
(holiday or exchange-side gap), the trade is skipped per user policy.
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    if "--dte" not in sys.argv:
        sys.argv.extend(["--dte", "1"])
    from run_sweet_spot_agent import main as _main  # noqa: E402
    _main()
