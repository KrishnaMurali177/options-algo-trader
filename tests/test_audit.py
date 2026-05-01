"""Tests for the AuditLogger."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import date
from pathlib import Path

from src.audit import AuditLogger


class TestAuditLogger:

    def test_creates_file_and_writes_json(self, tmp_path):
        logger = AuditLogger(audit_dir=tmp_path)
        summary = {"symbol": "AAPL", "status": "dry_run", "score": 8}
        path = logger.log(summary)

        assert path.exists()
        assert path.name == f"{date.today().isoformat()}.jsonl"

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["symbol"] == "AAPL"
        assert record["status"] == "dry_run"
        assert "logged_at" in record

    def test_appends_multiple_entries(self, tmp_path):
        logger = AuditLogger(audit_dir=tmp_path)
        logger.log({"run": 1})
        logger.log({"run": 2})

        path = tmp_path / f"{date.today().isoformat()}.jsonl"
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["run"] == 1
        assert json.loads(lines[1])["run"] == 2

    def test_creates_directory_if_missing(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        logger = AuditLogger(audit_dir=nested)
        path = logger.log({"test": True})
        assert path.exists()
