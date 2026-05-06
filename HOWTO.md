# How to Run (Docker)

## Prerequisites

- Docker and Docker Compose installed
- No Python setup needed — everything runs inside the container

## Quick Start

**Mac/Linux:**
```bash
./run.sh dashboard
```

**Windows:**
```cmd
run.bat dashboard
```

Opens at http://localhost:8501

## All Commands

| Command | Description |
|---------|-------------|
| `./run.sh dashboard` | Start the Streamlit dashboard at http://localhost:8501 |
| `./run.sh agent` | Start the live sweet spot agent (daemon mode) |
| `./run.sh backtest -- [args]` | Run backtest (e.g. `-- --symbol SPY --period 1y --save`) |
| `./run.sh replay -- [args]` | Run replay sweet spot (e.g. `-- --days 365`) |
| `./run.sh scan` | Scan today's sweet spots |
| `./run.sh test` | Run the test suite |
| `./run.sh shell` | Open a bash shell inside the container |
| `./run.sh down` | Stop all containers |
| `./run.sh build` | Rebuild the Docker image |

## Configuration

On first run, `run.sh` copies `options_agent/.env.example` to `options_agent/.env` if it doesn't exist.

Edit `options_agent/.env` to set:

| Variable | Required For |
|----------|-------------|
| `ROBINHOOD_USERNAME`, `ROBINHOOD_PASSWORD`, `ROBINHOOD_TOTP_SECRET` | Live Robinhood trading |
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | Live agent paper trading, 5-min historical data |
| `GEMINI_API_KEY` | Optional LLM confirmation of trades |
| `DRY_RUN=true` | Safety switch (default: on) |

The dashboard works without any credentials — it uses public market data via yfinance.

## Examples

```bash
# Backtest SPY over 1 year
./run.sh backtest -- --symbol SPY --period 1y --save

# Replay sweet spot strategy on QQQ, last 365 days
./run.sh replay -- --symbol QQQ --days 365

# Scan with custom choppiness threshold
./run.sh scan -- --max-chop 7 --min-stability 3

# Run tests
./run.sh test
```
