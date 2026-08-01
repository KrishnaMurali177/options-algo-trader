#!/usr/bin/env python3
"""Real-browser QA of the auth UI via Playwright (Chromium).

Drives the PRIVATE test instance (default http://localhost:8502) through the
browser-only scenarios AppTest can't cover: signup via invite, login + cookie
session surviving a reload, wrong-password generic error, lockout, and logout.
Saves a screenshot per step to qa/reports/ as evidence.

SCAFFOLD: Streamlit's async rendering + component iframes make selectors
brittle. The ui-qa-tester agent validates and hardens these on first run. Run:

    BASE_URL=http://localhost:8502 INVITE_TOKEN=$(cat state/invite_token.txt) \
        python test_auth_browser.py
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://localhost:8502")
INVITE = os.environ.get("INVITE_TOKEN", "")
SHOTS = os.environ.get("QA_REPORT_DIR", "/app/dashboard/qa/reports")
os.makedirs(SHOTS, exist_ok=True)
RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def shot(page, name):
    page.screenshot(path=os.path.join(SHOTS, f"{name}.png"))


def settle(page, ms=2500):
    page.wait_for_load_state("networkidle")
    time.sleep(ms / 1000)


def login(page, user, pw):
    page.goto(BASE)
    settle(page)
    page.get_by_label("Username").first.fill(user)
    page.get_by_label("Password").first.fill(pw)
    page.get_by_role("button", name="Login").first.click()
    settle(page)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        # 1) Gate: unauthenticated shows a login form.
        page.goto(BASE)
        settle(page)
        shot(page, "01_gate")
        check("gate: login form visible unauthenticated",
              page.get_by_label("Username").count() > 0)

        # 2) Signup via invite → then login as the new user.
        if INVITE:
            page.goto(f"{BASE}/?invite={INVITE}")
            settle(page)
            shot(page, "02_signup")
            try:
                new_user = f"qa_new_{int(time.time())}"
                page.get_by_label("Username").first.fill(new_user)
                # streamlit-authenticator register form: first/last name, email, pw x2
                for lbl, val in [("Email", f"{new_user}@qa.local"),
                                 ("First name", "Qa"), ("Last name", "New")]:
                    loc = page.get_by_label(lbl)
                    if loc.count():
                        loc.first.fill(val)
                pw_fields = page.get_by_label("Password")
                pw_fields.first.fill("Qa-New-123!")
                if pw_fields.count() > 1:
                    pw_fields.nth(1).fill("Qa-New-123!")
                page.get_by_role("button", name="Register").first.click()
                settle(page)
                shot(page, "03_signup_done")
                check("signup: no visible traceback", "Traceback" not in page.content())
            except Exception as e:  # noqa: BLE001
                check("signup: form drove without error", False)
                print("  signup exception:", e)

        # 3) Login happy path (known QA owner).
        login(page, "qa_owner", "Qa-Owner-123!")
        shot(page, "04_login")
        logged_in = page.get_by_role("button", name="Log out").count() > 0
        check("login: reaches app (logout control present)", logged_in)

        # 4) Session survives a reload (cookie).
        page.reload()
        settle(page)
        shot(page, "05_reload")
        check("session: still logged in after reload",
              page.get_by_role("button", name="Log out").count() > 0)

        # 5) Logout returns to login.
        if page.get_by_role("button", name="Log out").count():
            page.get_by_role("button", name="Log out").first.click()
            settle(page)
            shot(page, "06_logout")
            check("logout: back to login form",
                  page.get_by_label("Username").count() > 0)

        # 6) Wrong password → generic error, not logged in.
        login(page, "qa_owner", "wrong-password")
        shot(page, "07_wrongpw")
        check("login: wrong password rejected",
              page.get_by_role("button", name="Log out").count() == 0)

        # 7) Lockout after repeated failures (max_login_attempts=3 in QA config).
        for _ in range(3):
            login(page, "qa_member", "wrong-again")
        shot(page, "08_lockout")
        check("lockout: still not logged in after repeated failures",
              page.get_by_role("button", name="Log out").count() == 0)

        browser.close()

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nBrowser suite: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
