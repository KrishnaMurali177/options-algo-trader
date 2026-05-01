"""Audit Logger — persistent JSON-lines trail of every agent decision and order."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIT_DIR = Path("audit_logs")


class AuditLogger:
    """Appends one JSON line per agent run to a daily audit file."""

    def __init__(self, audit_dir: Path = AUDIT_DIR):
        self._dir = audit_dir

    def log(self, summary: dict) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        path = self._dir / f"{today}.jsonl"

        record = {
            "logged_at": datetime.now(timezone.utc).isoformat(),
            **summary,
        }

        line = json.dumps(record, default=str)
        with open(path, "a") as f:
            f.write(line + "\n")

        logger.info("Audit log written to %s", path)
        return path
