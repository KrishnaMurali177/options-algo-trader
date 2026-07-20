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
    ap.add_argument("--db", default=None, help="SQLite file to upsert the daily row into")
    ap.add_argument("--csv", default=None, help="CSV file to mirror the daily row into")
    ap.add_argument("--backfill", action="store_true",
                    help="One-shot: convert ALL historical days (orders + portfolio equity) "
                         "into --db/--csv, then exit. Ignores --period/Discord.")
    args = ap.parse_args()

    from src.utils.alpaca_paper import AlpacaPaperTrader
    trader = AlpacaPaperTrader()

    if args.backfill:
        _backfill(trader, args.db, args.csv, args.limit)
        return

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

    # Durable history (day only) — SQLite (queryable) + CSV mirror (grep/Excel).
    if args.period == "day" and (args.db or args.csv):
        try:
            acct = trader.get_today_pnl()
        except Exception:
            acct = {"equity": None, "buying_power": None, "today_pnl": None}
        row = {
            "date": today,
            "account": getattr(trader, "account_number", "") or _account_no(trader),
            "paper": int(bool(getattr(trader, "paper", True))),
            "equity": acct.get("equity"),
            "buying_power": acct.get("buying_power"),
            "today_pnl": acct.get("today_pnl"),
            "total_realized": round(total, 2),
            "per_symbol": json.dumps({k: round(v, 2) for k, v in sorted(by_und.items())}),
            "n_fills": entries,
            "winners": winners,
            "losers": losers,
            "recorded_at": _et_now().isoformat(timespec="seconds"),
        }
        if args.db:
            _persist_sqlite(args.db, row)
            print(f"Recorded to DB: {args.db}")
        if args.csv:
            _persist_csv(args.csv, row)
            print(f"Recorded to CSV: {args.csv}")


def _account_no(trader) -> str:
    try:
        return str(trader.client.get_account().account_number)
    except Exception:
        return ""


_COLS = ["date", "account", "paper", "equity", "buying_power", "today_pnl",
         "total_realized", "per_symbol", "n_fills", "winners", "losers", "recorded_at"]


def _persist_sqlite(path: str, row: dict) -> None:
    import sqlite3
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS daily_pnl ("
            + ", ".join(f"{c} {'INTEGER' if c in ('paper','n_fills','winners','losers') else 'REAL' if c in ('equity','buying_power','today_pnl','total_realized') else 'TEXT'}" for c in _COLS)
            + ", PRIMARY KEY (date, account))")
        placeholders = ", ".join("?" for _ in _COLS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLS if c not in ("date", "account"))
        con.execute(
            f"INSERT INTO daily_pnl ({', '.join(_COLS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(date, account) DO UPDATE SET {updates}",
            [row[c] for c in _COLS])
        con.commit()
    finally:
        con.close()


def _persist_csv(path: str, row: dict) -> None:
    import csv
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # De-dup on (date, account): drop any existing row for the same key, then append.
    existing = []
    if p.exists():
        with p.open() as f:
            existing = [r for r in csv.DictReader(f)
                        if not (r.get("date") == row["date"] and r.get("account") == str(row["account"]))]
    rows = existing + [row]
    rows.sort(key=lambda r: (str(r.get("date")), str(r.get("account"))))
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _backfill(trader, db: str | None, csv_path: str | None, limit: int) -> None:
    """One-shot history import: per-day realized cash (from orders) + daily equity/
    P&L (from Alpaca portfolio history), upserted into the DB/CSV. Idempotent on
    (date, account) — safe to re-run."""
    import datetime as _dt
    from collections import defaultdict

    orders = [o for o in trader.get_orders("all", limit) if o.get("filled_price") is not None]
    days: dict[str, dict] = defaultdict(lambda: {"occ": defaultdict(float), "und": defaultdict(float), "fills": 0})
    for o in orders:
        d = (o.get("submitted_at") or "")[:10]
        if not d:
            continue
        sym = o["symbol"]
        is_opt = len(sym) > 6
        mult = 100 if is_opt else 1
        signed = (o["filled_price"] if o["side"] == "sell" else -o["filled_price"]) * int(o["qty"]) * mult
        days[d]["occ"][sym] += signed
        days[d]["und"][sym[:-15] if is_opt and len(sym) > 15 else sym] += signed
        if o["side"] == "buy":
            days[d]["fills"] += 1

    equity_by: dict[str, tuple] = {}
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        ph = trader.client.get_portfolio_history(GetPortfolioHistoryRequest(period="1A", timeframe="1D"))
        for ts, eq, pl in zip(ph.timestamp, ph.equity, ph.profit_loss or [None] * len(ph.timestamp)):
            equity_by[_dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")] = (eq, pl)
    except Exception as e:
        print(f"portfolio history unavailable ({e}) — equity/today_pnl will be blank")

    acct_no = _account_no(trader)
    paper = int(bool(getattr(trader, "paper", True)))
    recorded = _et_now().isoformat(timespec="seconds")
    n = 0
    for d in sorted(set(days) | set(equity_by)):
        occ = days.get(d, {}).get("occ", {})
        und = days.get(d, {}).get("und", {})
        eq, pl = equity_by.get(d, (None, None))
        row = {
            "date": d, "account": acct_no, "paper": paper,
            "equity": eq, "buying_power": None,
            "today_pnl": round(pl, 2) if pl is not None else None,
            "total_realized": round(sum(occ.values()), 2),
            "per_symbol": json.dumps({k: round(v, 2) for k, v in sorted(und.items())}),
            "n_fills": days.get(d, {}).get("fills", 0),
            "winners": sum(1 for v in occ.values() if v > 0),
            "losers": sum(1 for v in occ.values() if v < 0),
            "recorded_at": recorded,
        }
        if db:
            _persist_sqlite(db, row)
        if csv_path:
            _persist_csv(csv_path, row)
        n += 1
    print(f"Backfilled {n} day(s) → db={db or '-'} csv={csv_path or '-'} "
          f"({len(orders)} fills scanned, account {acct_no})")


if __name__ == "__main__":
    main()
