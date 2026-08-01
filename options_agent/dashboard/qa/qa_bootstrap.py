#!/usr/bin/env python3
"""Seed a THROWAWAY QA auth config + one live invite token.

Runs inside the options-dashboard image. Writes to $DASHBOARD_AUTH_CONFIG.
CAPTCHA is disabled so signup/login flows can be automated; lockout threshold is
low so it triggers quickly. Never point this at a real config path.

    DASHBOARD_AUTH_CONFIG=/app/dashboard/qa/state/auth_config.yaml python qa_bootstrap.py
"""
import os
import secrets
import sys

import yaml

sys.path.insert(0, "/app/dashboard")
from streamlit_authenticator import Hasher  # noqa: E402

import _auth  # noqa: E402

CFG = os.environ["DASHBOARD_AUTH_CONFIG"]
os.makedirs(os.path.dirname(CFG), exist_ok=True)

# Known QA accounts (safe to hardcode — throwaway instance only).
ACCOUNTS = {
    "qa_owner": (["owner"], "Qa-Owner-123!"),
    "qa_member": (["member"], "Qa-Member-123!"),
}

cfg = {
    "credentials": {"usernames": {}},
    "cookie": {"name": "qa_dash_auth", "key": secrets.token_hex(32), "expiry_days": 1},
    "security": {"login_captcha": False, "register_captcha": False, "max_login_attempts": 3},
    "invites": {},
}
for user, (roles, pw) in ACCOUNTS.items():
    cfg["credentials"]["usernames"][user] = {
        "email": "", "name": user, "password": Hasher().hash(pw),
        "roles": roles, "failed_login_attempts": 0, "logged_in": False,
    }
with open(CFG, "w") as fh:
    yaml.safe_dump(cfg, fh, sort_keys=False)

# One live invite (raw token saved next to the config for the browser test).
cfg = _auth._load_config()
token = _auth.create_invite(cfg, role="member", ttl_hours=24, created_by="qa_owner")
_auth._save_config(cfg)
with open(os.path.join(os.path.dirname(CFG), "invite_token.txt"), "w") as fh:
    fh.write(token)

print("QA config + invite seeded:", CFG)
print("accounts:", ", ".join(ACCOUNTS))
print("invite token file: invite_token.txt")
