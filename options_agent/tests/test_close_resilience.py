"""Tests for the close-failure guard that keeps a failed close from orphaning a position.

A close that silently fails used to leave a real-money 0DTE position open while
the journal, the exit notification and every downstream report called it closed.
These cover the three defences: in-line retry on transient network errors, the
broker-truth verifier that catches an orphan after the fact, and the operational
alert that tells a human when it cannot be fixed automatically.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.alpaca_paper import _is_transient, _retry_on_network  # noqa: E402


def _load_agent():
    """Import run_sweet_spot_agent by path — scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        "run_sweet_spot_agent", ROOT / "scripts" / "run_sweet_spot_agent.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_sweet_spot_agent"] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


# ── transient classification ───────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "Max retries exceeded with url",
    "Failed to resolve 'api.alpaca.markets'",
    "Connection aborted",
    "Read timed out",
    "503 Service Unavailable",
])
def test_network_blips_are_transient(msg):
    assert _is_transient(Exception(msg))


@pytest.mark.parametrize("msg", [
    "position not found",
    "insufficient buying power",
    "40310000: account is not authorized",
])
def test_real_api_errors_are_not_transient(msg):
    """Fail fast on these — retrying a genuine rejection just hammers the broker."""
    assert not _is_transient(Exception(msg))


# ── in-line retry ──────────────────────────────────────────────────────

def test_retry_recovers_after_a_transient_blip(monkeypatch):
    monkeypatch.setattr("src.utils.alpaca_paper.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("Connection aborted")
        return "order-1"

    assert _retry_on_network(flaky, delay=0) == "order-1"
    assert calls["n"] == 3


def test_retry_gives_up_after_attempts_and_reraises(monkeypatch):
    monkeypatch.setattr("src.utils.alpaca_paper.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def always_down():
        calls["n"] += 1
        raise Exception("Max retries exceeded")

    with pytest.raises(Exception, match="Max retries"):
        _retry_on_network(always_down, attempts=3, delay=0)
    assert calls["n"] == 3


def test_non_transient_error_is_not_retried(monkeypatch):
    monkeypatch.setattr("src.utils.alpaca_paper.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def rejected():
        calls["n"] += 1
        raise Exception("position not found")

    with pytest.raises(Exception, match="position not found"):
        _retry_on_network(rejected, attempts=3, delay=0)
    assert calls["n"] == 1


# ── broker-truth verifier (cohort-aware) ───────────────────────────────

class FakeTrader:
    def __init__(self, open_syms, close_succeeds=True):
        self._open = list(open_syms)
        self.close_succeeds = close_succeeds
        self.close_calls = []

    def get_positions(self):
        return [{"symbol": s} for s in self._open]

    def close_options_position(self, occ):
        self.close_calls.append(occ)
        if not self.close_succeeds:
            return None
        self._open = [s for s in self._open if s != occ]
        return {"order_id": "recovered-1", "occ_symbol": occ, "status": "filled"}


def _trig(occ, closed=True, reason="stagnation"):
    return {"occ_symbol": occ, "trade_mode": "0dte_option",
            "closed": closed, "exit_reason": reason}


def test_orphan_is_reclosed_and_stamped_across_the_whole_cohort():
    occ = "SPY260914C00762000"
    trigs = [_trig(occ), _trig(occ), _trig(occ)]        # cohort of 3
    trader = FakeTrader([occ])
    agent._verify_closes_against_broker(trader, trigs, "SPY")
    assert trader.close_calls == [occ]                   # deduped: one call, not three
    assert all(t["close_order_id"] == "recovered-1" for t in trigs)
    assert all(t["closed"] for t in trigs)


def test_unrecoverable_orphan_is_flagged_open_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    occ = "SPY260914C00762000"
    trigs = [_trig(occ), _trig(occ)]
    trader = FakeTrader([occ], close_succeeds=False)
    agent._verify_closes_against_broker(trader, trigs, "SPY")
    # Every cohort member goes back to open so monitoring resumes next loop.
    assert all(t["closed"] is False for t in trigs)
    assert all(t["exit_reason"] == "close_failed" for t in trigs)
    # And a human is told, via the file the health check escalates.
    alert = (tmp_path / "ops_alerts.log").read_text()
    assert "ORPHANED POSITION" in alert and occ in alert


def test_a_genuinely_closed_position_is_left_alone():
    occ = "SPY260914C00762000"
    trigs = [_trig(occ)]
    trader = FakeTrader([])            # broker holds nothing
    agent._verify_closes_against_broker(trader, trigs, "SPY")
    assert trader.close_calls == []
    assert trigs[0]["closed"] is True
    assert trigs[0]["exit_reason"] == "stagnation"


def test_open_positions_are_not_treated_as_orphans():
    """A trigger the journal already calls OPEN is monitoring's job, not the verifier's."""
    occ = "SPY260914C00762000"
    trigs = [_trig(occ, closed=False, reason="open")]
    trader = FakeTrader([occ])
    agent._verify_closes_against_broker(trader, trigs, "SPY")
    assert trader.close_calls == []


def test_verifier_survives_a_broker_outage():
    """If positions can't be fetched we must not guess — leave the journal untouched."""
    class Down(FakeTrader):
        def get_positions(self):
            raise Exception("Max retries exceeded")

    trigs = [_trig("SPY260914C00762000")]
    agent._verify_closes_against_broker(Down([]), trigs, "SPY")
    assert trigs[0]["closed"] is True


def test_notifier_alert_accepts_both_call_shapes():
    """The order-placement handler calls (title, body, level=); the orphan path too."""
    from src.utils.trade_notifier import TradeNotifier
    seen = []

    class Ch:
        def notify_alert(self, title, message, level):
            seen.append((title, message, level))

    n = TradeNotifier()
    n._channels = [Ch()]
    n.notify_alert("just a message")
    n.notify_alert("A title", "a body", level="critical")
    assert seen[0][0] == "Agent alert" and seen[0][1] == "just a message"
    assert seen[1] == ("A title", "a body", "critical")


def test_failed_close_preserves_why_it_was_closing(tmp_path, monkeypatch):
    """The retry must re-use the original reason, not an unrecognised placeholder.

    close_reason feeds the risk trackers: "stop"/"stagnation"/"time_stop" count as
    a loss, anything else RESETS consecutive_losses. A recovered orphan carrying a
    made-up reason would quietly clear the consecutive-loss brake.
    """
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    occ = "SPY260914C00762000"
    trigs = [_trig(occ, reason="stop")]
    agent._verify_closes_against_broker(FakeTrader([occ], close_succeeds=False), trigs, "SPY")
    assert trigs[0]["exit_reason"] == "close_failed"
    assert trigs[0]["close_failed_prior_reason"] == "stop"
    assert trigs[0]["close_failed_prior_reason"] in ("stop", "stagnation", "time_stop")


def test_prior_reason_is_not_overwritten_on_a_second_failure(tmp_path, monkeypatch):
    """Two failed verifies in a row must not degrade the reason to "close_failed"."""
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    occ = "SPY260914C00762000"
    trigs = [_trig(occ, reason="stagnation")]
    for _ in range(2):
        for t in trigs:           # the agent re-closes it before each verify pass
            t["closed"] = True
        agent._verify_closes_against_broker(FakeTrader([occ], close_succeeds=False), trigs, "SPY")
    assert trigs[0]["close_failed_prior_reason"] == "stagnation"
