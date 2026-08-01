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
def _inject_chrome_css() -> None:
    """Hide Streamlit's dev chrome (Deploy button, hamburger, top bar, footer)
    on every page — this is a product, not a dev preview."""
    st.markdown(
        "<style>"
        '[data-testid="stToolbar"],[data-testid="stDecoration"],'
        "#MainMenu,footer{display:none !important;}"
        "</style>",
        unsafe_allow_html=True,
    )


def _inject_auth_css(max_width: int = 460) -> None:
    """Aurora theme for the unauthenticated screens: dark charcoal + violet
    accent, hidden sidebar, centered card. Injected only when NOT logged in, so
    it never bleeds into the dashboard."""
    st.markdown(
        f"""<style>
    [data-testid="stSidebar"],[data-testid="collapsedControl"]{{display:none !important;}}
    .stApp,[data-testid="stAppViewContainer"]{{
        background:radial-gradient(1100px 620px at 50% -12%,#1b1740 0%,#0b0d12 60%) !important;}}
    .block-container{{max-width:{max_width}px !important;padding-top:7vh !important;margin:0 auto !important;}}
    [data-testid="stForm"]{{
        background:#151823 !important;border:1px solid #262b3d !important;border-radius:16px !important;
        padding:26px 26px 18px !important;
        box-shadow:0 24px 70px rgba(0,0,0,.55),0 0 0 1px rgba(124,92,255,.08),0 0 60px rgba(124,92,255,.10) !important;}}
    [data-testid="stForm"] label{{color:#aeb4c2 !important;font-size:.8rem !important;font-weight:500 !important;}}
    [data-testid="stTextInput"] input{{
        background:#0e1117 !important;border:1px solid #2a2f42 !important;color:#eaecef !important;
        border-radius:10px !important;padding:.6rem .8rem !important;}}
    [data-testid="stTextInput"] input:focus{{
        border-color:#7c5cff !important;box-shadow:0 0 0 2px rgba(124,92,255,.25) !important;}}
    [data-testid="stFormSubmitButton"] button{{
        background:linear-gradient(180deg,#8b6cff,#7c5cff) !important;color:#fff !important;border:0 !important;
        border-radius:10px !important;width:100% !important;padding:.6rem 1rem !important;
        font-weight:600 !important;letter-spacing:.02em !important;margin-top:.5rem !important;}}
    [data-testid="stFormSubmitButton"] button:hover{{filter:brightness(1.08) !important;}}
    .auth-brand{{text-align:center;margin:0 auto 18px;max-width:{max_width}px;}}
    .auth-logo{{font-size:1.4rem;font-weight:800;letter-spacing:.14em;color:#eaecef;
        font-family:Inter,system-ui,sans-serif;}}
    .auth-logo .accent{{color:#7c5cff;}} .auth-logo .dia{{color:#7c5cff;margin-right:.35rem;}}
    .auth-tag{{color:#8890a2;font-size:.82rem;letter-spacing:.03em;margin-top:.3rem;}}
    .auth-foot{{text-align:center;color:#5c6478;font-size:.72rem;letter-spacing:.05em;
        margin:14px auto 0;max-width:{max_width}px;}}
    .auth-err{{max-width:{max_width}px;margin:12px auto 0;background:rgba(255,77,79,.08);
        border:1px solid rgba(255,77,79,.35);color:#ff9a9a;padding:.5rem .8rem;border-radius:10px;
        font-size:.85rem;text-align:center;}}
    </style>""",
        unsafe_allow_html=True,
    )


def _brand(tagline: str) -> None:
    st.markdown(
        f'<div class="auth-brand"><div class="auth-logo">'
        f'<span class="dia">◆</span>OPTIONS <span class="accent">AGENT</span></div>'
        f'<div class="auth-tag">{tagline}</div></div>',
        unsafe_allow_html=True,
    )


def _sidebar(authenticator: stauth.Authenticate, user: dict) -> None:
    with st.sidebar:
        roles = ", ".join(user["roles"]) or "member"
        st.caption(f"🔐 **{user['display']}** · _{roles}_")
        authenticator.logout("Log out", location="sidebar", key="_auth_logout")


def _handle_invite(cfg: dict, authenticator: stauth.Authenticate, token: str) -> None:
    inv = validate_invite(cfg, token)
    if not inv:
        st.markdown(
            '<div class="auth-err">This signup link is invalid, already used, or '
            'expired. Ask the fund owner for a fresh invite.</div>',
            unsafe_allow_html=True,
        )
        return

    try:
        _email, username, _name = authenticator.register_user(
            location="main",
            roles=[inv.get("role", "member")],
            captcha=bool(cfg.get("security", {}).get("register_captcha", True)),
            two_factor_auth=False,
            password_hint=False,
            merge_username_email=False,
            fields={"Form name": ""},
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
    _inject_chrome_css()
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
        _inject_auth_css(max_width=620)
        _brand("You've been invited — create your account")
        _handle_invite(cfg, authenticator, token)
        st.stop()

    # ── login ──
    sec = cfg.get("security", {})
    brand_slot = st.empty()  # reserve a slot ABOVE the login form
    login_error = None
    try:
        authenticator.login(
            location="main",
            max_login_attempts=int(sec.get("max_login_attempts", 8)),
            captcha=bool(sec.get("login_captcha", True)),
            fields={"Form name": ""},
            key="_auth_login",
        )
    except LoginError:
        # Once the per-user attempt counter hits max_login_attempts. Generic
        # time-based message — neither confirms the account exists nor reveals
        # lockout status to an attacker (generic-errors posture).
        _persist_failed_login(authenticator)
        login_error = "Too many failed attempts. Please wait a few minutes and try again."

    status = st.session_state.get("authentication_status")

    if status is True:
        brand_slot.empty()
        user = {
            "username": st.session_state.get("username"),
            "display": st.session_state.get("name") or st.session_state.get("username"),
            "roles": st.session_state.get("roles") or ["member"],
        }
        st.session_state["auth_user"] = user
        _sidebar(authenticator, user)
        return user

    if status is False and login_error is None:
        _persist_failed_login(authenticator)
        login_error = "Invalid username or password."

    # Not authenticated → dress the screen: brand above the form + Aurora theme.
    with brand_slot.container():
        _brand("private fund dashboard")
    _inject_auth_css()
    if login_error:
        st.markdown(f'<div class="auth-err">{login_error}</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="auth-foot">🔒 invite-only · encrypted · access is logged</div>',
        unsafe_allow_html=True,
    )
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
