@echo off
REM Launches the 4 IBKR-paper sweet-spot daemons (SPY / QQQ / IWM / DIA).
REM Each daemon runs in its own minimized cmd window so you can independently
REM monitor / stop any one of them via Task Manager without killing the others.
REM
REM Fired daily by Windows Task Scheduler at 09:25 ET on weekdays.
REM
REM Prereqs (verified separately):
REM   - IB Gateway logged in (paper mode, port 4002)
REM   - options_agent/.env has DISCORD_WEBHOOK_URL + IBKR_HOST/PORT/CLIENT_ID_BASE
REM   - Python installed with ib_async, streamlit-autorefresh, etc. per requirements.txt

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set ROOT=c:\Users\Brat\OneDrive\Desktop\Algooooo\options-algo-trader\options_agent

cd /d "%ROOT%"

REM AGENT_SYMBOL scopes the per-daemon log file so 4 concurrent processes
REM don't clobber each other's writes.
start "SPY_agent" /MIN cmd /c "cd /d %ROOT% && set "AGENT_SYMBOL=SPY" && py scripts\run_sweet_spot_agent.py --daemon --broker ibkr --symbol SPY"
start "QQQ_agent" /MIN cmd /c "cd /d %ROOT% && set "AGENT_SYMBOL=QQQ" && py scripts\run_sweet_spot_agent.py --daemon --broker ibkr --symbol QQQ"
start "IWM_agent" /MIN cmd /c "cd /d %ROOT% && set "AGENT_SYMBOL=IWM" && py scripts\run_sweet_spot_agent.py --daemon --broker ibkr --symbol IWM"
start "DIA_agent" /MIN cmd /c "cd /d %ROOT% && set "AGENT_SYMBOL=DIA" && py scripts\run_sweet_spot_agent.py --daemon --broker ibkr --symbol DIA"

echo Launched 4 IBKR daemons. Check taskbar for minimized windows.
