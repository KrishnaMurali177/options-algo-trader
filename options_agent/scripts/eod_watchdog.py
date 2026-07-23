"""EOD safety-net watchdog — broker-truth position reconciler.

Runs as an INDEPENDENT process from the four trading agents. Its whole reason
to exist is that the agents' own 15:30 time-stop only fires if the agent process
is alive — and the failure mode that orphans positions is exactly the one where
the agent has crashed (see bug_live_agent_crash_2026_07_09,
bug_auto_exercise_bp_blackout_2026_07). So this watchdog trusts NOTHING from the
agents' in-memory or journal state; it reads Alpaca's get_all_positions() as
ground truth.

Two jobs:
  1. Force-close ANY option position expiring TODAY (tracked or not) at ~15:30 ET,
     15 min before the broker's 15:45 auto-liquidation. Broker liquidation is
     partial/not guaranteed; ITM leftovers auto-exercise into stock and silently
     poison account-wide buying power. Closing them ourselves avoids that.
  2. Alert on ANY non-option (equity) position in the account. On 06-30 an
     orphaned ITM 0DTE auto-exercised into 200 SPY shares that sat undetected
     for 16 days and zeroed options buying power. A non-option position in this
     account is always an anomaly — surface it the same day.

Designed to be invoked once near 15:30 ET (single-shot, default) by the OS
scheduler, or with --loop to poll continuously through the close window.

Usage:
    python scripts/eod_watchdog.py                # single reconcile pass
    python scripts/eod_watchdog.py --loop         # poll until 15:45 ET
    python scripts/eod_watchdog.py --dry-run      # report only, never close
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Make `src` importable when run from anywhere.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

from src.utils.alpaca_paper import AlpacaPaperTrader
from src.utils.trade_notifier import TradeNotifier

ET = ZoneInfo("America/New_York")

JOURNAL_DIR = _ROOT / "sweet_spot_journal"

logger = logging.getLogger("eod_watchdog")

# OCC symbol: ROOT (1-6 alnum) + YYMMDD + C|P + 8-digit strike.
# e.g. SPY260630C00744000 -> exp 2026-06-30.
_OCC_RE = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})[CP]\d{8}$")


def _occ_expiry(symbol: str) -> date | None:
    """Parse the expiry date out of an OCC option symbol; None if not parseable."""
    m = _OCC_RE.match(symbol)
    if not m:
        return None
    try:
        return date(2000 + int(m["yy"]), int(m["mm"]), int(m["dd"]))
    except ValueError:
        return None


def _is_option(pos) -> bool:
    """True if the Alpaca position is an option contract."""
    ac = getattr(pos, "asset_class", None)
    ac_val = getattr(ac, "value", ac)
    if ac_val == "us_option":
        return True
    # Fall back to OCC-shape detection if asset_class is missing/unexpected.
    return _occ_expiry(pos.symbol) is not None


def journal_writeback(occ: str, close_order_id: str | None,
                      today: date) -> int:
    """Best-effort: stamp the exit on today's journal rows for a force-closed OCC.

    The watchdog decides WHAT to close purely from broker truth (see module
    docstring) — it never reads journal state to make that decision. But once a
    close has actually happened, we DO want the exit reflected in the journal so
    the row isn't left looking `open` forever and its realized loss isn't lost
    from reconcile_and_report.py (which only reconciles rows that already carry a
    `close_order_id`). See bug where 07-20 QQQ force-closes left three rows with
    no exit metadata and hid a -$1,191 loss.

    This is intentionally best-effort: if no matching open row exists (true
    orphan, or the agent crashed before journaling the entry), we simply write
    nothing and return 0 — the safety close already happened and is alerted
    separately. Never let a journal problem raise past the close.

    Matches today's `<date>_<SYM>*.json` journals (0DTE and 1DTE forks share the
    same OCC namespace). Returns the number of rows stamped.
    """
    stamped = 0
    now_iso = datetime.now(ET).isoformat()
    try:
        journals = sorted(JOURNAL_DIR.glob(f"{today.isoformat()}_*.json"))
    except Exception as e:
        logger.warning("journal_writeback: could not list journals: %s", e)
        return 0

    for jf in journals:
        try:
            rows = json.loads(jf.read_text())
        except Exception as e:
            logger.warning("journal_writeback: could not read %s: %s", jf.name, e)
            continue
        if not isinstance(rows, list):
            continue
        dirty = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("occ_symbol") == occ and not row.get("closed"):
                row["exit_reason"] = "eod_watchdog"
                row["exit_time"] = now_iso
                if close_order_id:
                    row["close_order_id"] = close_order_id
                row["closed"] = True
                dirty = True
                stamped += 1
        if dirty:
            try:
                jf.write_text(json.dumps(rows, indent=2, default=str))
            except Exception as e:
                logger.warning("journal_writeback: could not write %s: %s", jf.name, e)

    if stamped:
        logger.info("journal_writeback: stamped exit on %d row(s) for %s.", stamped, occ)
    else:
        logger.info("journal_writeback: no open journal row matched %s "
                    "(orphan or unjournaled entry).", occ)
    return stamped


def reconcile(trader: AlpacaPaperTrader, notifier: TradeNotifier,
              dry_run: bool = False) -> dict:
    """One reconcile pass against broker truth. Returns a summary dict."""
    today = datetime.now(ET).date()
    now_str = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")

    try:
        positions = trader.client.get_all_positions()
    except Exception as e:
        logger.error("get_all_positions() failed: %s — cannot reconcile this pass.", e)
        notifier.notify_alert(
            "EOD watchdog: broker fetch FAILED",
            f"`get_all_positions()` raised `{e}` at {now_str}. "
            "Could not verify positions — check the agents and account manually.",
            level="critical",
        )
        return {"error": str(e)}

    expiring_options: list = []
    other_options: list = []
    equity_positions: list = []

    for p in positions:
        if _is_option(p):
            exp = _occ_expiry(p.symbol)
            if exp == today:
                expiring_options.append(p)
            else:
                other_options.append(p)
        else:
            equity_positions.append(p)

    logger.info(
        "Broker truth @ %s: %d position(s) — %d expiring-today option(s), "
        "%d other option(s), %d equity position(s).",
        now_str, len(positions), len(expiring_options),
        len(other_options), len(equity_positions),
    )

    closed, failed = [], []
    for p in expiring_options:
        occ = p.symbol
        logger.warning(
            "EOD SAFETY CLOSE: %s qty=%s (%s) expires TODAY — force-closing.",
            occ, p.qty, p.side.value if hasattr(p.side, "value") else p.side,
        )
        if dry_run:
            closed.append(occ + " (dry-run)")
            continue
        result = trader.close_options_position(occ)
        if result and result.get("order_id"):
            closed.append(f"{occ} (order {result['order_id']})")
            # Best-effort: reflect the exit in today's journal so the row isn't
            # left `open` and its realized P&L isn't lost from the report. Never
            # let a journal problem undo a successful safety close.
            journal_writeback(occ, result.get("order_id"), today)
        else:
            failed.append(occ)
            logger.error("EOD SAFETY CLOSE FAILED for %s — will retry next pass.", occ)

    # ── Build alerts ──
    if failed:
        notifier.notify_alert(
            "EOD watchdog: FORCE-CLOSE FAILED",
            f"Could not close {len(failed)} expiring option(s) at {now_str}: "
            + ", ".join(f"`{s}`" for s in failed)
            + ". Broker will auto-liquidate at 15:45; ITM leftovers may exercise "
            "into stock. **Check the account now.**",
            level="critical",
        )
    elif closed and not dry_run:
        notifier.notify_alert(
            "EOD watchdog: safety-closed expiring options",
            f"Force-closed {len(closed)} option(s) expiring today at {now_str}:\n"
            + "\n".join(f"• `{s}`" for s in closed),
            level="warn",
        )

    if equity_positions:
        lines = [
            f"• `{p.symbol}` qty={p.qty} @ ${float(p.avg_entry_price):.2f} "
            f"(mkt ${float(p.market_value):,.0f}, uPL ${float(p.unrealized_pl):+,.0f})"
            for p in equity_positions
        ]
        notifier.notify_alert(
            "EOD watchdog: UNEXPECTED equity position(s)",
            f"This options account holds {len(equity_positions)} non-option "
            f"position(s) as of {now_str} — likely an auto-exercised orphan. "
            "These are NOT auto-sold (user decision). Review and liquidate manually:\n"
            + "\n".join(lines),
            level="critical",
        )
        for line in lines:
            logger.error("UNEXPECTED EQUITY POSITION %s", line)

    return {
        "positions": len(positions),
        "expiring_options": len(expiring_options),
        "closed": closed,
        "failed": failed,
        "equity_positions": len(equity_positions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="EOD broker-truth position watchdog")
    parser.add_argument("--loop", action="store_true",
                        help="Poll repeatedly until 15:45 ET instead of a single pass.")
    parser.add_argument("--interval", type=int, default=60,
                        help="Seconds between polls in --loop mode (default: 60).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report and alert but never place close orders.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    load_dotenv()

    notifier = TradeNotifier(discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""))
    try:
        trader = AlpacaPaperTrader()
    except Exception as e:
        logger.error("Could not init trader: %s", e)
        notifier.notify_alert("EOD watchdog: startup FAILED",
                              f"Could not connect to Alpaca: `{e}`", level="critical")
        return 1

    if not args.loop:
        reconcile(trader, notifier, dry_run=args.dry_run)
        return 0

    # Loop mode: reconcile every `interval` seconds until the broker's 15:45
    # auto-liquidation. Keeps retrying any failed close on the next pass.
    while True:
        now = datetime.now(ET)
        reconcile(trader, notifier, dry_run=args.dry_run)
        if now.hour == 15 and now.minute >= 45:
            logger.info("Reached 15:45 ET — watchdog done for the day.")
            break
        if now.hour >= 16:
            logger.info("Past 16:00 ET — watchdog done for the day.")
            break
        time.sleep(max(5, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
