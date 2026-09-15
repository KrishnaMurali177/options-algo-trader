"""Tests that no Alpaca HTTP call can block the scan loop indefinitely.

alpaca-py's RESTClient takes no timeout and passes none to its requests
Session, so a hung socket blocks until the server lets go. On 2026-09-14 that
was 304 seconds on the live account, inside a single-threaded 5-min scan loop.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils import alpaca_http  # noqa: E402


class FakeSession:
    """Stands in for requests.Session — records what timeout it was handed."""

    def __init__(self):
        self.seen = []

    def request(self, method, url, **kwargs):
        self.seen.append(kwargs.get("timeout", "NONE"))
        return {"ok": True}


class FakeClient:
    def __init__(self):
        self._session = FakeSession()


def test_unbounded_session_is_the_bug_being_fixed():
    """Baseline: without the patch the SDK sends no timeout at all."""
    c = FakeClient()
    c._session.request("GET", "/v2/options/contracts")
    assert c._session.seen == ["NONE"]


def test_bound_session_injects_a_default_timeout():
    c = alpaca_http.bound_session(FakeClient())
    c._session.request("GET", "/v2/options/contracts")
    connect, read = c._session.seen[0]
    assert connect == alpaca_http.DEFAULT_CONNECT_TIMEOUT
    assert read == alpaca_http.DEFAULT_READ_TIMEOUT


def test_timeout_is_well_inside_one_five_minute_bar():
    """The whole point: a stall must not outlive the bar it happens on."""
    assert alpaca_http.DEFAULT_CONNECT_TIMEOUT + alpaca_http.DEFAULT_READ_TIMEOUT < 300


def test_an_explicit_timeout_is_not_overridden():
    c = alpaca_http.bound_session(FakeClient())
    c._session.request("GET", "/x", timeout=1)
    assert c._session.seen[0] == 1


def test_binding_twice_does_not_stack_wrappers():
    c = FakeClient()
    alpaca_http.bound_session(c)
    wrapped = c._session.request
    alpaca_http.bound_session(c)
    assert c._session.request is wrapped


def test_custom_values_are_honoured():
    c = alpaca_http.bound_session(FakeClient(), connect=1.0, read=2.0)
    c._session.request("GET", "/x")
    assert c._session.seen[0] == (1.0, 2.0)


def test_a_client_without_a_session_is_left_alone():
    class NoSession:
        pass
    obj = NoSession()
    assert alpaca_http.bound_session(obj) is obj


def test_install_patches_the_sdk_once(monkeypatch):
    monkeypatch.setattr(alpaca_http, "_INSTALLED", False)
    from alpaca.common.rest import RESTClient
    original = RESTClient.__init__
    try:
        assert alpaca_http.install_default_timeouts() is True
        assert RESTClient.__init__ is not original
        # Second call is a no-op rather than double-wrapping.
        assert alpaca_http.install_default_timeouts() is False
    finally:
        RESTClient.__init__ = original
        alpaca_http._INSTALLED = False


def test_install_never_raises_even_if_the_sdk_moves(monkeypatch):
    """A missing time limit must not stop the agent from starting."""
    monkeypatch.setattr(alpaca_http, "_INSTALLED", False)
    monkeypatch.setitem(sys.modules, "alpaca.common.rest", None)
    assert alpaca_http.install_default_timeouts() is False


def test_chain_paging_is_capped():
    """The loop was `while True` — per-request timeouts alone can't bound it."""
    from src.utils import alpaca_data
    assert alpaca_data._CHAIN_MAX_PAGES > 0
    assert 0 < alpaca_data._CHAIN_BUDGET_SEC < 300
