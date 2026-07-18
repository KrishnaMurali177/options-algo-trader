"""Post ACTUAL cash P&L from the broker (Alpaca) to Discord — EOD or EOW.

The per-trade exit posts show R-multiples off the underlying. This is the
authoritative money view: it reads the real *filled orders* from Alpaca and
reports realized dollars. 0DTE positions are flat at EOD, so each contract's
realized cash is the net signed cashflow of its fills (sells +, buys −, ×100),
and the day total is cross-checked against the account equity delta.

The active account is whatever the env selects — `.env` = paper, `.env.live` =
real money (ALPACA_PAPER=false). Set --label (or DISCORD_TAG) to tag the post.

Run (Docker):
  # real money, end of day
  docker-compose --profile realmoney run --rm agent-live-spy \
    python scripts/report_broker_pnl.py --period day
  # paper, end of week
  docker-compose run --rm dashboard python scripts/report_broker_pnl.py --period week
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # /app → `src` imports

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None


def _et_now() -> datetime:
    return datetime.now(_ET) if _ET else datetime.now()


def _post_discord(webhook: str, title: str, lines: list[str], total: float,
                  footer: str) -> None:
    color = 0x2e7d32 if total >= 0 else 0xc62828
    desc = "\n".join(lines) if lines else "_no filled trades in this window_"
    payload = {"embeds": [{
        "title": title,
        "color": color,
        "description": desc,
        "footer": {"text": footer},
    }]}
    req = urllib.request.Request(
        webhook, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "OptionsAgent/1.0"},
        method="POST")
    urllib.request.urlopen(req, timeout=10)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", choices=["day", "week"], default="day")
    ap.add_argument("--label", default=os.environ.get("DISCORD_TAG", "PAPER"),
                    help="Account tag for the post title (e.g. '🔴 ALPACA · REAL-MONEY')")
    ap.add_argument("--webhook", default=os.environ.get("DISCORD_WEBHOOK_URL", ""))
    ap.add_argument("--limit", type=int, default=500, help="Max recent orders to scan")
    args = ap.parse_args()

    from src.utils.alpaca_paper import AlpacaPaperTrader
    trader = AlpacaPaperTrader()

    now = _et_now()
    today = now.strftime("%Y-%m-%d")
    if args.period == "week":
        start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")  # Monday
    else:
        start = today
    end = today

    orders = [o for o in trader.get_orders("all", args.limit)
              if o.get("filled_price") is not None
              and start <= (o.get("submitted_at") or "")[:10] <= end]

    # Realized cash per OCC contract = net signed cashflow (sells +, buys −, ×100).
    by_occ: dict[str, float] = defaultdict(float)
    by_und: dict[str, float] = defaultdict(float)
    entries = 0
    for o in orders:
        sym = o["symbol"]
        is_opt = len(sym) > 6
        mult = 100 if is_opt else 1
        signed = (o["filled_price"] if o["side"] == "sell" else -o["filled_price"]) \
            * int(o["qty"]) * mult
        by_occ[sym] += signed
        # OCC = root + YYMMDD(6) + C/P(1) + strike(8); root is everything before the last 15.
        und = sym[:-15] if is_opt and len(sym) > 15 else sym
        by_und[und] += signed
        if o["side"] == "buy":
            entries += 1
    total = sum(by_occ.values())
    winners = sum(1 for v in by_occ.values() if v > 0)
    losers = sum(1 for v in by_occ.values() if v < 0)

    # Build the message
    period_lbl = "EOD" if args.period == "day" else "EOW"
    lines = []
    for und, cash in sorted(by_und.items(), key=lambda kv: kv[1]):
        lines.append(f"{'🟢' if cash >= 0 else '🔴'} **{und}**: {'+' if cash>=0 else ''}${cash:,.0f}")
    lines.append("")
    lines.append(f"**Total realized: {'+' if total>=0 else ''}${total:,.0f}**  "
                 f"({entries} fills · {winners}W/{losers}L contracts)")

    if args.period == "day":
        try:
            acct = trader.get_today_pnl()
            lines.append(f"Account Δ today: {'+' if acct['today_pnl']>=0 else ''}"
                         f"${acct['today_pnl']:,.0f}  (equity ${acct['equity']:,.0f})")
        except Exception:
            pass

    title = f"💵 {period_lbl} Cash P&L — {args.label}"
    window = today if args.period == "day" else f"{start} → {end}"
    footer = f"{window} · broker fills (real cash)"

    print(f"{title}\n" + "\n".join(lines))
    if args.webhook:
        try:
            _post_discord(args.webhook, title, lines, total, footer)
            print("Discord post sent.")
        except Exception as e:
            print(f"Discord post failed: {e}")
    else:
        print("(no DISCORD_WEBHOOK_URL — printed only)")


if __name__ == "__main__":
    main()
