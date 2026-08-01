# Dashboard auth — QA harness

Reproducible tests for the auth UI, driven by the `ui-qa-tester` /
`qa-triage-developer` subagents (see `.claude/agents/`). Runs on the Ubuntu box
via Docker against a **private, disposable `:8502` instance** — never the live
dashboard, the trading agents, or the real-money account.

## Run
```bash
# on the box, from the repo:
bash options_agent/dashboard/qa/run_qa.sh            # AppTest + Playwright
bash options_agent/dashboard/qa/run_qa.sh --no-browser  # AppTest only (no browser image)
```

## What runs
| File | Layer | Covers |
|---|---|---|
| `qa_bootstrap.py` | seed | throwaway config: CAPTCHA off, `max_login_attempts: 3`, owner+member accounts, one live invite token |
| `test_auth_apptest.py` | Streamlit AppTest (deterministic, no browser) | gate enforcement, invite validate/expire/single-use, fail-closed, no content leak |
| `test_auth_browser.py` | Playwright (Chromium) | signup via invite, login, cookie session survives reload, wrong-password generic error, lockout, logout |

Artifacts (config, invite token, screenshots) land in `qa/state/` and
`qa/reports/` — both gitignored.

## Notes
- The Playwright spec is a **scaffold**: Streamlit's async rendering + component
  iframes make selectors brittle, so the `ui-qa-tester` agent validates and
  hardens it on first run. Failures there are often selector/timing issues in
  the *test*, not the product — which is exactly why `qa-triage-developer`
  reproduces before fixing and can classify them `ENV-ARTIFACT`.
- CAPTCHA is disabled in the QA config so flows automate; a separate check should
  confirm CAPTCHA renders when `login_captcha`/`register_captcha` are true.
