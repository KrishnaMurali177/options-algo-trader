#!/usr/bin/env bash
# QA harness for the dashboard auth UI. Runs on the Ubuntu box (Docker).
# Stands up a PRIVATE :8502 instance with a throwaway config — never the live
# dashboard, agents, or real-money account. Usage: bash run_qa.sh [--no-browser]
set -euo pipefail

QA_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$QA_DIR/../../.." && pwd)"
DASH="$REPO/options_agent/dashboard"
STATE="$QA_DIR/state"
REPORTS="$QA_DIR/reports"
CFG_IN_CTR=/app/dashboard/qa/state/auth_config.yaml
IMAGE=options-dashboard
PW_IMAGE=mcr.microsoft.com/playwright/python:v1.47.0-jammy
RUN_BROWSER=1
[ "${1:-}" = "--no-browser" ] && RUN_BROWSER=0

mkdir -p "$STATE" "$REPORTS"
rm -f "$STATE"/*.yaml* "$STATE"/invite_token.txt 2>/dev/null || true

echo "== build $IMAGE if missing =="
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -q -f "$DASH/dashboard.Dockerfile" -t "$IMAGE" "$REPO"
fi
SHA=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "?")

echo "== seed QA config + run deterministic AppTest suite =="
if docker run --rm \
  -e DASHBOARD_AUTH_CONFIG="$CFG_IN_CTR" \
  -v "$DASH":/app/dashboard \
  "$IMAGE" bash -c \
  "python dashboard/qa/qa_bootstrap.py && python dashboard/qa/test_auth_apptest.py"; then
  APPTEST_RC=0
else
  APPTEST_RC=$?
fi

BROWSER_RC=0
if [ "$RUN_BROWSER" = 1 ]; then
  echo "== start private :8502 test instance =="
  docker rm -f oat-qa-dash >/dev/null 2>&1 || true
  docker run -d --name oat-qa-dash -p 8502:8501 \
    -e DASHBOARD_AUTH_CONFIG="$CFG_IN_CTR" \
    -e DASHBOARD_PUBLIC_URL=http://localhost:8502 \
    $([ -f "$REPO/options_agent/.env" ] && echo "--env-file $REPO/options_agent/.env") \
    -v "$DASH":/app/dashboard \
    "$IMAGE" >/dev/null
  for i in $(seq 1 30); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8502/_stcore/health || true)" = "200" ] \
      && { echo "  healthy after ${i}s"; break; }
    sleep 1
  done

  echo "== run Playwright browser suite =="
  INVITE=$(cat "$STATE/invite_token.txt" 2>/dev/null || echo "")
  if docker run --rm --network host \
      -e BASE_URL=http://localhost:8502 -e INVITE_TOKEN="$INVITE" \
      -e QA_REPORT_DIR=/qa/reports \
      -v "$QA_DIR":/qa \
      "$PW_IMAGE" bash -c "pip install -q 'playwright==1.47.0' && python /qa/test_auth_browser.py"; then
    BROWSER_RC=0
  else
    BROWSER_RC=$?
    echo "  (browser suite returned $BROWSER_RC — see reports/ screenshots)"
  fi

  echo "== teardown :8502 =="
  docker rm -f oat-qa-dash >/dev/null 2>&1 || true
fi

echo
echo "===== QA harness summary (image @ $SHA) ====="
echo "AppTest suite rc : $APPTEST_RC"
[ "$RUN_BROWSER" = 1 ] && echo "Browser suite rc : $BROWSER_RC  (screenshots in qa/reports/)"
echo "Report artifacts : $REPORTS"
exit $(( APPTEST_RC != 0 || BROWSER_RC != 0 ? 1 : 0 ))
