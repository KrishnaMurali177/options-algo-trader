#!/usr/bin/env python3
"""Add or reset a dashboard login in ``auth_users.json`` (stdlib only).

Run it inside the dashboard container so it edits the mounted config file::

    docker exec -it options-dashboard \
        python dashboard/make_user.py <username> --role member --display "Name"

Prompts for the password (never echoed), generates a fresh 16-byte salt and
stores a PBKDF2-HMAC-SHA256 hash — plaintext is never written. Creates the file
with a random ``cookie_secret`` (reserved for future signed-cookie sessions) if
it does not yet exist, and chmods it to 0600.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys

from _auth import _CONFIG_PATH, _PBKDF2_ITERATIONS, hash_password

_MIN_PW_LEN = 10


def main() -> int:
    ap = argparse.ArgumentParser(description="Add/reset a dashboard login.")
    ap.add_argument("username")
    ap.add_argument("--role", default="member", choices=["member", "owner"])
    ap.add_argument("--display", default=None, help="Display name (defaults to username)")
    ap.add_argument("--config", default=_CONFIG_PATH)
    args = ap.parse_args()

    username = args.username.strip().lower()
    if not username:
        print("Username required.", file=sys.stderr)
        return 2

    if os.path.exists(args.config):
        with open(args.config) as fh:
            cfg = json.load(fh)
    else:
        cfg = {
            "cookie_secret": secrets.token_hex(32),
            "session_ttl_minutes": 480,
            "users": {},
        }

    pw1 = getpass.getpass(f"Password for '{username}': ")
    if len(pw1) < _MIN_PW_LEN:
        print(f"Password must be at least {_MIN_PW_LEN} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Confirm password: ") != pw1:
        print("Passwords do not match.", file=sys.stderr)
        return 1

    salt = secrets.token_bytes(16)
    cfg.setdefault("users", {})[username] = {
        "display": args.display or args.username,
        "role": args.role,
        "salt": salt.hex(),
        "iterations": _PBKDF2_ITERATIONS,
        "hash": hash_password(pw1, salt, _PBKDF2_ITERATIONS),
    }

    tmp = args.config + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, args.config)
    try:
        os.chmod(args.config, 0o600)
    except OSError:
        pass
    print(f"✓ user '{username}' ({args.role}) written to {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
