"""Signal Tuner — analyzes backtest results to find optimal signal weights and thresholds.

Runs a grid search over momentum thresholds and signal weights,
evaluates each configuration against historical trades, and outputs
the best-performing parameters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import product

import numpy as np

from src.models.backtest_result import BacktestReport, TradeResult

logger = logging.getLogger(__name__)


@dataclass
class TuningResult:
    """Result of the signal tuning process."""
    # Optimal parameters found
    optimal_breakout_threshold: int
    optimal_weights: dict[str, int]

    # Performance at optimal parameters
    optimal_win_rate: float
    optimal_profit_factor: float
    optimal_total_pnl: float
    optimal_trades_taken: int

    # Signal importance ranking
    signal_ranking: list[dict]  # [{name, importance_score, win_rate, avg_pnl}]

    # Current vs optimal comparison
    current_win_rate: float
    current_profit_factor: float
    improvement_pct: float


# Default weights (matching backtester signals)
DEFAULT_WEIGHTS = {
    "price_vs_range": 30,
    "intraday_rsi": 20,
    "intraday_macd": 15,
    "vwap": 15,
    "volume": 5,
    "or_candle": 10,
    "vix": 5,
    "gap_open": 10,
    "prev_day_sr": 8,
    "ema_cross": 10,
    "range_width": 8,
}

# Search ranges for each weight
WEIGHT_GRID = {
    "price_vs_range": [20, 25, 30, 35, 40],
    "intraday_rsi": [10, 15, 20, 25],
    "intraday_macd": [10, 15, 20],
    "vwap": [10, 15, 20, 25],
    "volume": [3, 5, 8, 10],
    "or_candle": [5, 10, 15],
    "vix": [3, 5, 8],
    "gap_open": [5, 8, 10, 15],
    "prev_day_sr": [5, 8, 10, 12],
    "ema_cross": [5, 8, 10, 15],
    "range_width": [5, 8, 10],
}

THRESHOLD_GRID = [20, 25, 30, 35, 40, 45, 50]


class SignalTuner:
    """Optimizes signal weights and breakout threshold using backtest data."""

    def tune(self, report: BacktestReport) -> TuningResult:
        """Run the tuning process on backtest results."""
        trades = report.trades
        taken = [t for t in trades if t.direction != "skip"]

        if len(taken) < 10:
            logger.warning("Only %d trades — too few for reliable tuning", len(taken))

        # Current performance (baseline)
        current_wr = report.win_rate
        current_pf = report.profit_factor

        # ── 1. Signal importance analysis ──
        signal_ranking = self._rank_signals(taken)

        # ── 2. Grid search over thresholds ──
        # For efficiency, we do a two-stage approach:
        # Stage A: Optimize breakout threshold with default weights
        # Stage B: Optimize top-3 signal weights with best threshold

        logger.info("Stage A: Optimizing breakout threshold …")
        best_threshold = 25
        best_threshold_score = -999.0

        for threshold in THRESHOLD_GRID:
            score = self._evaluate_threshold(trades, threshold, DEFAULT_WEIGHTS)
            if score > best_threshold_score:
                best_threshold_score = score
                best_threshold = threshold

        logger.info("Best threshold: %d (score=%.2f)", best_threshold, best_threshold_score)

        # Stage B: Optimize weights of top 3 most impactful signals
        logger.info("Stage B: Optimizing signal weights …")
        top_signals = [s["name"] for s in signal_ranking[:3]]
        best_weights = DEFAULT_WEIGHTS.copy()
        best_weight_score = best_threshold_score

        # Build grid for top 3 signals only
        grid_keys = [s for s in top_signals if s in WEIGHT_GRID]
        if grid_keys:
            grid_values = [WEIGHT_GRID[k] for k in grid_keys]
            for combo in product(*grid_values):
                test_weights = DEFAULT_WEIGHTS.copy()
                for k, v in zip(grid_keys, combo):
                    test_weights[k] = v
                score = self._evaluate_threshold(trades, best_threshold, test_weights)
                if score > best_weight_score:
                    best_weight_score = score
                    best_weights = test_weights.copy()

        # ── 3. Evaluate optimal config ──
        opt_trades = self._simulate_with_config(trades, best_threshold, best_weights)
        opt_taken = [t for t in opt_trades if t["direction"] != "skip"]
        opt_winners = [t for t in opt_taken if t["pnl"] > 0]

        opt_win_rate = len(opt_winners) / len(opt_taken) * 100 if opt_taken else 0
        gross_profit = sum(t["pnl"] for t in opt_winners)
        gross_loss = abs(sum(t["pnl"] for t in opt_taken if t["pnl"] <= 0))
        opt_pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        opt_total_pnl = sum(t["pnl"] for t in opt_taken)

        improvement = ((opt_win_rate - current_wr) / current_wr * 100) if current_wr > 0 else 0

        return TuningResult(
            optimal_breakout_threshold=best_threshold,
            optimal_weights=best_weights,
            optimal_win_rate=round(opt_win_rate, 1),
            optimal_profit_factor=round(opt_pf, 2),
            optimal_total_pnl=round(opt_total_pnl, 4),
            optimal_trades_taken=len(opt_taken),
            signal_ranking=signal_ranking,
            current_win_rate=round(current_wr, 1),
            current_profit_factor=round(current_pf, 2),
            improvement_pct=round(improvement, 1),
        )

    # ── Signal Ranking ───────────────────────────────────────────

    def _rank_signals(self, trades: list[TradeResult]) -> list[dict]:
        """Rank signals by how predictive they are of winning trades."""
        signal_names = set()
        for t in trades:
            signal_names.update(t.signal_scores.keys())

        rankings = []
        for name in signal_names:
            aligned_trades = []
            for t in trades:
                score = t.signal_scores.get(name, 0)
                if score != 0:
                    aligned = (t.direction == "call" and score > 0) or (t.direction == "put" and score < 0)
                    if aligned:
                        aligned_trades.append(t)

            if not aligned_trades:
                rankings.append({"name": name, "importance_score": 0, "win_rate": 0, "avg_pnl": 0, "count": 0})
                continue

            wins = sum(1 for t in aligned_trades if t.is_winner)
            wr = wins / len(aligned_trades) * 100
            avg_pnl = float(np.mean([t.pnl_dollars for t in aligned_trades]))
            # Importance = win_rate × frequency × avg_pnl_sign
            importance = wr * len(aligned_trades) / len(trades) * (1 if avg_pnl > 0 else 0.5)

            rankings.append({
                "name": name,
                "importance_score": round(importance, 1),
                "win_rate": round(wr, 1),
                "avg_pnl": round(avg_pnl, 4),
                "count": len(aligned_trades),
            })

        rankings.sort(key=lambda r: r["importance_score"], reverse=True)
        return rankings

    # ── Evaluation ───────────────────────────────────────────────

    def _evaluate_threshold(
        self, trades: list[TradeResult], threshold: int, weights: dict[str, int]
    ) -> float:
        """Score a configuration by simulating trades with the given threshold/weights."""
        results = self._simulate_with_config(trades, threshold, weights)
        taken = [r for r in results if r["direction"] != "skip"]
        if not taken:
            return -999.0

        winners = [r for r in taken if r["pnl"] > 0]
        win_rate = len(winners) / len(taken)
        total_pnl = sum(r["pnl"] for r in taken)
        gross_profit = sum(r["pnl"] for r in winners)
        gross_loss = abs(sum(r["pnl"] for r in taken if r["pnl"] <= 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else 2.0

        # Composite score: balance win rate, profit factor, and number of trades
        # Penalize configs that take too few trades
        trade_penalty = min(len(taken) / 10, 1.0)  # scale up to 10 trades
        score = (win_rate * 40 + min(pf, 3) * 20 + total_pnl * 0.1) * trade_penalty
        return score

    def _simulate_with_config(
        self, trades: list[TradeResult], threshold: int, weights: dict[str, int]
    ) -> list[dict]:
        """Re-evaluate trades with different weights/threshold."""
        results = []
        for t in trades:
            if t.direction == "skip" and t.exit_reason in ("insufficient_opening_bars", "zero_range", "no_post_or_bars"):
                results.append({"direction": "skip", "pnl": 0})
                continue

            # Recompute momentum with new weights
            momentum = 0
            raw_signals = t.signal_scores
            for sig_name, raw_score in raw_signals.items():
                weight = weights.get(sig_name, abs(raw_score))
                if raw_score > 0:
                    momentum += weight
                elif raw_score < 0:
                    momentum -= weight

            momentum = max(-100, min(100, momentum))

            if momentum >= threshold:
                direction = "call"
            elif momentum <= -threshold:
                direction = "put"
            else:
                results.append({"direction": "skip", "pnl": 0})
                continue

            # Use the actual trade's P&L if direction matches, else skip
            # (We can't simulate a different direction's P&L without bar data)
            if direction == t.direction and t.direction != "skip":
                results.append({"direction": direction, "pnl": t.pnl_dollars})
            else:
                results.append({"direction": "skip", "pnl": 0})

        return results

