# Dashboard authentication — public (Funnel), invite-only

The fund dashboard is exposed to the public internet via **Tailscale Funnel**
(HTTPS) and gated by an **invite-only** account system. Anyone can reach the URL;
only holders of a valid one-time invite link can create an account, and only
signed-in users see anything.

## Stack
- **`streamlit-authenticator` 0.4.2** — bcrypt password hashes, signed HttpOnly
  cookie sessions, CAPTCHA, per-user login **lockout** (`max_login_attempts`),
  optional 2FA.
- **`_auth.py`** — adds a custom **one-time invite-link** layer + `require_auth()`
  (called at the top of every page) + the owner Admin panel.
- **Decoupled image** (`dashboard.Dockerfile` → `options-dashboard`): the base
  agent image plus the auth deps, so the **live trading agents are never
  rebuilt** by dashboard dependencies.

## Security posture
| Concern | Mitigation |
|---|---|
| Password theft | bcrypt hashes only; strength enforced by `Validator`; never logged |
| Sessions | signed HttpOnly cookie, short expiry; TLS via Funnel |
| Signup abuse | reachable only with a valid `?invite=` token — 256-bit, **SHA-256 stored** (leaked config ⇒ no usable links), single-use, expiring |
| Brute force | CAPTCHA + per-user lockout; generic errors (no user enumeration) |
| Config at rest | gitignored `auth_config.yaml`, `chmod 600`, atomic writes under `flock` |
| Misconfig | **fail-closed** — blocks the dashboard, never exposes it |
| XSRF | Streamlit `enableXsrfProtection=true` |

⚠️ **Data privacy gap (until step 2):** every signed-in user sees the *single
fund* including the live overlay. Invite only people you would show your account
to. Step 2 adds per-user data/credential isolation.

## Bootstrap (once, on the box)
```bash
# 1) build the decoupled dashboard image (base image must exist first)
cd ~/projects/options_algo/options-algo-trader
docker build -f options_agent/dashboard/dashboard.Dockerfile -t options-dashboard .

# 2) create the owner account (writes gitignored auth_config.yaml)
touch options_agent/dashboard/auth_config.yaml   # so the RW mount has a target
docker compose run --rm dashboard \
    python dashboard/make_user.py rohith --name "Rohith" --role owner

# 3) bring the dashboard up on the new image (still private on :8501)
docker-compose up -d dashboard
```

## Go public (you run this — needs sudo)
```bash
# expose ONLY the dashboard, on Funnel port 8443 (":443/" is the multilingual app)
sudo tailscale funnel --bg --https=8443 127.0.0.1:8501
# set the public host so invite links render correctly, then restart:
#   DASHBOARD_PUBLIC_URL=https://<host>.ts.net:8443  (in options_agent/.env)
```
Public URL: `https://<host>.tailXXXX.ts.net:8443`

## Inviting contributors
1. Sign in as owner → **Admin** page → *Generate one-time invite link*.
2. Send the link privately (it is shown once, works once, expires).
3. They open it, pick a username + strong password (CAPTCHA), and are in.

## Roadmap → step 2 (separate branch)
Per-user **encrypted** credential store (unlock derived from the user's own login
secret, so even box/repo access can't read another user's live keys) + per-user
data scoping in the dashboard, keyed off `st.session_state["auth_user"]`.
Optional hardening already available in the stack: **owner TOTP/2FA** and
`Encryptor` for encrypting the whole config at rest.
