"""Post-hoc reconcile of SHADOW journal trades against the shadow paper account.

The shadow agents run main's code, whose reconcile only fills in gainz-exit close
prices — so stop/decay/stagnation/theta exits are left with no exit_price/pnl. This
backfills them: each closed 0DTE trade already carries order_id + close_order_id, so
we look up the fills on the SHADOW Alpaca paper account (.env.shadow) and compute
P&L (options are long premium: pnl = exit - entry).

Run (this-branch code, shadow-account creds mounted read-only):
  docker-compose --profile live run --rm --no-deps \
    -v "$PWD/options_agent/sweet_spot_journal_shadow:/app/sweet_spot_journal_shadow" \
    -v "$PWD/options_agent/.env.shadow:/app/.env.shadow:ro" \
    agent-spy python scripts/reconcile_shadow.py
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # /app, so `src` imports

from dotenv import load_dotenv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default="/app/sweet_spot_journal_shadow")
    ap.add_argument("--env", default="/app/.env.shadow", help="shadow-account env file")
    ap.add_argument("--symbols", default="SPY,QQQ")
    args = ap.parse_args()

    # Shadow account keys override whatever the container was started with.
    load_dotenv(args.env, override=True)
    from src.utils.alpaca_paper import AlpacaPaperTrader
    trader = AlpacaPaperTrader()

    files = []
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        files += sorted(glob.glob(os.path.join(args.journal, f"*_{sym}.json")))

    total_recon = 0
    for f in files:
        try:
            trades = json.load(open(f))
        except (json.JSONDecodeError, OSError):
            continue
        changed = False
        for t in trades:
            if t.get("trade_mode") != "0dte_option" or not t.get("closed"):
                continue
            if t.get("exit_price") is not None and t.get("pnl") is not None:
                continue  # already reconciled

            # Entry fill (usually already present as actual_entry).
            if t.get("actual_entry") is None and t.get("order_id"):
                oc = trader.get_order_outcome(t["order_id"])
                if oc and oc.get("actual_entry") is not None:
                    t["actual_entry"] = oc["actual_entry"]; changed = True

            # Exit fill via the recorded close order.
            if t.get("exit_price") is None and t.get("close_order_id"):
                fill = trader.get_fill_price(t["close_order_id"])
                if fill and fill.get("price") is not None:
                    t["exit_price"] = fill["price"]; changed = True

            entry, exit_p = t.get("actual_entry"), t.get("exit_price")
            if entry is not None and exit_p is not None:
                pnl = exit_p - entry  # options long premium (call or put)
                t["pnl"] = round(pnl, 4)
                t["is_winner"] = pnl > 0
                changed = True
                total_recon += 1

        if changed:
            Path(f).write_text(json.dumps(trades, indent=2, default=str))
            print(f"  reconciled {os.path.basename(f)}")

    print(f"Done — {total_recon} shadow trades reconciled.")


if __name__ == "__main__":
    main()
