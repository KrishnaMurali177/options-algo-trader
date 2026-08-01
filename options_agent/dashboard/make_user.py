#!/usr/bin/env python3
"""Bootstrap the FIRST owner account for the dashboard (one-time).

Everyone else self-signs-up through a one-time invite link in the browser; this
script only exists to create the initial owner so the invite machinery has an
admin. Run it inside the dashboard container so it writes the mounted config::

    docker exec -it options-dashboard \
        python dashboard/make_user.py rohith --name "Rohith" --role owner

Prompts for the password (never echoed), hashes it with bcrypt via
streamlit-authenticator's Hasher, and writes/updates the gitignored
``auth_config.yaml`` (creating a random cookie key if absent), chmod 0600.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys

import yaml
from streamlit_authenticator import Hasher
from streamlit_authenticator.utilities import Validator

from _auth import _CONFIG_PATH

_MIN_PW_LEN = 10


def main() -> int:
    ap = argparse.ArgumentParser(description="Bootstrap the dashboard owner account.")
    ap.add_argument("username")
    ap.add_argument("--name", default=None, help="Display name (defaults to username)")
    ap.add_argument("--email", default="", help="Optional; used only for reset/2FA")
    ap.add_argument("--role", default="owner", choices=["owner", "member"])
    ap.add_argument("--config", default=_CONFIG_PATH)
    args = ap.parse_args()

    username = args.username.strip().lower()
    if not username:
        print("Username required.", file=sys.stderr)
        return 2

    if os.path.exists(args.config):
        with open(args.config) as fh:
            cfg = yaml.safe_load(fh) or {}
    else:
        cfg = {}
    cfg.setdefault("credentials", {}).setdefault("usernames", {})
    cfg.setdefault("cookie", {})
    cfg.setdefault("invites", {})
    cfg.setdefault("security", {"login_captcha": True, "max_login_attempts": 8})
    cfg["cookie"].setdefault("name", "options_dash_auth")
    cfg["cookie"].setdefault("expiry_days", 7)
    if not cfg["cookie"].get("key"):
        cfg["cookie"]["key"] = secrets.token_hex(32)

    pw1 = getpass.getpass(f"Password for '{username}': ")
    if not Validator().validate_password(pw1) or len(pw1) < _MIN_PW_LEN:
        print("Password too weak (need length + upper/lower/digit/special).",
              file=sys.stderr)
        return 1
    if getpass.getpass("Confirm password: ") != pw1:
        print("Passwords do not match.", file=sys.stderr)
        return 1

    cfg["credentials"]["usernames"][username] = {
        "email": args.email,
        "name": args.name or args.username,
        "password": Hasher().hash(pw1),
        "roles": [args.role],
        "failed_login_attempts": 0,
        "logged_in": False,
    }

    tmp = args.config + ".tmp"
    with open(tmp, "w") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False)
    os.replace(tmp, args.config)
    try:
        os.chmod(args.config, 0o600)
    except OSError:
        pass
    print(f"✓ {args.role} '{username}' written to {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
