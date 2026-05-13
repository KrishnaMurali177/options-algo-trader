#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE="options_agent/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Creating .env from template..."
    cp options_agent/.env.example "$ENV_FILE"
    echo ""
    echo "==> Edit $ENV_FILE with your credentials before running the agent live."
    echo "    The dashboard works without credentials (uses public market data)."
    echo ""
fi

usage() {
    echo "Usage: ./run.sh <command>"
    echo ""
    echo "Commands:"
    echo "  all          Start dashboard + both agents (everything)"
    echo "  dashboard    Start the Streamlit dashboard (http://localhost:8501)"
    echo "  agents       Start both SPY + QQQ agents (daemon, background)"
    echo "  agent-spy    Start only the SPY agent"
    echo "  agent-qqq    Start only the QQQ agent"
    echo "  status       Show running agent containers and recent logs"
    echo "  stop-agents  Stop all agents"
    echo "  restart      Rebuild and restart everything"
    echo "  backtest     Run backtest (pass extra args after --)"
    echo "  replay       Run replay sweet spot (pass extra args after --)"
    echo "  scan         Scan today's sweet spots"
    echo "  test         Run the test suite"
    echo "  shell        Open a shell in the container"
    echo "  down         Stop all containers"
    echo "  build        Rebuild the Docker image"
    echo ""
    echo "Examples:"
    echo "  ./run.sh agents"
    echo "  ./run.sh status"
    echo "  ./run.sh stop-agents"
    echo "  ./run.sh backtest -- --symbol SPY --period 1y --save"
    echo "  ./run.sh replay -- --days 365"
}

CAFFEINATE_PID_FILE="/tmp/options-agent-caffeinate.pid"

_start_caffeinate() {
    _stop_caffeinate 2>/dev/null
    caffeinate -s &
    echo $! > "$CAFFEINATE_PID_FILE"
    echo "Sleep prevention enabled (caffeinate PID: $!)"
}

_stop_caffeinate() {
    if [ -f "$CAFFEINATE_PID_FILE" ]; then
        kill "$(cat "$CAFFEINATE_PID_FILE")" 2>/dev/null || true
        rm -f "$CAFFEINATE_PID_FILE"
        echo "Sleep prevention disabled."
    fi
}

case "${1:-}" in
    all)
        echo "Building image and starting dashboard + agents..."
        docker-compose --profile live up --build -d
        _start_caffeinate
        echo "Dashboard: http://localhost:8501"
        echo "Agents: SPY + QQQ running. Use './run.sh status' to check."
        ;;
    dashboard)
        docker-compose up --build dashboard
        ;;
    agents)
        echo "Building image and starting SPY + QQQ agents..."
        docker-compose build dashboard
        docker-compose --profile live up -d agent-spy agent-qqq
        _start_caffeinate
        echo "Both agents running. Use './run.sh status' to check."
        ;;
    agent-spy)
        docker-compose build dashboard
        docker-compose --profile live up -d agent-spy
        _start_caffeinate
        echo "SPY agent running."
        ;;
    agent-qqq)
        docker-compose build dashboard
        docker-compose --profile live up -d agent-qqq
        _start_caffeinate
        echo "QQQ agent running."
        ;;
    status)
        echo "=== Running Containers ==="
        docker ps --filter "name=options-agent" --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"
        echo ""
        echo "=== SPY Agent (last 10 lines) ==="
        docker logs --tail 10 options-agent-spy 2>/dev/null || echo "  (not running)"
        echo ""
        echo "=== QQQ Agent (last 10 lines) ==="
        docker logs --tail 10 options-agent-qqq 2>/dev/null || echo "  (not running)"
        ;;
    stop-agents)
        echo "Stopping agents..."
        docker-compose --profile live stop agent-spy agent-qqq
        _stop_caffeinate
        echo "Agents stopped."
        ;;
    restart)
        echo "Stopping all containers..."
        docker-compose --profile live down
        echo "Rebuilding image..."
        docker-compose build dashboard
        echo "Starting dashboard + agents..."
        docker-compose --profile live up -d
        _start_caffeinate
        echo "All restarted. Dashboard: http://localhost:8501"
        echo "Use './run.sh status' to check agents."
        ;;
    backtest)
        shift
        docker-compose run --rm --no-deps dashboard python scripts/backtest.py "$@"
        ;;
    replay)
        shift
        docker-compose run --rm --no-deps dashboard python scripts/replay_sweet_spot.py "$@"
        ;;
    scan)
        shift
        docker-compose run --rm --no-deps dashboard python scripts/scan_sweet_spot_today.py "$@"
        ;;
    test)
        docker-compose run --rm --no-deps dashboard pytest tests/ -v
        ;;
    shell)
        docker-compose run --rm --no-deps dashboard bash
        ;;
    down)
        docker-compose --profile live down
        _stop_caffeinate
        ;;
    build)
        docker-compose build
        ;;
    *)
        usage
        ;;
esac
