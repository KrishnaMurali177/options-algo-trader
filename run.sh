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
    echo "  dashboard    Start the Streamlit dashboard (http://localhost:8501)"
    echo "  agent        Start the live sweet spot agent (daemon mode)"
    echo "  backtest     Run backtest (pass extra args after --)"
    echo "  replay       Run replay sweet spot (pass extra args after --)"
    echo "  scan         Scan today's sweet spots"
    echo "  test         Run the test suite"
    echo "  shell        Open a shell in the container"
    echo "  down         Stop all containers"
    echo "  build        Rebuild the Docker image"
    echo ""
    echo "Examples:"
    echo "  ./run.sh dashboard"
    echo "  ./run.sh backtest -- --symbol SPY --period 1y --save"
    echo "  ./run.sh replay -- --days 365"
    echo "  ./run.sh agent"
}

case "${1:-}" in
    dashboard)
        docker-compose up --build dashboard
        ;;
    agent)
        docker-compose --profile live up --build agent
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
        docker-compose down
        ;;
    build)
        docker-compose build
        ;;
    *)
        usage
        ;;
esac
