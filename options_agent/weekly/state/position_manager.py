"""Weekly Position Manager — persistent multi-day position tracking.

Each position is stored as a single JSON file in the weekly_journal/ directory:
  - Open:   YYYY-MM-DD_SYMBOL_OPEN.json
  - Closed: YYYY-MM-DD_SYMBOL_CLOSED.json

This allows the agent to discover open positions by globbing *_OPEN.json.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class WeeklyPositionManager:
    """Manages persistent weekly option positions across trading days."""

    def __init__(self, journal_dir: Path):
        self.journal_dir = journal_dir
        self.journal_dir.mkdir(parents=True, exist_ok=True)

    def load_open_positions(self) -> dict[str, dict]:
        """Load all open positions. Returns dict keyed by occ_symbol."""
        positions = {}
        for f in self.journal_dir.glob("*_OPEN.json"):
            try:
                data = json.loads(f.read_text())
                occ = data.get("occ_symbol")
                if occ:
                    data["_file"] = f
                    positions[occ] = data
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load position from %s: %s", f.name, e)
        return positions

    def create_position(self, trigger: dict) -> Path:
        """Create a new open position file. Returns the file path."""
        entry_date = trigger.get("entry_date", date.today().isoformat())
        symbol = trigger["symbol"]
        filename = f"{entry_date}_{symbol}_OPEN.json"
        filepath = self.journal_dir / filename

        counter = 1
        while filepath.exists():
            filename = f"{entry_date}_{symbol}_{counter}_OPEN.json"
            filepath = self.journal_dir / filename
            counter += 1

        trigger["closed"] = False
        trigger["evaluations"] = trigger.get("evaluations", [])
        trigger["exit_reason"] = None
        trigger["exit_date"] = None
        trigger["exit_underlying"] = None
        trigger["exit_premium"] = None
        trigger["pnl_per_contract"] = None
        trigger["is_winner"] = None

        filepath.write_text(json.dumps(trigger, indent=2, default=str))
        logger.info("Created weekly position: %s → %s", trigger.get("occ_symbol"), filename)
        return filepath

    def add_daily_evaluation(self, occ_symbol: str, eval_data: dict) -> bool:
        """Append a daily evaluation to an open position."""
        pos, filepath = self._find_open(occ_symbol)
        if pos is None:
            logger.warning("Cannot evaluate — no open position for %s", occ_symbol)
            return False

        pos.setdefault("evaluations", []).append(eval_data)

        if "trailing_stop" in eval_data and eval_data["trailing_stop"] is not None:
            pos["trailing_stop"] = eval_data["trailing_stop"]
        if "max_favorable_excursion" in eval_data:
            pos["max_favorable_excursion"] = max(
                pos.get("max_favorable_excursion", 0.0),
                eval_data["max_favorable_excursion"],
            )

        filepath.write_text(json.dumps(pos, indent=2, default=str))
        return True

    def close_position(self, occ_symbol: str, exit_data: dict) -> bool:
        """Close a position: stamp exit fields and rename to _CLOSED."""
        pos, filepath = self._find_open(occ_symbol)
        if pos is None:
            logger.warning("Cannot close — no open position for %s", occ_symbol)
            return False

        pos["closed"] = True
        pos["exit_reason"] = exit_data.get("exit_reason", "unknown")
        pos["exit_date"] = exit_data.get("exit_date", date.today().isoformat())
        pos["exit_underlying"] = exit_data.get("exit_underlying")
        pos["exit_premium"] = exit_data.get("exit_premium")

        entry_prem = pos.get("entry_premium", 0)
        exit_prem = exit_data.get("exit_premium")
        if entry_prem and exit_prem is not None:
            direction = pos.get("direction", "buy_call")
            pnl = (exit_prem - entry_prem) * 100
            pos["pnl_per_contract"] = round(pnl, 2)
            pos["is_winner"] = pnl > 0

        closed_name = filepath.name.replace("_OPEN.json", "_CLOSED.json")
        closed_path = filepath.parent / closed_name
        filepath.write_text(json.dumps(pos, indent=2, default=str))
        shutil.move(str(filepath), str(closed_path))

        logger.info(
            "Closed weekly position %s → %s (reason=%s, pnl=$%.2f/contract)",
            occ_symbol, closed_name, pos["exit_reason"],
            pos.get("pnl_per_contract", 0),
        )
        return True

    def get_open_count(self, symbol: str) -> int:
        """Count open positions for a symbol."""
        return sum(
            1 for f in self.journal_dir.glob(f"*_{symbol}_OPEN.json")
        ) + sum(
            1 for f in self.journal_dir.glob(f"*_{symbol}_*_OPEN.json")
        )

    def get_weekly_stop_count(self, symbol: str) -> int:
        """Count stop-loss exits this calendar week."""
        today = date.today()
        monday = today - timedelta(days=today.weekday())

        count = 0
        for f in self.journal_dir.glob(f"*_{symbol}_CLOSED.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("exit_reason") == "stop_loss":
                    exit_date = data.get("exit_date", "")
                    if exit_date:
                        d = date.fromisoformat(exit_date)
                        if d >= monday:
                            count += 1
            except (json.JSONDecodeError, ValueError):
                pass
        return count

    def _find_open(self, occ_symbol: str) -> tuple[dict | None, Path | None]:
        """Find the open position file for a given OCC symbol."""
        for f in self.journal_dir.glob("*_OPEN.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("occ_symbol") == occ_symbol:
                    return data, f
            except json.JSONDecodeError:
                continue
        return None, None
