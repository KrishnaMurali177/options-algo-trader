"""Lightweight, dependency-free auth gate for the dashboard (stdlib only).

Passwords are verified with PBKDF2-HMAC-SHA256 over a per-user 16-byte salt.
Credentials live in an untracked JSON file (``auth_users.json``) that is mounted
read-only into the container; only PBKDF2 hashes are stored, never plaintext.

The gate is session-scoped: a login persists for the browser session and
re-prompts on a hard refresh (Streamlit cannot set a persistent cookie without a
custom component; that is a deliberate step-1 trade-off, and short sessions are a
feature for a real-money tool).

Every dashboard page MUST call :func:`require_auth` immediately AFTER
``st.set_page_config()``. It returns the identity dict and also stashes it in
``st.session_state["auth_user"]`` — the hook that future per-user data/credential
isolation (step 2) will key off.

No third-party dependency is used on purpose: the dashboard shares a Docker image
with the live trading agents, so adding a pip dependency would force a rebuild of
that image. stdlib-only keeps the live agents untouched.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Optional

import streamlit as st

_CONFIG_PATH = os.environ.get(
    "DASHBOARD_AUTH_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_users.json"),
)
_PBKDF2_ITERATIONS = 240_000
_SESSION_KEY = "auth_user"
_FAIL_KEY = "_auth_fail_count"


def hash_password(password: str, salt: bytes, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """PBKDF2-HMAC-SHA256 hash of *password*, returned as hex."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return dk.hex()


def _load_config() -> Optional[dict]:
    try:
        with open(_CONFIG_PATH) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return None


def _verify(password: str, rec: dict) -> bool:
    try:
        salt = bytes.fromhex(rec["salt"])
        iterations = int(rec.get("iterations", _PBKDF2_ITERATIONS))
        expected = rec["hash"]
    except (KeyError, ValueError, TypeError):
        return False
    candidate = hash_password(password, salt, iterations)
    return hmac.compare_digest(candidate, expected)


def _session_ttl_seconds(cfg: dict) -> int:
    return int(cfg.get("session_ttl_minutes", 480)) * 60


def logout() -> None:
    st.session_state.pop(_SESSION_KEY, None)
    st.rerun()


def _current_user(cfg: dict) -> Optional[dict]:
    u = st.session_state.get(_SESSION_KEY)
    if not u:
        return None
    if time.time() - u.get("login_at", 0) > _session_ttl_seconds(cfg):
        st.session_state.pop(_SESSION_KEY, None)
        return None
    return u


def _sidebar_identity(user: dict) -> None:
    with st.sidebar:
        st.caption(
            f"🔐 Signed in as **{user.get('display', user['username'])}** "
            f"· _{user.get('role', 'member')}_"
        )
        if st.button("Log out", use_container_width=True, key="_auth_logout"):
            logout()


def _login_form(cfg: dict) -> None:
    st.markdown("### 🔐 Sign in")
    st.caption("Contributor access only · private to the Tailscale network.")
    with st.form("_auth_login", clear_on_submit=False):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)
    if not submitted:
        return

    users = (cfg or {}).get("users", {})
    key = username.strip().lower() if username else ""
    rec = users.get(key)
    if rec and _verify(password, rec):
        st.session_state[_SESSION_KEY] = {
            "username": key,
            "display": rec.get("display", username),
            "role": rec.get("role", "member"),
            "login_at": time.time(),
        }
        st.session_state.pop(_FAIL_KEY, None)
        st.rerun()
    else:
        # Generic message (no user enumeration) + escalating throttle vs brute force.
        st.session_state[_FAIL_KEY] = st.session_state.get(_FAIL_KEY, 0) + 1
        time.sleep(min(2.0, 0.4 * st.session_state[_FAIL_KEY]))
        st.error("Invalid username or password.")


def require_auth() -> dict:
    """Gate the current page. Renders a login form and ``st.stop()``s until the
    visitor authenticates. Returns the identity dict
    ``{username, display, role, login_at}``.

    Fails closed: if the config is missing/empty the dashboard is blocked, never
    exposed. Call immediately AFTER ``st.set_page_config()``.
    """
    cfg = _load_config()
    if not cfg or not cfg.get("users"):
        st.error(
            "🔒 Dashboard authentication is not configured. Create "
            "`auth_users.json` (copy `auth_users.example.json` or run "
            "`python dashboard/make_user.py <username>`)."
        )
        st.stop()

    user = _current_user(cfg)
    if user:
        _sidebar_identity(user)
        return user

    _login_form(cfg)
    st.stop()
