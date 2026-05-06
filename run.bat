@echo off
setlocal

cd /d "%~dp0"

if not exist "options_agent\.env" (
    echo Creating .env from template...
    copy "options_agent\.env.example" "options_agent\.env" >nul
    echo.
    echo ==^> Edit options_agent\.env with your credentials before running the agent live.
    echo     The dashboard works without credentials (uses public market data).
    echo.
)

if "%1"=="" goto usage
if "%1"=="dashboard" goto dashboard
if "%1"=="agent" goto agent
if "%1"=="backtest" goto backtest
if "%1"=="replay" goto replay
if "%1"=="scan" goto scan
if "%1"=="test" goto test
if "%1"=="shell" goto shell
if "%1"=="down" goto down
if "%1"=="build" goto build
goto usage

:dashboard
docker-compose up --build dashboard
goto end

:agent
docker-compose --profile live up --build agent
goto end

:backtest
shift
docker-compose run --rm --no-deps dashboard python scripts/backtest.py %*
goto end

:replay
shift
docker-compose run --rm --no-deps dashboard python scripts/replay_sweet_spot.py %*
goto end

:scan
shift
docker-compose run --rm --no-deps dashboard python scripts/scan_sweet_spot_today.py %*
goto end

:test
docker-compose run --rm --no-deps dashboard pytest tests/ -v
goto end

:shell
docker-compose run --rm --no-deps dashboard bash
goto end

:down
docker-compose down
goto end

:build
docker-compose build
goto end

:usage
echo Usage: run.bat ^<command^>
echo.
echo Commands:
echo   dashboard    Start the Streamlit dashboard (http://localhost:8501)
echo   agent        Start the live sweet spot agent (daemon mode)
echo   backtest     Run backtest (pass extra args after the command)
echo   replay       Run replay sweet spot (pass extra args after the command)
echo   scan         Scan today's sweet spots
echo   test         Run the test suite
echo   shell        Open a shell in the container
echo   down         Stop all containers
echo   build        Rebuild the Docker image
echo.
echo Examples:
echo   run.bat dashboard
echo   run.bat backtest --symbol SPY --period 1y --save
echo   run.bat replay --days 365
echo   run.bat agent

:end
endlocal
