# Dashboard authentication

A dependency-free login gate for the Streamlit dashboard. Contributor-only,
hosted over Tailscale (private), no public exposure.

## How it works
- `_auth.py` verifies passwords with **PBKDF2-HMAC-SHA256** (per-user 16-byte
  salt, 240k iterations, `hmac.compare_digest`). Only hashes are stored.
- Credentials live in `auth_users.json` — **gitignored**, box-only, mounted
  read-only into the container. `auth_users.example.json` is the template.
- Every page calls `require_auth()` right after `st.set_page_config()`. It
  **fails closed** (missing config ⇒ dashboard blocked, never exposed) and
  stashes the identity in `st.session_state["auth_user"]`.
- **No third-party dependency** on purpose: the dashboard shares a Docker image
  with the live trading agents, so a new pip dep would force rebuilding that
  image. stdlib-only keeps the agents untouched — deploy is just
  `docker restart options-dashboard`, no rebuild.

## Setup (on the box)
```bash
# create / reset a login (edits the mounted auth_users.json)
docker exec -it options-dashboard \
    python dashboard/make_user.py rohith --role owner --display "Rohith"
docker exec -it options-dashboard \
    python dashboard/make_user.py alice --role member --display "Alice"
# no rebuild — restart to be safe
docker restart options-dashboard
```

## Hosting (Tailscale, private)
Served via `tailscale serve` (tailnet-only HTTPS), **not** Funnel — the
dashboard never touches the public internet. Reachable from any device logged
into the tailnet.

## Session behaviour
Session-scoped: a login lasts `session_ttl_minutes` (default 480) and re-prompts
on a hard refresh. Streamlit can't set a persistent cookie without a custom
component; short sessions are acceptable (arguably desirable) for a real-money
tool. Upgrade path: `streamlit-authenticator` for "remember me" cookies — at the
cost of a shared-image rebuild.

## Threat model
- **Solved (step 1):** only PBKDF2 hashes + a reserved cookie secret at rest;
  generic login errors (no user enumeration); escalating failure throttle;
  traffic WireGuard-encrypted within the tailnet even before TLS.
- **NOT yet solved (step 2 — credential management):** per-user **live-key
  isolation** and per-user **data views**. Until then, every logged-in
  contributor sees the *current single fund*, including the live overlay.
  When step 2 lands, live Alpaca keys must be encrypted per-user and unlockable
  only by that user's own login secret — so even a co-builder with full box/repo
  access cannot read another contributor's live keys.

## Roadmap → step 2 (separate branch, deliberate)
1. Per-user encrypted credential store (unlock key derived from login secret).
2. A separate control-plane service owns the store + agent start/stop — the
   read-only dashboard never holds keys or touches the Docker socket.
3. Per-user data scoping in the dashboard, keyed off `st.session_state["auth_user"]`.
