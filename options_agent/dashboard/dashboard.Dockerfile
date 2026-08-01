# Dashboard-only image. Extends the base agent image with the auth stack so the
# LIVE TRADING AGENTS' image (options-algo-trader) is never rebuilt/altered by
# dashboard dependencies. Only the dashboard uses this image.
#
# Build (on the box, base image must exist first):
#   docker build -f options_agent/dashboard/dashboard.Dockerfile -t options-dashboard .
FROM options-algo-trader

# Public (Tailscale Funnel) invite-gated auth: bcrypt hashes, signed cookie
# sessions, CAPTCHA, per-user lockout, optional 2FA. Additive only.
RUN pip install --no-cache-dir "streamlit-authenticator==0.4.2"

# Dashboard code is bind-mounted in docker-compose (no rebuild for code edits).
CMD ["streamlit", "run", "dashboard/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.enableXsrfProtection=true", \
     "--browser.gatherUsageStats=false"]
