# Replay Parity Fix — Implementation Plan

## Problem Statement

The live agent and replay/backtester produce different signals for the same trade date because they operate on different bar data:

- **Live agent** fetches `alpaca_fetch_bars(symbol, days_back=5)` at trade time, getting 5 calendar days back from *that moment*.
- **Replay/verify** uses cached parquet files fetched at a *different* time. Since "5 days back" is relative, the prior-day warmup bars differ.

This causes:
- SMA20/SMA50 drift → quality score differs by 1-2 points (Q=7 live → Q=8 replay)
- EMA13/EMA55 drift → PB EMA gate produces different accept/reject decisions
- Trades that the agent took cannot be reproduced by the replayer

### Observed Impact (Week of May 12-16, 2026)

| Date | Symbol | Live Agent Q | Replay Q | Delta |
|------|--------|-------------|----------|-------|
| May 14 | SPY 12:15 | 7 | 8 | +1 (crosses 3-7 band) |
| May 14 | QQQ 11:50 | 7 | 8 | +1 |
| May 15 | SPY 13:57 | 7 | 9 | +2 |
| May 13 | QQQ 13:42 | 5 | 8 | +3 |

## Solution: 5-Layer Fix

### Layer 1 — Store Indicator Values in Journal

**Goal**: Instant drift detection without replaying bars.

**Change**: Add `"indicators"` sub-dict to each trigger entry in the journal.

**Fields to store**:
```json
"indicators": {
    "sma_20": 748.34,
    "sma_50": 746.03,
    "ema_13": 748.40,
    "ema_55": 746.44,
    "rsi_14": 62.5,
    "vwap": 738.85,
    "zlema_trend": "bearish",
    "or_direction": "bullish",
    "or_momentum": 85,
    "recent_dir": "bullish",
    "recent_momentum": 25,
    "num_warmup_bars": 219
}
```

**File**: `scripts/run_sweet_spot_agent.py` — add to the trigger return dict (~line 512).

**Cost**: ~200 bytes JSON per trade.

**Verification use**: The verify script can compare its recomputed indicators against stored values to pinpoint which indicator drifted and by how much.

---

### Layer 2 — Snapshot Bars at Trade Time

**Goal**: Enable perfect reproduction of any past trade decision.

**Change**: Save the exact `extended_bars` DataFrame when a trigger fires.

**Storage**: `sweet_spot_journal/bars/{date}_{symbol}_{HH:MM}_bars.parquet`

**Implementation**:
1. In `check_sweet_spot()`, attach `extended_bars` to the trigger dict as a transient key `"_bars"`
2. In `run_day()`, after appending the trigger to the journal, save `_bars` to parquet and record the path as `"bars_file"` in the journal entry
3. Strip `_bars` before JSON serialization

**Cost**: ~18KB per snapshot (78 bars/day × 5 days × 6 columns × 8 bytes). At max 3 trades/day = ~54KB/day, ~14MB/year.

**Docker**: The `sweet_spot_journal/` volume mount already covers this subdirectory.

---

### Layer 3 — Fix Prior-Bars Windowing in Verify

**Goal**: Ensure the verify script uses the same warmup window as the live agent.

**Current bug** (in `verify_live_vs_replay.py`):
```python
prior_days = sorted({d for d in bars_full.index.date if d < target_date})
prior_bars = bars_full[pd.Series(bars_full.index.date).isin(set(prior_days)).values]
```
This includes ALL prior days in the cache. The live agent only uses 5 calendar days back.

**Fix**:
```python
from datetime import timedelta
cutoff = target_date - timedelta(days=5)
prior_bars = bars_full[
    (bars_full.index.date >= cutoff) & (bars_full.index.date < target_date)
]
```

**Also**: When Layer 2 bar snapshots exist, prefer loading those directly:
```python
snapshot = journal_dir / "bars" / f"{target_date}_{symbol}_{time}_bars.parquet"
if snapshot.exists():
    bars_full = pd.read_parquet(snapshot)
```

---

### Layer 4 — Date-Anchored Fetch in alpaca_data.py

**Goal**: Allow replay/verify to request the exact bar window the agent would have had on any historical date.

**New function** in `src/utils/alpaca_data.py`:
```python
def fetch_bars_for_date(symbol: str, trade_date: date, days_back: int = 5,
                        interval: str = "5min") -> pd.DataFrame:
    """Fetch bars as if requesting on `trade_date` (not today).
    
    Returns bars from (trade_date - days_back) through end of trade_date.
    """
    start = trade_date - timedelta(days=days_back)
    end = trade_date + timedelta(days=1)
    # Use Alpaca historical bars endpoint with explicit start/end
    ...
```

**Usage in verify script**:
```python
bars_full = fetch_bars_for_date(args.symbol, target_date, days_back=5)
```

This eliminates dependence on cached parquet files entirely.

---

### Layer 5 — Shared Indicator Computation Module (Structural)

**Goal**: Prevent future drift by having a single source of truth for indicator computation.

**Change**: Extract `_build_indicators_replay_parity()` from `run_sweet_spot_agent.py` into `src/utils/indicator_builder.py`.

**Validation**: Unit test that proves given identical input bars:
- Live path (`check_sweet_spot` → `_build_indicators_replay_parity`)
- Replay path (`_build_indicators_from_bars` in `replay_sweet_spot.py`)

produce bit-identical `MarketIndicators`.

**Known differences to reconcile**:
- Live agent: `ext_close.iloc[-50:]` for SMA-50 (fixed window at end)
- Replay: `ext_close.iloc[:ext_n].iloc[-50:]` (sliding window as scan progresses)
- These should be equivalent at scan time but may differ if prior_bars length differs

---

## Implementation Sequence

| Priority | Layer | Effort | Risk | Value |
|----------|-------|--------|------|-------|
| 1 | Layer 1 (indicators in journal) | Small | Zero | Immediate debugging |
| 2 | Layer 2 (bar snapshots) | Small | Zero | Perfect reproduction |
| 3 | Layer 3 (fix verify windowing) | Tiny | Zero | Correct comparisons |
| 4 | Layer 4 (date-anchored fetch) | Medium | Low | Self-contained verify |
| 5 | Layer 5 (shared module) | Large | Medium | Structural prevention |

Layers 1-3 should be implemented together as a single PR. Layer 4 can follow. Layer 5 is a later refactor.

## Files to Modify

| File | Layers | Change |
|------|--------|--------|
| `scripts/run_sweet_spot_agent.py` | 1, 2 | Add indicators dict + bar snapshot save |
| `scripts/verify_live_vs_replay.py` | 3, 4 | Fix prior_bars window, add --refetch/--use-snapshot |
| `src/utils/alpaca_data.py` | 4 | Add `fetch_bars_for_date()` |
| `src/utils/indicator_builder.py` | 5 | New shared module (extract from agent) |

## Validation

After implementation, re-run verify for May 12-15 using bar snapshots (Layer 2) or date-anchored fetch (Layer 4). Expected result: live path reproduces the exact Q/E/C scores logged in the journal.
