"""Auth for the public (Tailscale Funnel) invite-gated fund dashboard.

Built on ``streamlit-authenticator`` (bcrypt password hashes, signed HttpOnly
cookie sessions, CAPTCHA, per-user login lockout via ``max_login_attempts``,
optional 2FA) plus a custom **one-time invite-link** layer for self-service
signup.

Security posture (this app is exposed to the public internet and shows
real-money P&L):

- **Passwords** — bcrypt via streamlit-authenticator; never stored or logged in
  clear. Strength enforced by the library's ``Validator``.
- **Sessions** — signed HttpOnly cookie, short expiry (default 7 days,
  configurable). TLS is terminated by Tailscale Funnel.
- **Signup** — only reachable with a valid one-time invite token in the URL
  (``?invite=…``). Tokens are 256-bit (``secrets.token_urlsafe(32)``); only their
  **SHA-256 is stored**, so a leaked config yields no usable links. Single-use,
  expiring.
- **Login** — CAPTCHA + attempt lockout; generic errors (no user enumeration).
- **Config at rest** — gitignored ``auth_config.yaml``, ``chmod 600``, atomic
  writes under an advisory ``flock``. Only hashes + token-hashes are stored.
- **Fail-closed** — a missing/incomplete config blocks the dashboard, never
  exposes it.

NOTE: until per-user data isolation (step 2), every signed-in user sees the
single fund including the live overlay — invite only people you would show your
account to.

Every page must call :func:`require_auth` immediately after
``st.set_page_config()``.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import streamlit as st
import streamlit_authenticator as stauth
import yaml
from streamlit_authenticator.utilities import Validator
from streamlit_authenticator.utilities.exceptions import LoginError, RegisterError

_CONFIG_PATH = os.environ.get(
    "DASHBOARD_AUTH_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_config.yaml"),
)
_PUBLIC_URL = os.environ.get("DASHBOARD_PUBLIC_URL", "").rstrip("/")
_INVITE_TTL_HOURS = int(os.environ.get("DASHBOARD_INVITE_TTL_HOURS", "72"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── config load/save (atomic, locked) ─────────────────────────────────────────
@contextmanager
def _config_lock():
    """Advisory exclusive lock on a sidecar file so concurrent signups/logins
    don't corrupt the yaml."""
    fh = open(_CONFIG_PATH + ".lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _load_config() -> Optional[dict]:
    try:
        with open(_CONFIG_PATH) as fh:
            cfg = yaml.safe_load(fh) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return None
    cfg.setdefault("credentials", {}).setdefault("usernames", {})
    cfg.setdefault("cookie", {})
    cfg.setdefault("invites", {})
    cfg.setdefault("security", {})
    return cfg


def _save_config(cfg: dict) -> None:
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False)
    os.replace(tmp, _CONFIG_PATH)
    try:
        os.chmod(_CONFIG_PATH, 0o600)
    except OSError:
        pass


# ── one-time invite tokens ─────────────────────────────────────────────────────
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(cfg: dict, role: str = "member",
                  ttl_hours: int = _INVITE_TTL_HOURS, created_by: str = "owner") -> str:
    """Mint a one-time invite. Returns the RAW token (shown once); only its hash
    is persisted."""
    token = secrets.token_urlsafe(32)
    cfg["invites"][_hash_token(token)] = {
        "role": role,
        "created_by": created_by,
        "created_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(hours=ttl_hours)).isoformat(),
        "used": False,
        "used_by": None,
    }
    return token


def _invite_record(cfg: dict, token: str) -> Optional[dict]:
    return cfg.get("invites", {}).get(_hash_token(token))


def validate_invite(cfg: dict, token: str) -> Optional[dict]:
    rec = _invite_record(cfg, token)
    if not rec or rec.get("used"):
        return None
    try:
        if _now() > datetime.fromisoformat(rec["expires_at"]):
            return None
    except (KeyError, ValueError):
        return None
    return rec


def consume_invite(cfg: dict, token: str, username: str) -> None:
    rec = _invite_record(cfg, token)
    if rec is not None:
        rec.update(used=True, used_by=username, used_at=_now().isoformat())


def invite_link(token: str) -> str:
    base = _PUBLIC_URL or "https://<your-funnel-host>:8443"
    return f"{base}/?invite={token}"


# ── authenticator ──────────────────────────────────────────────────────────────
def get_authenticator(cfg: dict) -> stauth.Authenticate:
    ck = cfg["cookie"]
    return stauth.Authenticate(
        cfg["credentials"],
        ck.get("name", "options_dash_auth"),
        ck.get("key", ""),
        float(ck.get("expiry_days", 7)),
        validator=Validator(),
        auto_hash=False,  # stored passwords are already bcrypt hashes
    )


def _creds_from_auth(authenticator: stauth.Authenticate, cfg: dict) -> dict:
    try:
        return authenticator.authentication_controller.authentication_model.credentials
    except Exception:
        return cfg["credentials"]


def _persist_failed_login(authenticator: stauth.Authenticate) -> None:
    """Persist lockout counters after a failed attempt so limits survive a
    restart. Best-effort."""
    try:
        creds = authenticator.authentication_controller.authentication_model.credentials
        with _config_lock():
            latest = _load_config()
            if latest is not None:
                latest["credentials"] = creds
                _save_config(latest)
    except Exception:
        pass


# ── UI helpers ─────────────────────────────────────────────────────────────────
def _sidebar(authenticator: stauth.Authenticate, user: dict) -> None:
    with st.sidebar:
        roles = ", ".join(user["roles"]) or "member"
        st.caption(f"🔐 **{user['display']}** · _{roles}_")
        authenticator.logout("Log out", location="sidebar", key="_auth_logout")


def _handle_invite(cfg: dict, authenticator: stauth.Authenticate, token: str) -> None:
    inv = validate_invite(cfg, token)
    if not inv:
        st.error("🚫 This signup link is invalid, already used, or expired. "
                 "Ask the fund owner for a fresh invite.")
        return

    st.markdown("### 🎟️ You've been invited — create your account")
    st.caption("Pick a username and a strong password. This link works once.")
    try:
        _email, username, _name = authenticator.register_user(
            location="main",
            roles=[inv.get("role", "member")],
            captcha=bool(cfg.get("security", {}).get("register_captcha", True)),
            two_factor_auth=False,
            password_hint=True,
            merge_username_email=False,
            fields={"Form name": "Create account"},
            key="_auth_register",
        )
    except RegisterError as exc:
        st.error(str(exc))
        return

    if username:
        # Persist the new (hashed) user and burn the invite, atomically.
        new_creds = _creds_from_auth(authenticator, cfg)
        with _config_lock():
            latest = _load_config() or cfg
            latest["credentials"]["usernames"][username] = \
                new_creds["usernames"][username]
            consume_invite(latest, token, username)
            _save_config(latest)
        st.query_params.clear()
        st.rerun()  # invite param is gone; page reloads showing the login form


def require_auth() -> dict:
    """Gate the current page. Renders invite-signup / login and ``st.stop()``s
    until authenticated. Returns ``{username, display, roles}`` and also stashes
    it in ``st.session_state["auth_user"]`` (the hook for step-2 per-user
    isolation). Fails closed."""
    with _config_lock():
        cfg = _load_config()

    if (not cfg or not cfg["cookie"].get("key")
            or not cfg["credentials"]["usernames"]):
        st.error("🔒 Dashboard authentication is not configured. Bootstrap the "
                 "owner account first — see `dashboard/AUTH_README.md`.")
        st.stop()

    authenticator = get_authenticator(cfg)
    already_authed = st.session_state.get("authentication_status") is True

    # ── one-time invite signup (only when not already logged in) ──
    token = st.query_params.get("invite")
    if token and not already_authed:
        _handle_invite(cfg, authenticator, token)
        st.stop()

    # ── login ──
    sec = cfg.get("security", {})
    try:
        authenticator.login(
            location="main",
            max_login_attempts=int(sec.get("max_login_attempts", 8)),
            captcha=bool(sec.get("login_captcha", True)),
            fields={"Form name": "Sign in — fund dashboard"},
            key="_auth_login",
        )
    except LoginError:
        # Raised by streamlit-authenticator once the per-user attempt counter
        # reaches max_login_attempts.  Show a generic time-based message so
        # we neither confirm whether the account exists nor reveal lockout
        # status to an attacker — consistent with the generic-errors posture.
        _persist_failed_login(authenticator)
        st.error("Too many failed attempts. Please wait a few minutes and try again.")
        st.stop()
    status = st.session_state.get("authentication_status")

    if status is True:
        user = {
            "username": st.session_state.get("username"),
            "display": st.session_state.get("name") or st.session_state.get("username"),
            "roles": st.session_state.get("roles") or ["member"],
        }
        st.session_state["auth_user"] = user
        _sidebar(authenticator, user)
        return user

    if status is False:
        _persist_failed_login(authenticator)
        st.error("Invalid username or password.")
        st.stop()

    st.info("🔐 Please sign in. Access to this dashboard is invite-only.")
    st.stop()


# ── owner admin panel (invite management) ──────────────────────────────────────
def render_admin() -> None:
    user = st.session_state.get("auth_user") or {}
    if "owner" not in (user.get("roles") or []):
        st.error("🚫 Owners only.")
        st.stop()

    st.subheader("🎟️ Invite a contributor")
    col = st.columns([1, 1, 2])
    role = col[0].selectbox("Role", ["member", "owner"], index=0)
    ttl = col[1].number_input("Link valid (hours)", 1, 720, _INVITE_TTL_HOURS)
    if col[2].button("Generate one-time invite link", use_container_width=True):
        with _config_lock():
            cfg = _load_config()
            token = create_invite(cfg, role=role, ttl_hours=int(ttl),
                                   created_by=user["username"])
            _save_config(cfg)
        st.success("Share this link privately — it is shown once and works once:")
        st.code(invite_link(token), language="text")
        if not _PUBLIC_URL:
            st.warning("Set `DASHBOARD_PUBLIC_URL` so links use the real Funnel host.")

    st.divider()
    st.subheader("Outstanding invites")
    cfg = _load_config() or {}
    rows = []
    for h, inv in cfg.get("invites", {}).items():
        exp = inv.get("expires_at", "")
        try:
            expired = _now() > datetime.fromisoformat(exp)
        except ValueError:
            expired = True
        state = "used" if inv.get("used") else ("expired" if expired else "active")
        rows.append({"id": h[:10], "role": inv.get("role"), "state": state,
                     "created_by": inv.get("created_by"), "expires_at": exp,
                     "used_by": inv.get("used_by")})
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No invites yet.")

    st.divider()
    st.subheader("Users")
    st.dataframe(
        [{"username": u, "name": d.get("name"),
          "roles": ", ".join(d.get("roles", []) or [])}
         for u, d in cfg.get("credentials", {}).get("usernames", {}).items()],
        use_container_width=True, hide_index=True,
    )
