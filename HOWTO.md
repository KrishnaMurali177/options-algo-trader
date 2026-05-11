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

## Live Agents (Paper Trading)

The sweet spot agent scans SPY and QQQ every 5 minutes during market hours, buys 0DTE options on your Alpaca paper account, and auto-manages exits (stops, decay targets, stagnation, Gainz reversals).

### Setup

1. Get a free Alpaca paper trading account at https://alpaca.markets
2. Add your keys to `options_agent/.env`:
   ```
   ALPACA_API_KEY=your_key_here
   ALPACA_SECRET_KEY=your_secret_here
   ```

### Running the Agents

```bash
# Start both SPY + QQQ agents in background
./run.sh agents

# Start only one
./run.sh agent-spy
./run.sh agent-qqq

# Check status and recent logs
./run.sh status

# Stop agents
./run.sh stop-agents
```

Both agents run in daemon mode — they auto-restart daily, sleep through weekends, and survive container restarts. A cron job can be added for extra reliability:

```bash
# Add to crontab — ensures agents are running every 30 min during market hours
*/30 6-13 * * 1-5 cd /path/to/options-algo-trader && docker-compose --profile live up -d agent-spy agent-qqq
```

### Agent Parameters

Both agents run with golden defaults (validated over 730 days of backtesting):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--symbol` | SPY / QQQ | Ticker to trade |
| `--contracts` | 1 | Base contracts per trade |
| `--cascade-size-low/mid/high` | 3/3/3 | Contract multiplier by explosion tier |
| `--target-delta` | 0.50 | ATM option selection |
| `--max-trades-per-day` | 3 | Daily trade cap |
| `--max-stops-per-day` | 1 | Halt after 1 stop-out |
| `--max-consecutive-losses` | 2 | Streak breaker |
| `--max-chop` | 5 | Max choppiness score |
| `--scan-start-min` | 60 | Wait 60 min after open (10:30 AM ET) |
| `--vix-max` | 30 | Skip day if VIX > 30 |
| `--vix-spike-pct` | 20 | Skip day if VIX spiked > 20% |

To customize, edit the `command:` in `docker-compose.yml` for each agent service.

### Logs and Journal

- Agent logs: `options_agent/logs/sweet_spot_agent.log`
- Trade journal: `options_agent/sweet_spot_journal/YYYY-MM-DD.json`
- Cron health check: `options_agent/logs/cron_agent.log`

## All Commands

| Command | Description |
|---------|-------------|
| `./run.sh dashboard` | Start the Streamlit dashboard at http://localhost:8501 |
| `./run.sh agents` | Start both SPY + QQQ agents (daemon, background) |
| `./run.sh agent-spy` | Start only the SPY agent |
| `./run.sh agent-qqq` | Start only the QQQ agent |
| `./run.sh status` | Show running agent containers and recent logs |
| `./run.sh stop-agents` | Stop all agents |
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
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | Live agent paper trading, 5-min historical data |
| `ROBINHOOD_USERNAME`, `ROBINHOOD_PASSWORD`, `ROBINHOOD_TOTP_SECRET` | Live Robinhood trading (MCP agent) |
| `GEMINI_API_KEY` | Optional LLM confirmation of trades |
| `DRY_RUN=true` | Safety switch for Robinhood agent (default: on) |

The dashboard works without any credentials — it uses public market data via yfinance.

## Examples

```bash
# Start agents and check status
./run.sh agents
./run.sh status

# Backtest SPY over 1 year
./run.sh backtest -- --symbol SPY --period 1y --save

# Replay sweet spot strategy on QQQ, last 365 days
./run.sh replay -- --symbol QQQ --days 365

# Scan with custom choppiness threshold
./run.sh scan -- --max-chop 7 --min-stability 3

# Run tests
./run.sh test
```
