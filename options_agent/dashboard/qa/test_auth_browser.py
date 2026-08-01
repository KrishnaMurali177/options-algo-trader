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
                # Streamlit-authenticator register form: first name, last name, email,
                # username, password, repeat password, password hint.
                # Use get_by_label().locator("input") to skip the ? help buttons that
                # also carry aria-label="Help for Password" etc. — they resolve first
                # when using .first and cause a fill() error on a <button>.
                def fill_label(label, val):
                    loc = page.get_by_label(label).locator("input")
                    if loc.count() == 0:
                        # Fallback: get_by_label without input filter (e.g. plain inputs)
                        loc = page.get_by_label(label).first
                    else:
                        loc = loc.first
                    loc.fill(val)

                fill_label("First name", "Qa")
                fill_label("Last name", "New")
                fill_label("Email", f"{new_user}@qa.local")
                fill_label("Username", new_user)
                # Password fields: filter to input[type=password] to avoid help button
                pw_inputs = page.locator("input[type='password']")
                pw_inputs.first.fill("Qa-New-123!")
                if pw_inputs.count() > 1:
                    pw_inputs.nth(1).fill("Qa-New-123!")
                page.get_by_role("button", name="Register").first.click()
                settle(page)
                shot(page, "03_signup_done")
                check("signup: no visible traceback", "Traceback" not in page.content())
            except Exception as e:  # noqa: BLE001
                check("signup: form drove without error", False)
                print("  signup exception:", e)

        # 2b) Reused invite shows error (OBS-003 regression scenario).
        # The same INVITE token was consumed in step 2.  Revisiting it
        # must show the "invalid/used/expired" error, not a signup form.
        if INVITE:
            page.goto(f"{BASE}/?invite={INVITE}")
            settle(page)
            shot(page, "09_reused_invite")
            body = page.content()
            check("invite: reused token shows error not signup form",
                  "invalid" in body.lower() or "used" in body.lower() or
                  "expired" in body.lower())
            check("invite: reused token does not show register form",
                  page.get_by_role("button", name="Register").count() == 0)

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
        # 3 attempts exhaust the counter; the 4th triggers LoginError → lockout msg.
        for _ in range(4):
            login(page, "qa_member", "wrong-again")
        shot(page, "08_lockout")
        check("lockout: still not logged in after repeated failures",
              page.get_by_role("button", name="Log out").count() == 0)
        # QA-001 regression: lockout must show the generic time-based message
        # (not a traceback, not "Invalid username or password.").
        lockout_body = page.content()
        check("lockout: shows too-many-attempts message (no traceback)",
              "too many" in lockout_body.lower() and "Traceback" not in lockout_body)

        browser.close()

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nBrowser suite: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
