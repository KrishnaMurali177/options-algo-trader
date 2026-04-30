"""Test entry trigger variations to find optimal breakout offset."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.WARNING)

from src.backtester import IntradayBacktester

# entry_offset_pct: shift as % of range width
#   -0.20 = enter 20% INSIDE range (much earlier — price only needs 80% of range)
#   -0.10 = enter 10% inside range
#    0.00 = enter at exact range high/low (current default)
#   +0.05 = enter 5% beyond range (slight confirmation)
#   +0.10 = enter 10% beyond range (more confirmation, fewer trades)

# Also vary ATR buffer (current = 0.05)
configs = [
    # (label, entry_offset_pct, atr_buffer)
    ("inside_20pct",     -0.20, 0.05),
    ("inside_15pct",     -0.15, 0.05),
    ("inside_10pct",     -0.10, 0.05),
    ("inside_5pct",      -0.05, 0.05),
    ("exact (baseline)",  0.00, 0.05),
    ("beyond_5pct",      +0.05, 0.05),
    ("beyond_10pct",     +0.10, 0.05),
    ("beyond_15pct",     +0.15, 0.05),
    # Try different ATR buffers with best offsets
    ("inside_10_buf0",   -0.10, 0.00),
    ("inside_10_buf3",   -0.10, 0.03),
    ("inside_10_buf8",   -0.10, 0.08),
    ("exact_buf0",        0.00, 0.00),
    ("exact_buf3",        0.00, 0.03),
    ("exact_buf8",        0.00, 0.08),
]

print(f"{'Config':<22} {'offset':>6} {'buf':>5} {'SPY_PnL':>8} {'SPY_WR':>7} {'SPY_#':>5} {'QQQ_PnL':>8} {'QQQ_WR':>7} {'QQQ_#':>5} {'Combined':>9} {'PF':>5}")
print("-" * 105)

for label, offset, buf in configs:
    bt = IntradayBacktester(atr_buffer=buf, entry_offset_pct=offset)
    total_pnl = 0
    all_taken = []
    parts = []
    for sym in ['SPY', 'QQQ']:
        r = bt.run(sym, period='1y')
        taken = [t for t in r.trades if t.direction != 'skip']
        all_taken.extend(taken)
        total_pnl += r.total_pnl
        parts.append(f"{r.total_pnl:+8.2f} {r.win_rate:6.1f}% {len(taken):5d}")

    gp = sum(t.pnl_dollars for t in all_taken if t.is_winner)
    gl = abs(sum(t.pnl_dollars for t in all_taken if not t.is_winner))
    pf = gp / gl if gl > 0 else 999

    print(f"{label:<22} {offset:>+6.2f} {buf:>5.2f} {parts[0]} {parts[1]} {total_pnl:+9.2f} {pf:5.2f}")

